#!/usr/bin/env python3
"""sd88: install the two missing privileged apps by copying them off the card.

History, because this patch has now been wrong twice:

  sd86  the apps were hex-embedded in the init binary.  Worked for FusedLocation
        (boot ad81325c: `sysapp: wrote 16798/16798 bytes ... sha256 c9e2ee86`),
        but SystemUI is 11.9 MiB and cannot be embedded in a 700 KiB init.
  sd87  switched the snapshot app to the card's FAT boot partition so the APK
        could live on the card instead.  The copy never ran: boot da17cd8a
        logged `sysapp: could not mount the card boot partition from any
        candidate` with no per-candidate line at all, i.e. every
        `access("/dev/block/...")` returned ENOENT.

The sd87 failure is a first-stage timing fact, not a filesystem one: this code
runs before ueventd has cold-booted the block devices, so /dev/block/by-name and
/dev/block/mmcblk0p1 do not exist yet -- they only appear later, which is why
every mountinfo dump taken from a running system happily shows
`/dev/mmcblk0p1 /mnt vfat` and `/sys/class/block/mmcblk0p1` with PARTNAME=boot.

sd88 therefore builds the node itself from `/sys/class/block/<name>/dev` and
mknods it under /dev, then mounts it read-only.  The GPT layout is known from
dmesg-da17cd8a (`mmcblk0: p1 p2 p3 p4 p5`, p1 = 64 MiB, PARTNAME=boot, vfat) and
`vfat` is in /proc/filesystems, so no module loading is needed.

boot b5c8841d then proved the mknod half right and the mount half still wrong:

    sysapp: candidate /dev/block/mmcblk0p1 access=-1
    sysapp: node /dev/dsh_mmcblk0p1 = 179:1 (mmcblk0p1), ok
    sysapp: mount /dev/dsh_mmcblk0p1 -> /mnt/dshboot rc=-1 errno=16 (Device or resource busy)
    sysapp: candidate /dev/block/mmcblk1p1 access=-1
    sysapp: mmcblk1p1: no /sys/class/block/mmcblk1p1/dev (2)
    sysapp: no mountable boot partition (tried mmcblk0p1, mmcblk1p1)

EBUSY because the kmsg follower child in tools/debug-init/debug-init.c has its
own mount namespace, so the FAT superblock and its exclusive claim on
mmcblk0p1 outlive the MNT_DETACH the handover does.  sd89 stops depending on
this mount: debug-init copies both APKs into /dshsysapp/ in the RAM rootfs
while /mnt is still its own, and this code reads them from there.  The mount
path below is kept only as a fallback for a build without that staging.

Boot 2d61c94a closed that gap and opened a new one: debug-init's staging worked
(`dshsysapp: staged /mnt/SystemUI.apk -> /dshsysapp/SystemUI.apk (11932626
bytes)`, `2 app(s) staged`), yet the first stage still logged
`sysapp: /dshsysapp/SystemUI.apk missing (errno=2)`.  Reason: this code runs
*after* `SwitchRoot("/first_stage_ramdisk")`, so "/" is the system image and the
ramdisk -- where debug-init wrote -- survives only as the `DIR*` opened before
the switch.  sd90 opens the staged APKs with `openat(dfd, ...)` instead of by
path, which is exactly how PublishRamdiskFstab and the vendor-overlay copies
already reach the ramdisk.

sd91: with both APKs finally in place (boot 2cc41b35: two `sysapp: copied ...
ok` lines, and the location crash gone), PackageManagerService then refused
SystemUI -- `Signature|privileged permissions not in privapp-permissions
whitelist`, six system_server retries.  The allowlist
`system_ext/etc/permissions/com.android.systemui.xml` exists in the build but
the card's system_ext image predates SystemUI, so this patch publishes it too,
through an overlay of /system_ext/etc.  patches/patch-privapp-log.py adds the
matching `ro.control_privapp_permissions=log` so a gap in that XML cannot abort
the boot again.

Both apps are carried on the card now, because they both have to be there:

  * SystemUI      -> /system_ext/priv-app/SystemUI/SystemUI.apk  (handheld_system_ext.mk)
  * FusedLocation -> /system/priv-app/FusedLocation/FusedLocation.apk (handheld_system.mk)

SystemUI is what unblocks `DisplayContent.shouldWaitForSystemDecorWindowsOnBoot()`
(no status bar -> `performEnableScreen()` returns early -> `bootAnimationComplete()`
never runs -> user 0 never leaves STATE_BOOTING).  FusedLocation is what unblocks
`LocationManagerService.initializeProvidersLocked()` at phase 600, which is the
crash loop visible in boot da17cd8a's logcat-late: system_server dies ten times
in a row with `Unable to find a direct boot aware fused location provider`.
"""

