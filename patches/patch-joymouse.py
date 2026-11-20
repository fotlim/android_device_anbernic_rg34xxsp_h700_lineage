#!/usr/bin/env python3
"""sd118: start the joystick-as-mouse daemon (patches/joymouse) as a root service.

The device has no touchscreen and the gamepad keys are not mapped to a pointer,
so the Android UI (lock screen swipe, launcher taps) cannot be driven. `dsh-joymouse`
creates a uinput virtual mouse and maps the left stick to the pointer and the face
buttons to clicks (kernel CONFIG_INPUT_UINPUT=y).

The binary itself is baked into the initrd's dshsysapp/ and published to
/system/bin/dsh-joymouse by patch-system-app-overlay.py; this patch only publishes
the init rc that keeps it running (class core, restarts if it exits).
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishJoyMouseRc"

JOY_RC = """service dsh-joymouse /system/bin/dsh-joymouse
    class core
    user root
    group root
"""

# sd132: drmserver registers the legacy drm.drmManager binder service that
# MediaProvider.onCreate's DrmManagerClient blocks on. Separate rc file, root,
# class core, so init runs it very early and registers drm.drmManager before
# MediaProvider starts. Binary published to /system/bin by patch-system-app-overlay.
DRM_RC = """service drm /system/bin/drmserver
    class core
    user root
    group root
    writepid /dev/cpuset/foreground/tasks
"""

NEW_CODE = r'''// sd118: joystick-as-mouse daemon (binary published to /system/bin by
// patch-system-app-overlay.py). See patches/joymouse/joymouse.c.
const char kJoyMouseRc[] =
%s
// sd132: drmserver rc (registers drm.drmManager for MediaProvider).
const char kDrmServerRc[] =
%s
void PublishJoyMouseRc() {
    PublishTextFile("/vendor/etc/init/91-dsh-joymouse.rc", kJoyMouseRc, 0644);
    PublishTextFile("/vendor/etc/init/90-dsh-drmserver.rc", kDrmServerRc, 0644);
}

'''


def c_literal(text):
    out = []
    for line in text.splitlines():
        esc = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append('    "%s\\n"\n' % esc)
    return "".join(out).rstrip("\n") + ";\n"


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        print("already patched (PublishJoyMouseRc present), nothing to do")
        return 0

    anchor = "void PublishRamdiskFstab(DIR* ramdisk) {"
    if anchor not in text:
        sys.exit("anchor for function insert not found")
    text = text.replace(anchor, (NEW_CODE % (c_literal(JOY_RC), c_literal(DRM_RC))) + anchor, 1)

    for call in ("    PublishSkipSuw();\n", "    PublishLiveSnapshot();\n",
                 "    PublishServiceRcOverrides();\n"):
        if call in text:
            text = text.replace(call, call + "    PublishJoyMouseRc();\n", 1)
            break
    else:
        sys.exit("anchor for call insert not found")

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("patched", PATH, "->", text.count("\n") + 1, "lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
