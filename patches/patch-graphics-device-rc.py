#!/usr/bin/env python3
"""
sd39: teach first-stage init one more thing to publish -- which DRM node the
hardware composer should open.

Why: AOSP's drm_hwcomposer picks its card with

    property_get("hwc.drm.device", path, "/dev/dri/card0")
    (external/drm_hwcomposer/drm/resourcemanager.cpp)

and the first thing it does on that fd is

    drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1)
    (external/drm_hwcomposer/drm/drmdevice.cpp)

drm_setclientcap() answers -EOPNOTSUPP for any cap on a device without
DRIVER_MODESET. On this board panfrost (render-only) registers first and gets
minor 0, so card0 is the GPU, the cap comes back -95, the composer exits with
status 1, SurfaceFlinger is killed with it, and init reboots the machine after
zygote's fourth death. sd39 also fixes the numbering in the kernel; this file
says out loud which node is the display, so the next boot's log answers the
question without a bisect.
"""
import sys

SRC = "/root/android/lineage/system/core/init/first_stage_init.cpp"

RC = r'''
/*
 * The card the hardware composer opens.
 *
 * See external/drm_hwcomposer/drm/resourcemanager.cpp: the path comes from the
 * hwc.drm.device property and defaults to /dev/dri/card0. Until sd39 card0 was
 * the GPU's render-only node, where drmSetClientCap() answers -EOPNOTSUPP and
 * the composer gives up. The kernel now gives card nodes to mode-setting
 * drivers only; this property keeps the intent readable in the log either way.
 */
const char kGraphicsRc[] =
    "# published by first-stage init (sd39)\n"
    "# The composer opened panfrost's card and died on DRM_CLIENT_CAP_UNIVERSAL_PLANES.\n"
    "# The display controller is the only card node on this board now.\n"
    "on post-fs-data\n"
    "    setprop hwc.drm.device /dev/dri/card0\n";

void PublishGraphicsRc() {
    const char* path = "/vendor/etc/init/97-graphics-device.rc";
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        pubnote("gfx: open %s failed: %d (%s)", path, errno, strerror(errno));
        return;
    }
    size_t len = strlen(kGraphicsRc);
    ssize_t written = write(fd, kGraphicsRc, len);
    fsync(fd);
    close(fd);
    pubnote("gfx: wrote %zd/%zu bytes to %s", written, len, path);
}

'''

ANCHOR_FUNC = "void PublishRamdiskFstab(DIR* ramdisk) {"
ANCHOR_CALL = "    if (published) PublishLogRc();\n"

with open(SRC, encoding="utf-8") as f:
    text = f.read()

if "kGraphicsRc" in text:
    print("already patched, nothing to do")
    sys.exit(0)

if ANCHOR_FUNC not in text:
    sys.exit("anchor for function insert not found")
if ANCHOR_CALL not in text:
    sys.exit("anchor for call insert not found")

text = text.replace(ANCHOR_FUNC, RC.lstrip("\n") + ANCHOR_FUNC, 1)
text = text.replace(ANCHOR_CALL, ANCHOR_CALL + "    if (published) PublishGraphicsRc();\n", 1)

with open(SRC, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", SRC)