import os
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

START = "// sd88: see patches/patch-system-app-overlay.py."
OLD_START = "// sd87: see patches/patch-system-app-overlay.py."

CODE = r'''// sd88: see patches/patch-system-app-overlay.py.
// Copy the two privileged apps off the card's FAT boot partition into the tmpfs
// overlays of /system/priv-app and /system_ext/priv-app.  The block device for
// that partition does not exist yet at this point in first stage (ueventd has
// not cold-booted /dev), so the node is built from sysfs and mknod'ed by hand.
static int DevNodeForBlock(const char* name, char* out, size_t len) {
    char sysfs[128];
    snprintf(sysfs, sizeof(sysfs), "/sys/class/block/%s/dev", name);
    int fd = open(sysfs, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        pubnote("sysapp: %s: no %s (%d)", name, sysfs, errno);
        return -1;
    }
    char buf[32] = {};
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) {
        pubnote("sysapp: %s: %s unreadable", name, sysfs);
        return -1;
    }
    unsigned int maj = 0, min = 0;
    if (sscanf(buf, "%u:%u", &maj, &min) != 2) {
        pubnote("sysapp: %s: unexpected sysfs contents '%s'", name, buf);
        return -1;
    }
    snprintf(out, len, "/dev/dsh_%s", name);
    unlink(out);
    if (mknod(out, S_IFBLK | 0600, makedev(maj, min)) != 0) {
        pubnote("sysapp: mknod %s (%u:%u) failed: %d (%s)", out, maj, min, errno,
                strerror(errno));
        return -1;
    }
    pubnote("sysapp: node %s = %u:%u (%s), ok", out, maj, min, name);
    return 0;
}

static bool MountCardBoot(char* mountpoint, size_t len) {
    static const char* kCandidates[] = {"mmcblk0p1", "mmcblk1p1"};
    static const char kOpts[] = "codepage=437,iocharset=iso8859-1,shortname=mixed";
    snprintf(mountpoint, len, "/mnt/dshboot");
    mkdir(mountpoint, 0755);
    for (const char* name : kCandidates) {
        char dev[64];
        snprintf(dev, sizeof(dev), "/dev/block/%s", name);
        int have = access(dev, F_OK);
        pubnote("sysapp: candidate %s access=%d", dev, have);
        if (have != 0) {
            if (DevNodeForBlock(name, dev, sizeof(dev)) != 0) continue;
        }
        int rc = mount(dev, mountpoint, "vfat", MS_RDONLY, kOpts);
        pubnote("sysapp: mount %s -> %s rc=%d errno=%d (%s)", dev, mountpoint, rc,
                rc ? errno : 0, rc ? strerror(errno) : "ok");
        if (rc == 0) return true;
    }
    pubnote("sysapp: no mountable boot partition (tried mmcblk0p1, mmcblk1p1)");
    return false;
}

// src is relative to dirfd when dirfd >= 0, and an absolute path otherwise.
//
// The relative form exists because of SwitchRoot(): by the time this code runs,
// "/" is already the system image and the ramdisk that debug-init wrote into is
// reachable only through the DIR* opened before the switch -- that is the same
// `dfd` PublishRamdiskFstab uses. Boot 2d61c94a proved the difference: the
// staging itself had worked,
//   dshsysapp: staged /mnt/SystemUI.apk -> /dshsysapp/SystemUI.apk (11932626 bytes)
//   dshsysapp: staged /mnt/FusedLocation.apk -> /dshsysapp/FusedLocation.apk (16798 bytes)
// but the first stage then logged
//   sysapp: /dshsysapp/FusedLocation.apk missing (errno=2)
//   sysapp: /dshsysapp/SystemUI.apk missing (errno=2)
// because that path does not exist in the new root any more.
static bool CopyAppAt(int dirfd, const char* src, const char* dir, const char* path) {
    if (dirfd >= 0 ? faccessat(dirfd, src, F_OK, 0) != 0 : access(src, F_OK) != 0) {
        pubnote("sysapp: %s missing (dirfd=%d errno=%d)", src, dirfd, errno);
        return false;
    }
    if (mkdir(dir, 0755) != 0 && errno != EEXIST) {
        pubnote("sysapp: mkdir %s failed: %d (%s)", dir, errno, strerror(errno));
        return false;
    }
    chmod(dir, 0755);

    int in = dirfd >= 0 ? openat(dirfd, src, O_RDONLY | O_CLOEXEC)
                        : open(src, O_RDONLY | O_CLOEXEC);
    if (in < 0) {
        pubnote("sysapp: open %s failed (dirfd=%d): %d (%s)", src, dirfd, errno,
                strerror(errno));
        return false;
    }
    int out = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (out < 0) {
        pubnote("sysapp: open %s failed: %d (%s)", path, errno, strerror(errno));
        close(in);
        return false;
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
    if (ok) chmod(path, 0644);
    pubnote("sysapp: copied %s -> %s (%zu bytes, %s)", src, path, total,
            ok ? "ok" : "failed");
    return ok;
}

void PublishSystemApp(int dfd) {
    if (!OverlayDir("/system/priv-app", "/dev/sysapp_upper", "/dev/sysapp_work")) {
        pubnote("sysapp: overlay of /system/priv-app failed");
        return;
    }
    if (!OverlayDir("/system_ext/priv-app", "/dev/sysext_upper", "/dev/sysext_work")) {
        pubnote("sysapp: overlay of /system_ext/priv-app failed");
        return;
    }
    // sd91: the privapp allowlist has to land in /system_ext/etc/permissions too.
    if (!OverlayDir("/system_ext/etc", "/dev/sysextetc_upper", "/dev/sysextetc_work")) {
        pubnote("sysapp: overlay of /system_ext/etc failed");
        return;
    }
    chmod("/system/priv-app", 0755);
    chmod("/dev/sysapp_upper", 0755);
    chmod("/dev/sysapp_work", 0755);
    chmod("/system_ext/priv-app", 0755);
    chmod("/dev/sysext_upper", 0755);
    chmod("/dev/sysext_work", 0755);
    chmod("/system_ext/etc", 0755);
    chmod("/dev/sysextetc_upper", 0755);
    chmod("/dev/sysextetc_work", 0755);

    char boot[64];

    // sd90: read the staged APKs through `dfd`, the ramdisk handle, not through
    // a path.  debug-init put them there before the handover; SwitchRoot()
    // then removed that path from the namespace.
    bool have_fused = CopyAppAt(dfd, "dshsysapp/FusedLocation.apk",
                                "/system/priv-app/FusedLocation",
                                "/system/priv-app/FusedLocation/FusedLocation.apk");
    bool have_sysui = CopyAppAt(dfd, "dshsysapp/SystemUI.apk",
                                "/system_ext/priv-app/SystemUI",
                                "/system_ext/priv-app/SystemUI/SystemUI.apk");
    // sd91: without this, PackageManagerService refuses SystemUI outright with
    // "Signature|privileged permissions not in privapp-permissions whitelist".
    // The file is in the build (system_ext/etc/permissions/com.android.systemui.xml,
    // 4,434 B) but the card's system_ext image predates SystemUI, so it is not
    // on the device.
    bool have_allowlist = CopyAppAt(dfd, "dshsysapp/com.android.systemui.xml",
                                    "/system_ext/etc/permissions",
                                    "/system_ext/etc/permissions/com.android.systemui.xml");
    // sd95: the gralloc-import probe. It cannot be a vendor binary (AHardwareBuffer
    // lives in libandroid, which has no vendor variant here), so it lives in
    // /system/bin and needs an overlay of its own -- /system is read-only.
    bool have_probe = false;
    if (OverlayDir("/system/bin", "/dev/sysbin_upper", "/dev/sysbin_work")) {
        chmod("/system/bin", 0755);
        chmod("/dev/sysbin_upper", 0755);
        chmod("/dev/sysbin_work", 0755);
        have_probe = CopyAppAt(dfd, "dshsysapp/dsh-glprobe2", "/system/bin",
                               "/system/bin/dsh-glprobe2");
        // CopyAppAt leaves 0644 (files); this one has to be executable.
        if (have_probe) chmod("/system/bin/dsh-glprobe2", 0755);
        // sd118: joystick-as-mouse daemon
        bool have_joy = CopyAppAt(dfd, "dshsysapp/dsh-joymouse", "/system/bin",
                                  "/system/bin/dsh-joymouse");
        if (have_joy) chmod("/system/bin/dsh-joymouse", 0755);
    } else {
        pubnote("sysapp: overlay of /system/bin failed, no glprobe2/joymouse");
    }
    // sd111: a directBootAware HOME.  Nothing else resolves as home while user 0
    // is still STATE_BOOTING (PackageManagerService.updateFlags() only matches
    // MATCH_DIRECT_BOOT_AWARE until the user is unlocking/unlocked), so
    // resolveHomeActivity() returns null, no activity ever goes idle, and
    // ActivityStackSupervisor.checkFinishBootingLocked() -- the only caller of
    // finishBooting() -- never runs.  That single missing idle is what leaves
    // sys.boot_completed unset, the screen disabled and user 0 stuck in BOOTING.
    // See patches/fallback-home/ and dev log §78.
    bool have_fallback = false;
    if (OverlayDir("/system/app", "/dev/sysapp2_upper", "/dev/sysapp2_work")) {
        chmod("/system/app", 0755);
        chmod("/dev/sysapp2_upper", 0755);
        chmod("/dev/sysapp2_work", 0755);
        have_fallback = CopyAppAt(dfd, "dshsysapp/DshFallbackHome.apk",
                                  "/system/app/DshFallbackHome",
                                  "/system/app/DshFallbackHome/DshFallbackHome.apk");
    } else {
        pubnote("sysapp: overlay of /system/app failed, no fallback home");
    }
    // sd116: the rebuilt Launcher3 (TrebuchetQuickStep) with the appwidget-NPE
    // guard. Its package is on /system_ext, whose image is fixed on the card, so
    // it has to be overlaid like SystemUI.
    bool have_treb = CopyAppAt(dfd, "dshsysapp/TrebuchetQuickStep.apk",
                               "/system_ext/priv-app/TrebuchetQuickStep",
                               "/system_ext/priv-app/TrebuchetQuickStep/TrebuchetQuickStep.apk");
    // sd127: remap the gamepad (A=enter, B=back, start=home) by overlaying the
    // keylayout, so the no-touch UI is fully navigable in any focused app.
    if (OverlayDir("/vendor/usr/keylayout", "/dev/keylayout_upper", "/dev/keylayout_work")) {
        chmod("/vendor/usr/keylayout", 0755);
        chmod("/dev/keylayout_upper", 0755);
        chmod("/dev/keylayout_work", 0755);
        CopyAppAt(dfd, "dshsysapp/Vendor_484b_Product_14df.kl",
                  "/vendor/usr/keylayout",
                  "/vendor/usr/keylayout/Vendor_484b_Product_14df.kl");
    } else {
        pubnote("sysapp: overlay of /vendor/usr/keylayout failed");
    }
    if (have_fused && have_sysui && have_allowlist && have_probe && have_fallback) return;

    // Fallback: the card itself.  Normally EBUSY (errno 16) here, because the
    // kmsg follower keeps a second mount namespace holding the FAT superblock.
    if (!MountCardBoot(boot, sizeof(boot))) return;
    if (!have_fused) {
        CopyAppAt(-1, "/mnt/dshboot/FusedLocation.apk", "/system/priv-app/FusedLocation",
                  "/system/priv-app/FusedLocation/FusedLocation.apk");
    }
    if (!have_sysui) {
        CopyAppAt(-1, "/mnt/dshboot/SystemUI.apk", "/system_ext/priv-app/SystemUI",
                  "/system_ext/priv-app/SystemUI/SystemUI.apk");
    }
    if (!have_allowlist) {
        CopyAppAt(-1, "/mnt/dshboot/com.android.systemui.xml", "/system_ext/etc/permissions",
                  "/system_ext/etc/permissions/com.android.systemui.xml");
    }
    if (!have_probe) {
        CopyAppAt(-1, "/mnt/dshboot/dsh-glprobe2", "/system/bin",
                  "/system/bin/dsh-glprobe2");
        chmod("/system/bin/dsh-glprobe2", 0755);
    }
    if (!have_fallback) {
        CopyAppAt(-1, "/mnt/dshboot/DshFallbackHome.apk", "/system/app/DshFallbackHome",
                  "/system/app/DshFallbackHome/DshFallbackHome.apk");
    }
    if (!have_treb) {
        CopyAppAt(-1, "/mnt/dshboot/TrebuchetQuickStep.apk",
                  "/system_ext/priv-app/TrebuchetQuickStep",
                  "/system_ext/priv-app/TrebuchetQuickStep/TrebuchetQuickStep.apk");
    }
    umount(boot);
}

// sd88-end
'''

