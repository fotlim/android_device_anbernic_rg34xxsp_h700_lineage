#!/usr/bin/env python3
"""sd75: give SurfaceFlinger the composer service version it actually asks for.

Boot df79ab2d, in order, inside SurfaceFlinger's own start:

    I SurfaceFlinger: Using HWComposer service: 'default'
    I hwservicemanager: Since android.hardware.graphics.composer@2.1::IComposer/
                        default is not registered, trying to start it as a
                        lazy HAL.
    I init: Control message: Could not find
            'android.hardware.graphics.composer@2.1::IComposer/default' for
            ctl.interface_start from pid: 187 (/system/bin/hwservicemanager)

So this tree's SurfaceFlinger is a composer@2.1 client, and the image answers
with the 2.2 service only:

  * android.hardware.graphics.composer@2.2-service is built and installed
    (its rc is hardware/interfaces/graphics/composer/2.2/default/
    ...@2.2-service.rc, which -- unlike the 2.1 one -- has no `interface` line
    at all, so nothing could satisfy a 2.1 lookup even in principle), and
  * it dies with status 1 within a millisecond of dlopen()ing
    hwcomposer.drm_minigbm.so, 216 times in boot 690cccf3, which init turns
    into "updatable process ... exited 4 times before boot completed", an
    apexd revert attempt, and one surfaceflinger restart per death.

Both halves get fixed here: a 2.1 rc with the interface line (the binary comes
from tools/build-hwc-probe.sh -> /vendor/bin/hw through the ramdisk overlay),
and a no-op 2.2 rc so the restart storm stops. Nothing on this device is a
composer@2.2 client.

This is a separate script from patch-service-rc.py because that one exits on
its own marker; keeping the new work here keeps both patches idempotent.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

CONSTS = r'''// sd75: SurfaceFlinger is a composer@2.1 client; see patch-composer-rc.py.
const char kComposer21Rc[] =
    "\n"
    "service vendor.hwcomposer-2-1 /vendor/bin/hw/android.hardware.graphics.composer@2.1-service\n"
    "    interface android.hardware.graphics.composer@2.1::IComposer default\n"
    "    class hal animation\n"
    "    user root\n"
    "    group graphics drmrpc\n"
    "    capabilities SYS_NICE\n"
    "    onrestart restart surfaceflinger\n";
// AOSP's 2.1 rc also asks for
//   writepid /dev/cpuset/system-background/tasks
// which this device does not have; it only produced an init error line, and
// the 2.2 service started fine with it, so it is dropped rather than guessed at.

// The 2.2 composer exits with status 1 one millisecond after it loads
// hwcomposer.drm_minigbm.so. Its deaths are what init reports as
// "updatable process 'vendor.hwcomposer-2-2' exited 4 times before boot
// completed" before asking apexd for a revert, and every death restarts
// surfaceflinger through its onrestart. Nothing here is a 2.2 client.
const char kComposer22RcOff[] =
    "# sd75: replaced by a no-op, see patches/patch-composer-rc.py\n"
    "# The 2.2 composer exits with status 1 on every start, and SurfaceFlinger\n"
    "# asks for android.hardware.graphics.composer@2.1::IComposer/default.\n";

'''

FUNC_ANCHOR = "void PublishServiceRcOverrides() {"

PUBLISH_ANCHOR = """    PublishTextFile("/vendor/etc/init/android.hardware.memtrack@1.0-service.rc",
                    kMemtrackRc, 0644);
"""

PUBLISH_ADD = PUBLISH_ANCHOR + """    PublishTextFile("/vendor/etc/init/android.hardware.graphics.composer@2.1-service.rc",
                    kComposer21Rc, 0644);
    PublishTextFile("/vendor/etc/init/android.hardware.graphics.composer@2.2-service.rc",
                    kComposer22RcOff, 0644);
"""

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "kComposer21Rc" in text:
    print("already patched (composer 2.1 rc is published), nothing to do")
    sys.exit(0)

if FUNC_ANCHOR not in text:
    sys.exit("anchor for the composer consts not found")
if PUBLISH_ANCHOR not in text:
    sys.exit("anchor for the memtrack rc publish not found")

text = text.replace(FUNC_ANCHOR, CONSTS + FUNC_ANCHOR, 1)
text = text.replace(PUBLISH_ANCHOR, PUBLISH_ADD, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "->", text.count("\n") + 1, "lines")