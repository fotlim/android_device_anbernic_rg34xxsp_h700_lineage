#!/usr/bin/env python3
"""sd112/115: boot-time shortcut that gets past the LineageOS setup wizard AND
keeps the panel awake.

Why the wizard has to be skipped (boot 9a3c220d proves it): the wizard itself is
healthy, but org.lineageos.setupwizard.WifiSetupActivity does

    startFirstRunActivityForResult(new Intent("android.net.wifi.SETUP_WIFI_NETWORK"), ...)

and that intent is handled by com.android.settings (Settings$WifiSetupActivity),
which this product does not build. So the wizard dies with
ActivityNotFoundException right after Locale -> DateTime -> Wifi, and every later
step in res/raw/lineage_wizard_script.xml also needs Settings activities. Rather
than stub all of them, once the framework is up we mark the device provisioned
and drop to the launcher.

Why keep the screen on (boot 3fdfccdb): this handheld has no touchscreen, so
there is never any user activity and PowerManager turns the screen off after the
60 s default. The SystemSuspend HAL then enters deep sleep and cannot freeze
userspace ("Freezing user space processes aborted ... 172 tasks refusing to
freeze"), so it aborts and retries once a second -- `PM: suspend entry/exit` in a
tight loop, which looks exactly like the device blinking then powering off.
Setting an effectively infinite screen-off timeout and stay-on keeps it awake so
the panel stays lit while we work on the launcher.

Remove this service (and re-enable org.lineageos.setupwizard) once
com.android.settings, a real WiFi stack and working suspend exist.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishSkipSuw"

SKIP_SH = """#!/system/bin/sh
LOG=/metadata/dsh/skip-suw.log
echo "skip-suw: start" > $LOG
n=0
while [ $n -lt 180 ]; do
    [ "$(getprop sys.boot_completed)" = "1" ] && break
    n=$((n+1)); sleep 1
done
echo "skip-suw: boot_completed after ${n}s" >> $LOG
# sd115: keep the panel awake. No touch => no user activity => 60s screen-off =>
# SystemSuspend HAL deep-sleep loop that cannot freeze userspace and retries
# forever (boot 3fdfccdb). An effectively infinite timeout + stay-on stops it.
settings put system screen_off_timeout 2147483647 >> $LOG 2>&1
settings put global stay_on_while_plugged_in 7 >> $LOG 2>&1
svc power stayon true >> $LOG 2>&1
# sd112: mark provisioned and drop the crashing wizard, then go to the launcher.
settings put global device_provisioned 1 >> $LOG 2>&1
settings put secure user_setup_complete 1 >> $LOG 2>&1
settings put secure tv_user_setup_complete 1 >> $LOG 2>&1
pm disable-user --user 0 org.lineageos.setupwizard >> $LOG 2>&1
am force-stop org.lineageos.setupwizard >> $LOG 2>&1
sleep 1
am start -a android.intent.action.MAIN -c android.intent.category.HOME -f 0x10000000 >> $LOG 2>&1
# sd119: SystemUI's ImageWallpaper render thread SIGSEGVs on this Panfrost/minigbm
# stack (Fatal signal 11 fault addr 0x38, null deref, name ImageWallpaper), which
# crash-loops all of SystemUI and keeps the lock screen bouncing. Disable the
# ImageWallpaper component and recycle SystemUI so it runs without a wallpaper.
pm disable --user 0 com.android.systemui/com.android.systemui.ImageWallpaper >> $LOG 2>&1
pm disable-user --user 0 com.android.systemui/com.android.systemui.ImageWallpaper >> $LOG 2>&1
am force-stop com.android.systemui >> $LOG 2>&1
# sd116/118: no touchscreen; force the swipe keyguard away (retry while SystemUI settles).
settings put secure lockscreen.disabled 1 >> $LOG 2>&1
settings put global lockscreen.disabled 1 >> $LOG 2>&1
i=0
while [ $i -lt 8 ]; do
    sleep 3
    wm dismiss-keyguard >> $LOG 2>&1
    i=$((i+1))
done
# sd116: record the gamepad input device so the joystick-as-mouse daemon can be
# built against the exact event node / axes next round.
cat /proc/bus/input/devices > /metadata/dsh/input-devices.txt 2>>$LOG
echo "skip-suw: done" >> $LOG
"""

SKIP_RC = """# class core: late_start waits for vold.decrypt, which this device never reaches.
service dsh-skip-suw /system/bin/sh /metadata/dsh/dsh-skip-suw.sh
    class core
    oneshot
    user root
    group root
"""

NEW_CODE = r'''// sd112: mark the device provisioned and drop to the launcher, because
// org.lineageos.setupwizard's WiFi step needs com.android.settings which this
// product does not build; sd115 also keeps the panel awake so the no-touch
// device never enters the broken autosuspend loop. See patches/patch-skip-suw.py.
const char kSkipSuwSh[] =
%s
const char kSkipSuwRc[] =
%s
void PublishSkipSuw() {
    PublishTextFile("/metadata/dsh/dsh-skip-suw.sh", kSkipSuwSh, 0755);
    PublishTextFile("/vendor/etc/init/93-dsh-skip-suw.rc", kSkipSuwRc, 0644);
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
        print("already patched (PublishSkipSuw present), nothing to do")
        return 0

    anchor = "void PublishRamdiskFstab(DIR* ramdisk) {"
    if anchor not in text:
        sys.exit("anchor for function insert not found")

    text = text.replace(anchor,
                        (NEW_CODE % (c_literal(SKIP_SH), c_literal(SKIP_RC))) + anchor,
                        1)

    if "    PublishLiveSnapshot();\n" in text:
        text = text.replace("    PublishLiveSnapshot();\n",
                            "    PublishLiveSnapshot();\n    PublishSkipSuw();\n", 1)
    elif "    PublishServiceRcOverrides();\n" in text:
        text = text.replace("    PublishServiceRcOverrides();\n",
                            "    PublishServiceRcOverrides();\n    PublishSkipSuw();\n", 1)
    else:
        sys.exit("anchor for call insert not found")

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("patched", PATH, "->", text.count("\n") + 1, "lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