FUNC_ANCHOR = "void PublishRamdiskFstab(DIR* ramdisk) {"
CALL_ANCHOR = "    PublishVendorGraphics(dfd);\n"


def strip_previous(text):
    """Remove a previously applied block.

    The block carries its own end marker, because the "stop at the next
    top-level definition" heuristic is self-matching: the first
    `\\nstatic ` after the start marker is this block's own DevNodeForBlock, so
    the strip removes nothing and the block is inserted a second time.  That is
    what produced "redefinition of 'MountCardBoot'" on this tree.

    Legacy (sd87) blocks have no end marker; they end at the anchor, which was
    correct for them and only wrong once a later block sat in between.
    """
    if START in text:
        start = text.index(START)
        end_marker = "// sd88-end"
        stop = text.index(end_marker, start) + len(end_marker)
        return text[:start] + text[stop:].lstrip("\n"), start, START
    if OLD_START in text:
        start = text.index(OLD_START)
        stop = text.index(FUNC_ANCHOR)
        return text[:start] + text[stop:], start, OLD_START
    return text, None, None


with open(PATH, encoding="utf-8") as f:
    text = f.read()

text, hint, removed = strip_previous(text)
if removed:
    print("removed the previous block (%s)" % removed.split(':')[0])

if FUNC_ANCHOR not in text:
    sys.exit("anchor for the function insert not found")
