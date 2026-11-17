#!/usr/bin/env python3
"""sd87: install SystemUI by copying it off the card's boot partition.

Why SystemUI is the whole problem: boot ad81325c has 163 binder services and a
live system_server, but

  * `dumpsys user` shows user 0 stuck in `State: BOOTING`, and
  * the events buffer has `boot_progress_ams_ready` but no
    `uc_finish_user_boot` and no `boot_progress_enable_screen`.

Tracing it back in source: `ActivityManagerService.finishBooting()` returns
immediately while `mBootAnimationComplete` is false, and that flag is only set by
`bootAnimationComplete()`, whose only caller is
`WindowManagerService.performEnableScreen()`. That method refuses to enable the
screen until

    // Don't enable the screen until all existing windows have been drawn.
    if (!mForceDisplayEnabled)
        for (...) if (shouldWaitForSystemDecorWindowsOnBoot()) return;

and `DisplayContent.shouldWaitForSystemDecorWindowsOnBoot()` is waiting for the
system decor windows (the status bar) to be drawn. The status bar lives in
SystemUI -- which this image does not contain at all:

    system/priv-app/            -> no SystemUI
    installed-files.txt         -> no systemui entry
    SystemUI is only listed in build/make/target/product/handheld_system_ext.mk:29
    and this product inherits core_64_bit -> base -> common_full_tablet_wifionly

So the boot deadlocks in a loop: no SystemUI -> no decor window -> screen neve
enabled -> finishBooting never runs -> user never unlocks -> resolveHomeActivity()
filters Home out -> nothing ever draws.

The durable fix is to put SystemUI in PRODUCT_PACKAGES and rebuild the partitions
(device/anbernic/rg34xxsp/device.mk now does). That costs hours plus a whole-card
flash, so this patch is the fast path: the APK is placed at the root of the card's
FAT boot partition, first stage mounts that partition, copies it into the tmpfs
overlay already used for /system/priv-app, and PackageManagerService scans it as a
privileged system app.

The overlay+write half of this is already proven: boot ad81325c logged
`sysapp: wrote 16798/16798 bytes to /system/priv-app/FusedLocation/FusedLocation.apk
(source sha256 c9e2ee86…)`. Only the *source* changed - the card instead of the
unpacked ramfs, which did not resolve `system-overlay/...` entries.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

CODE = r'''// sd87: see patches/patch-system-app-overlay.py.
// Copy SystemUI.apk off the card's FAT boot partition into /system/priv-app.
static bool MountCardBoot(char* mountpoint, size_t len) {
    static const char* kCandidates[] = {
        "/dev/block/by-name/boot",
        "/dev/block/by-name/BOOT",
        "/dev/block/mmcblk0p1",
        "/dev/block/mmcblk1p1",
    };
    snprintf(mountpoint, len, "/mnt/dshboot");
    mkdir(mountpoint, 0755);
    for (const char* dev : kCandidates) {
        if (access(dev, F_OK) != 0) continue;
        int rc = mount(dev, mountpoint, "vfat", MS_RDONLY, "");
        pubnote("sysapp: mount %s -> %s rc=%d%s", dev, mountpoint, rc,
                rc ? "" : " (ok)");
        if (rc == 0) return true;
    }
    pubnote("sysapp: could not mount the card boot partition from any candidate");
    return false;
}

void PublishSystemApp(int /*dfd*/) {
    static const char kDir[] = "/system_ext/priv-app/SystemUI";
    static const char kPath[] = "/system_ext/priv-app/SystemUI/SystemUI.apk";
    char boot[64];

    if (!OverlayDir("/system_ext/priv-app", "/dev/sysapp_upper", "/dev/sysapp_work")) {
        pubnote("sysapp: overlay of /system_ext/priv-app failed");
        return;
    }
    chmod("/system_ext/priv-app", 0755);
    chmod("/dev/sysapp_upper", 0755);
    chmod("/dev/sysapp_work", 0755);

    if (!MountCardBoot(boot, sizeof(boot))) return;

    // Accept the APK at the root of the card or inside DEBUG/, so it can be
    // dropped next to Image/initrd.img without another layout decision.
    char src[128];
    const char* found = nullptr;
    static const char* kRel[] = {"/SystemUI.apk", "/DEBUG/SystemUI.apk"};
    for (const char* rel : kRel) {
        snprintf(src, sizeof(src), "%s%s", boot, rel);
        if (access(src, F_OK) == 0) {
            found = src;
            break;
        }
    }
    if (found == nullptr) {
        pubnote("sysapp: no SystemUI.apk on the card (%s/SystemUI.apk)", boot);
        umount(boot);
        return;
    }

    if (mkdir(kDir, 0755) != 0 && errno != EEXIST) {
        pubnote("sysapp: mkdir %s failed: %d (%s)", kDir, errno, strerror(errno));
        umount(boot);
        return;
    }
    chmod(kDir, 0755);

    int in = open(found, O_RDONLY | O_CLOEXEC);
    int out = in < 0 ? -1 : open(kPath, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (in < 0 || out < 0) {
        pubnote("sysapp: open failed in=%d out=%d errno=%d (%s)", in, out, errno,
                strerror(errno));
        if (in >= 0) close(in);
        if (out >= 0) close(out);
        umount(boot);
        return;
    }
    char buf[65536];
    ssize_t n;
    size_t total = 0;
    bool ok = true;
    while ((n = read(in, buf, sizeof(buf))) > 0) {
        ssize_t off = 0;
        while (off < n) {
            ssize_t w = write(out, buf + off, static_cast<size_t>(n - off));
            if (w <= 0) {
                ok = false;
                break;
            }
            off += w;
            total += static_cast<size_t>(w);
        }
        if (!ok) break;
    }
    fsync(out);
    close(out);
    close(in);
    umount(boot);
    if (ok) chmod(kPath, 0644);
    pubnote("sysapp: copied %s -> %s (%zu bytes, %s)", found, kPath, total,
            ok ? "ok" : "failed");
}

'''

FUNC_ANCHOR = "void PublishRamdiskFstab(DIR* ramdisk) {"
CALL_ANCHOR = "    PublishVendorGraphics(dfd);\n"

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "MountCardBoot" in text:
    print("already patched (SystemUI comes off the card), nothing to do")
    sys.exit(0)

if FUNC_ANCHOR not in text:
    sys.exit("anchor for the function insert not found")
if CALL_ANCHOR not in text:
    sys.exit("anchor for the call insert not found")

text = text.replace(FUNC_ANCHOR, CODE + FUNC_ANCHOR, 1)
text = text.replace(CALL_ANCHOR, CALL_ANCHOR + "    PublishSystemApp(dfd);\n", 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))