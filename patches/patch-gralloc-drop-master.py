#!/usr/bin/env python3
"""sd106: minigbm must not hold the DRM master.

Boot bebfa692's debugfs dump names the culprit outright:

    /sys/kernel/debug/dri/1/clients            (1 = sun4i-drm, the display)
      command          tgid dev master
      allocator@4.0-s   298   1   **y**      <- DRM master
      composer@2.1-se   299   1   n
      surfaceflinger    308   1   n

So `android.hardware.graphics.allocator@4.0-service.minigbm` holds DRM master on
the display device. The kernel grants master to the *first* opener, and gralloc
(allocator) starts before the composer, so it wins. The composer's
drmSetMaster() then returns EBUSY, its atomic commits return -EACCES, and the
panel keeps the kernel console.

gralloc does not need master: it only allocates dumb buffers. Dropping master
right after opening the node frees it, and the composer's own drmSetMaster()
can then succeed.

The node is opened in cros_gralloc_driver::Initialize(): it probes
"%s/renderD%d" then "%s/card%d"; sun4i has no render node so it settles on
card1 and keeps that fd.
"""

import sys

PATH = "/root/android/lineage/external/minigbm/cros_gralloc/cros_gralloc_driver.cc"

ANCHOR = """			drv_ = drv_create(fd);
			if (drv_) {
				drv_log("render_node_scan: using %s\\n", node);
				free(node);
				return 0;
			}
"""

ADD = """			drv_ = drv_create(fd);
			if (drv_) {
				drv_log("render_node_scan: using %s\\n", node);
				// sd106: see patches/patch-gralloc-drop-master.py. The kernel
				// makes the first opener the DRM master, and gralloc starts
				// before the composer, so it used to sit on master and make the
				// composer's drmSetMaster() fail with EBUSY (every atomic commit
				// then -EACCES and the panel kept the console). Allocation needs
				// no master.
				drmDropMaster(fd);
				free(node);
				return 0;
			}
"""

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "sd106" in text:
    print("already patched (gralloc drops DRM master), nothing to do")
    sys.exit(0)

if ANCHOR not in text:
    sys.exit("anchor not found in " + PATH)

text = text.replace(ANCHOR, ADD, 1)
with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)
print("patched", PATH)
