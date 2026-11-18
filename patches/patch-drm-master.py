#!/usr/bin/env python3
"""sd102: make drm_hwcomposer become DRM master.

Boot c6b24d47 finally got the whole graphics stack working -- the probe proves
gralloc sampling works in every direction, SystemUI and Launcher3 both run, and
SurfaceFlinger composites real layers:

    Layer StatusBar#0  720x32  fourcc/mod: AR24 / 0   (ARGB8888, linear)
    flips=35  frame-counter=24

...and yet the panel still shows the kernel console text, because every single
frame is rejected by DRM:

    E hwc-drm-two: Failed to apply the frame composition ret=-13
    E hwc-drm-display-compositor: Commit test failed for display 0, FIXME
    E HWComposer: presentAndGetReleaseFences: present failed for display 0: BadParameter (4)

-13 is EACCES. drmModeAtomicCommit() returns EACCES when the calling file is not
the DRM master, and `DrmDevice::Init()` only does `open(path, O_RDWR)` -- it
never calls drmSetMaster(). When the fbdev console (fb0, from
CONFIG_DRM_FBDEV_EMULATION) still owns the CRTC, the compositor's commits are
refused, so nothing it composes ever reaches the panel and the console text
stays. That is exactly the "black screen with code text" the user reports.

Fix: call drmSetMaster() immediately after open(). The kernel drops the fbdev
console's master when we take over, which also clears the console from the
panel.

This is a source patch to external/drm_hwcomposer, so the binary has to be
rebuilt and shipped in the vendor overlay:
    m hwcomposer.drm_minigbm
    tools/stage-mesa-overlay.sh        (copies it next to the mesa files)
    patches/patch-mesa-publish.py      (publishes it to /vendor/lib64/hw)
"""

import sys

PATH = "/root/android/lineage/external/drm_hwcomposer/drm/drmdevice.cpp"

ANCHOR = """  fd_.Set(open(path, O_RDWR));
  if (fd() < 0) {
    ALOGE("Failed to open dri- %s", strerror(-errno));
    return std::make_tuple(-ENODEV, 0);
  }
"""

ADD = ANCHOR + """
  // sd102: see patches/patch-drm-master.py. Become the DRM master, otherwise
  // drmModeAtomicCommit() returns -EACCES while the fbdev console owns the
  // CRTC and SurfaceFlinger's frames never reach the panel (boot c6b24d47:
  // "hwc-drm-two: Failed to apply the frame composition ret=-13").
  {
    int mret = drmSetMaster(fd());
    if (mret != 0)
      ALOGE("Failed to set DRM master on %s: %s", path, strerror(errno));
    else
      ALOGI("Became DRM master on %s", path);
  }
"""

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "sd102" in text:
    print("already patched (composer takes DRM master), nothing to do")
    sys.exit(0)

if ANCHOR not in text:
    sys.exit("anchor for the open() block not found in " + PATH)

text = text.replace(ANCHOR, ADD, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH)
