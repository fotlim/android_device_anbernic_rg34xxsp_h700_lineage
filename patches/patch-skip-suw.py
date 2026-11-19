#!/usr/bin/env python3
"""sd112: boot-time shortcut that gets past the LineageOS setup wizard.

Why (boot 9a3c220d proves it): the wizard itself is healthy, but
org.lineageos.setupwizard.WifiSetupActivity does

    startFirstRunActivityForResult(new Intent("android.net.wifi.SETUP_WIFI_NETWORK"), ...)

and that intent is handled by com.android.settings (Settings$WifiSetupActivity),
which this product does not build. So the wizard dies with

    FATAL EXCEPTION: main
    java.lang.RuntimeException: Unable to start activity
      ComponentInfo{org.lineageos.setupwizard/.WifiSetupActivity}:
      android.content.ActivityNotFoundException:
        No Activity found to handle Intent { act=android.net.wifi.SETUP_WIFI_NETWORK }

right after Locale -> DateTime -> Wifi, and every later step in
res/raw/lineage_wizard_script.xml (sim_missing, location_settings,
biometric_settings, lockscreen_settings, restore) also needs Settings
activities. Stubbing all of them is a losing game; instead, once the framework
is up, mark the device provisioned and drop to the launcher. Remove this service
(and re-enable org.lineageos.setupwizard) once com.android.settings and a real
WiFi stack exist.

This runs as `class core` for the same reason patch-live-snapshot.py does:
`late_start` waits for vold.decrypt, which never happens on this device. The
pm/am/settings invocations are the same ones the live snapshot already performs
successfully from this same kind of root one-shot.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishSkipSuw"

SKIP_SH = """#!/system/bin/sh
# sd112: bring-up shortcut (see patches/patch-skip-suw.py).
LOG=/metadata/dsh/skip-suw.log
echo "skip-suw: start" > $LOG
n=0
while [ $n -lt 180 ]; do
    [ "$(getprop sys.boot_completed)" = "1" ] && break
    n=$((n+1)); sleep 1
done
echo "skip-suw: boot_completed after ${n}s" >> $LOG
settings put global device_provisioned 1 >> $LOG 2>&1
settings put secure user_setup_complete 1 >> $LOG 2>&1
settings put secure tv_user_setup_complete 1 >> $LOG 2>&1
pm disable-user --user 0 org.lineageos.setupwizard >> $LOG 2>&1
am force-stop org.lineageos.setupwizard >> $LOG 2>&1
sleep 1
am start -a android.intent.action.MAIN -c android.intent.category.HOME -f 0x10000000 >> $LOG 2>&1
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
// product does not build. See patches/patch-skip-suw.py.
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
