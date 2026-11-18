#!/usr/bin/env python3
"""sd97: publish mesa + panfrost into /vendor, the fast route.

Why: boot bbb58a26 closed the SwiftShader question. Its own probe showed that
rendering *into* a minigbm buffer works and reads back correctly, but sampling
one reads back all black, and sampling a CPU-written one segfaults -- and
SurfaceFlinger samples every layer every frame. So `ro.hardware.egl=swiftshader`
cannot ever draw the UI, no matter how well the rest of the stack works.

The replacement is already in the tree: external/mesa3d is mesa 20.3.4 with
src/gallium/drivers/panfrost, and its Android.mk builds libGLES_mesa.so plus
gallium_dri.so as soon as BOARD_GPU_DRIVERS is non-empty (see
device/anbernic/rg34xxsp/BoardConfig.mk). mesa 20.3 wants libdrm >= 2.4.89 and
this tree has 2.4.99, so the "must be >= 2.4.109 first" note in HANDOVER only
ever applied to mesa 23.

What this patch does: the vendor image on the card is stale (built before mesa
existed), so the libraries cannot simply be installed into it. They travel in
the initrd's vendor overlay instead -- the same mechanism that already ships
gralloc.minigbm, the composer 2.1 service and the memtrack stub -- and this code
copies them into three overlays:

    /vendor/lib64            libglapi.so   (mesa's own, needed by both)
    /vendor/lib64/egl        libEGL_swiftshader.so / libGLESv2_swiftshader.so
    /vendor/lib64/dri        panfrost_dri.so  (== gallium_dri.so, renamed)

The egl files are mesa's libGLES_mesa.so shipped *under the swiftshader names*
on purpose. The EGL loader picks its driver by dlopening
/vendor/lib64/egl/libEGL_<ro.hardware.egl>.so, and ro.hardware.egl lives in
/vendor/build.prop = "swiftshader"; ro.* properties are write-once, so
overriding the file is more reliable than fighting the property. It also
displaces the 16 MB SwiftShader, which is the point.

Cost on the card: gallium_dri.so is 9.6 MB raw but 3.3 MB gzipped, so the initrd
goes from 1.5 MB to about 4.9 MB. The boot FAT partition has 12 MB free.

If panfrost works, the durable fix is to rebuild the vendor image with mesa in
it (docs/ISSUES.md I-图形-03) and drop this patch entirely.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

MARKER = "// sd97: publish mesa"
END = "// sd97-end"

CODE = r'''// sd97: publish mesa + panfrost into /vendor (patches/patch-mesa-publish.py).
// Inserted right after the sd88 block so CopyAppAt()/OverlayDir() are already
// declared. Everything comes out of the initrd's vendor-overlay/, i.e. through
// the ramdisk handle dfd -- /vendor itself is read-only and stale.
void PublishMesa(int dfd) {
    // /vendor/lib64 first: the overlay makes the directory writable, which is
    // what lets us create /vendor/lib64/dri inside the upper layer and then
    // mount a second overlay on top of it.
    if (!OverlayDir("/vendor/lib64", "/dev/vg_root_upper", "/dev/vg_root_work")) {
        pubnote("mesa: overlay of /vendor/lib64 failed");
        return;
    }
    chmod("/vendor/lib64", 0755);
    chmod("/dev/vg_root_upper", 0755);
    chmod("/dev/vg_root_work", 0755);

    // The stale vendor image has no dri/ directory at all.
    if (mkdir("/vendor/lib64/dri", 0755) != 0 && errno != EEXIST) {
        pubnote("mesa: mkdir /vendor/lib64/dri failed: %d (%s)", errno,
                strerror(errno));
    }

    if (!OverlayDir("/vendor/lib64/egl", "/dev/vg_egl_upper", "/dev/vg_egl_work")) {
        pubnote("mesa: overlay of /vendor/lib64/egl failed");
        return;
    }
    if (!OverlayDir("/vendor/lib64/dri", "/dev/vg_dri_upper", "/dev/vg_dri_work")) {
        pubnote("mesa: overlay of /vendor/lib64/dri failed");
        return;
    }
    chmod("/vendor/lib64/egl", 0755);
    chmod("/dev/vg_egl_upper", 0755);
    chmod("/dev/vg_egl_work", 0755);
    chmod("/vendor/lib64/dri", 0755);
    chmod("/dev/vg_dri_upper", 0755);
    chmod("/dev/vg_dri_work", 0755);

    CopyAppAt(dfd, "vendor-overlay/lib64/libglapi.so", "/vendor/lib64",
              "/vendor/lib64/libglapi.so");
    // Mesa's libGLES_mesa.so under the swiftshader names: the loader looks up
    // libEGL_<ro.hardware.egl>.so and ro.hardware.egl is "swiftshader".
    CopyAppAt(dfd, "vendor-overlay/lib64/egl/libEGL_swiftshader.so", "/vendor/lib64/egl",
              "/vendor/lib64/egl/libEGL_swiftshader.so");
    CopyAppAt(dfd, "vendor-overlay/lib64/egl/libGLESv2_swiftshader.so", "/vendor/lib64/egl",
              "/vendor/lib64/egl/libGLESv2_swiftshader.so");
    // gallium_dri.so renamed to the driver name mesa will ask for.
    CopyAppAt(dfd, "vendor-overlay/lib64/dri/panfrost_dri.so", "/vendor/lib64/dri",
              "/vendor/lib64/dri/panfrost_dri.so");
}

// sd97-end

'''

CALL_ANCHOR = "    PublishSystemApp(dfd);\n"


def strip(text):
    if MARKER not in text:
        return text, False
    start = text.index(MARKER)
    stop = text.index(END, start) + len(END)
    return text[:start] + text[stop:].lstrip("\n"), True


with open(PATH, encoding="utf-8") as f:
    text = f.read()

text, removed = strip(text)
if removed:
    print("removed the previous block")

if CALL_ANCHOR not in text:
    sys.exit("anchor for the call insert not found")
if "    PublishMesa(dfd);\n" not in text:
    text = text.replace(CALL_ANCHOR, CALL_ANCHOR + "    PublishMesa(dfd);\n", 1)

# Insert the function after the sd88 block's end sentinel, so CopyAppAt is in
# scope; fall back to the PublishRamdiskFstab anchor if that block is missing.
ANCHOR88 = "// sd88-end\n"
ANCHOR_FN = "void PublishRamdiskFstab(DIR* ramdisk) {"
if ANCHOR88 in text:
    text = text.replace(ANCHOR88, ANCHOR88 + "\n" + CODE, 1)
elif ANCHOR_FN in text:
    text = text.replace(ANCHOR_FN, CODE + ANCHOR_FN, 1)
else:
    sys.exit("no anchor for the function insert")

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))
