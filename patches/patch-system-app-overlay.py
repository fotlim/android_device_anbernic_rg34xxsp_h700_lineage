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

static bool CopyAppFromCard(const char* boot, const char* rel, const char* dir,
                            const char* path) {
    char src[192];
    snprintf(src, sizeof(src), "%s%s", boot, rel);
    if (access(src, F_OK) != 0) {
        pubnote("sysapp: %s missing (errno=%d)", src, errno);
        return false;
    }
    if (mkdir(dir, 0755) != 0 && errno != EEXIST) {
        pubnote("sysapp: mkdir %s failed: %d (%s)", dir, errno, strerror(errno));
        return false;
    }
    chmod(dir, 0755);

    int in = open(src, O_RDONLY | O_CLOEXEC);
    if (in < 0) {
        pubnote("sysapp: open %s failed: %d (%s)", src, errno, strerror(errno));
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

void PublishSystemApp(int /*dfd*/) {
    if (!OverlayDir("/system/priv-app", "/dev/sysapp_upper", "/dev/sysapp_work")) {
        pubnote("sysapp: overlay of /system/priv-app failed");
        return;
    }
    if (!OverlayDir("/system_ext/priv-app", "/dev/sysext_upper", "/dev/sysext_work")) {
        pubnote("sysapp: overlay of /system_ext/priv-app failed");
        return;
    }
    chmod("/system/priv-app", 0755);
    chmod("/dev/sysapp_upper", 0755);
    chmod("/dev/sysapp_work", 0755);
    chmod("/system_ext/priv-app", 0755);
    chmod("/dev/sysext_upper", 0755);
    chmod("/dev/sysext_work", 0755);

    char boot[64];

    // sd89: /dshsysapp first.  debug-init stages the APKs there while the card
    // is still its own mount; mounting the card from here comes back EBUSY
    // (boot b5c8841d: `mount ... rc=-1 errno=16`) because the kmsg follower
    // keeps a second mount namespace holding the FAT superblock.
    bool have_fused = CopyAppFromCard("/dshsysapp", "/FusedLocation.apk",
                                      "/system/priv-app/FusedLocation",
                                      "/system/priv-app/FusedLocation/FusedLocation.apk");
    bool have_sysui = CopyAppFromCard("/dshsysapp", "/SystemUI.apk",
                                      "/system_ext/priv-app/SystemUI",
                                      "/system_ext/priv-app/SystemUI/SystemUI.apk");
    if (have_fused && have_sysui) return;

    // Fallback for a build without debug-init staging (or a missing APK there).
    if (!MountCardBoot(boot, sizeof(boot))) return;
    if (!have_fused) {
        CopyAppFromCard(boot, "/FusedLocation.apk", "/system/priv-app/FusedLocation",
                        "/system/priv-app/FusedLocation/FusedLocation.apk");
    }
    if (!have_sysui) {
        CopyAppFromCard(boot, "/SystemUI.apk", "/system_ext/priv-app/SystemUI",
                        "/system_ext/priv-app/SystemUI/SystemUI.apk");
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
