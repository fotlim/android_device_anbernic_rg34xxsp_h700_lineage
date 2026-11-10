#!/usr/bin/env python3
"""
sd39: give card nodes back to mode-setting drivers only.

Linux hands a DRM_MINOR_PRIMARY (a /dev/dri/cardN node) to every device with
GEM, so on this board panfrost -- render-only, no DRIVER_MODESET -- registered
first and became card0, while sun4i-drm (the display controller) became card1.

That is fine for X and for Mesa, and fatal for AOSP's hardware composer:
drm_hwcomposer opens /dev/dri/card0 and its first ioctl is

    drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1)

whose handler in drm_ioctl.c starts with

    if (!drm_core_check_feature(dev, DRIVER_MODESET))
            return -EOPNOTSUPP;

and the composer treats that as fatal (drmdevice.cpp:133). The trace from the
machine says exactly this:

    panfrost 1800000.gpu: [drm:drm_open_helper] comm="android.hardwar" pid=241, minor=0
    [drm:drm_ioctl] comm="android.hardwar" pid=241, dev=0xe200, auth=1, DRM_IOCTL_SET_CLIENT_CAP
    [drm:drm_ioctl] comm="android.hardwar" pid=241, dev=0xe200, auth=1, ret=-95

Two edits, both in drivers/gpu/drm/drm_drv.c:

  1. allocate DRM_MINOR_PRIMARY only for DRIVER_MODESET devices, which is what
     every kernel before the "card node for everyone" change did;
  2. the "Initialized ... on minor %d" line falls back to dev->accel->index when
     there is no primary, which would dereference NULL for panfrost once (1)
     lands -- fall back to the render minor instead.
"""
import sys

SRC = "/tmp/lk-rocknix/drivers/gpu/drm/drm_drv.c"

OLD_ALLOC = """		ret = drm_minor_alloc(dev, DRM_MINOR_PRIMARY);
		if (ret)
			goto err;
"""

NEW_ALLOC = """		/*
		 * Android opens /dev/dri/card0 for the display and expects a
		 * KMS device there; a render-only GPU on card0 makes its
		 * composer fail on the first drmSetClientCap(). Card nodes are
		 * for mode-setting drivers, which is what every kernel before
		 * the "a card node for every GEM device" change did.
		 */
		if (drm_core_check_feature(dev, DRIVER_MODESET)) {
			ret = drm_minor_alloc(dev, DRM_MINOR_PRIMARY);
			if (ret)
				goto err;
		}
"""

OLD_INFO = """		 dev->primary ? dev->primary->index : dev->accel->index);"""
NEW_INFO = """		 dev->primary ? dev->primary->index :
		 (dev->render ? dev->render->index : dev->accel->index));"""

with open(SRC, encoding="utf-8") as f:
    text = f.read()

if "Card nodes are" in text and "dev->render ? dev->render->index" in text:
    print("already patched, nothing to do")
    sys.exit(0)

for old, new, name in ((OLD_ALLOC, NEW_ALLOC, "alloc"), (OLD_INFO, NEW_INFO, "info")):
    if text.count(old) != 1:
        sys.exit("anchor %s appears %d times, refusing" % (name, text.count(old)))
    text = text.replace(old, new, 1)

with open(SRC, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", SRC)