if CALL_ANCHOR not in text:
    sys.exit("anchor for the call insert not found")
if "    PublishSystemApp(dfd);\n" not in text:
    text = text.replace(CALL_ANCHOR, CALL_ANCHOR + "    PublishSystemApp(dfd);\n", 1)

# Put the block back where it was, not at the anchor.
#
# Every patch inserts itself immediately before PublishRamdiskFstab, so a chain
# replay puts this block first and the live-snapshot block (applied later, same
# anchor) directly behind it: [sysapp][snapshot][anchor].  A plain re-insert at
# the anchor lands it *after* snapshot instead -- [snapshot][sysapp][anchor] --
# and since the live-snapshot patch is insert-once, it never moves back. That
# made tools/verify-patch-chain.sh report a difference after every incremental
# build even though the compiled binary was byte-identical. Re-inserting at the
# stripped position keeps both orders the same.
BLOCK = CODE + "\n"
if hint is not None:
    text = text[:hint] + BLOCK + text[hint:]
else:
    text = text.replace(FUNC_ANCHOR, BLOCK + FUNC_ANCHOR, 1)

# makedev() lives in <sys/sysmacros.h>; add it next to the other includes.
if "#include <sys/sysmacros.h>" not in text:
    anchor = "#include <sys/mount.h>"
    if anchor not in text:
        sys.exit("could not find the include anchor for <sys/sysmacros.h>")
    text = text.replace(anchor, anchor + "\n#include <sys/sysmacros.h>", 1)
    print("added <sys/sysmacros.h>")

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))
if not os.path.exists(PATH):
    sys.exit("write did not land")
