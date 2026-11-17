#!/usr/bin/env python3
"""sd85: do not let the boot animation deadlock system_server.

Boot 9300494a: 163 binder services, system_server alive, DRM `DSI-1` reports
`connected / enabled / On`, /proc/fb has sun4i-drmdrmfb, and SurfaceFlinger has a
「Built-in Screen」at 720x480 with `powerMode=On`. Yet the panel keeps showing
whatever debug-init painted, `sys.boot_completed` is never set, and neither
SystemUI nor Launcher ever starts (ps has no such processes).

The chain, every link checked in source:

  * WindowManagerService.checkBootAnimationCompleteLocked() polls
        SystemService.isRunning("bootanim")     // WindowManagerService.java:3511
    every 50 ms and returns false -- i.e. "keep waiting" -- while it answers running;
  * `bootanim` (pid 452) is stuck in binder_thread_read. Its animation thread is
    already dead: `dumpsys SurfaceFlinger` shows 0 layers, FramebufferSurface
    frame-counter 0, flips 0, all three slots FREE. `BootAnimation::readyToRun()`
    failed, so `Thread::_threadLoop` exited, but main() cannot see that:
        boot->run("BootAnimation", PRIORITY_DISPLAY);   // returns OK = thread created
        IPCThreadState::self()->joinThreadPool();       // blocks forever
    (system/core/libutils/Threads.cpp:718 documents that run() only reports thread
     creation, not readyToRun()'s result.) So the init service never stops.
  * therefore enableScreenAfterBoot never runs, boot_completed is never set, and
    nothing ever asks the Launcher to draw.

Disabling the boot animation breaks the deadlock with a property instead of a
framework rebuild: BootAnimationUtil::bootAnimationDisabled() returns true, main()
returns 0 immediately, init marks the service stopped, and WindowManager's poll
succeeds on its next tick.

This is also honest: there is nothing to see in an animation on a device whose
GPU path is SwiftShader and whose real goal right now is the first frame of the UI.
The animation's own failure is captured separately by the live snapshot
(live-bootanim-run.txt / live-bootanim-log.txt), so we can still fix it properly
once the desktop works.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

# Append after the hwc.drm.device line that patch-hwc-drm-device.py adds, so this
# script only has to run after it (see patches/apply.sh order).
ANCHOR = '    text += "hwc.drm.device=/dev/dri/card1\\n";\n'

ADD = ANCHOR + '''    // sd85: see patches/patch-skip-bootanim.py. WindowManagerService blocks boot
    // until `init.svc.bootanim` stops; bootanimation's animation thread dies in
    // readyToRun() while main() sits in joinThreadPool(), so the service never
    // stops and sys.boot_completed never happens.
    text += "debug.sf.nobootanimation=1\\n";
'''

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "debug.sf.nobootanimation" in text:
    print("already patched (boot animation is disabled), nothing to do")
    sys.exit(0)

if ANCHOR not in text:
    sys.exit("anchor for the prop.default append not found "
             "(apply patch-hwc-drm-device.py first)")

text = text.replace(ANCHOR, ADD, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))