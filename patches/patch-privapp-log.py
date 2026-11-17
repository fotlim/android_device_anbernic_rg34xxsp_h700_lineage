#!/usr/bin/env python3
"""sd91: let the boot continue even when a privileged permission is unlisted.

Boot 2cc41b35 is the first one where both APKs actually reach the system:

    sysapp: copied dshsysapp/FusedLocation.apk -> /system/priv-app/FusedLocation/FusedLocation.apk (16798 bytes, ok)
    sysapp: copied dshsysapp/SystemUI.apk -> /system_ext/priv-app/SystemUI/SystemUI.apk (11932626 bytes, ok)

and the location crash is gone (`fused location` hits: 0). What is left is
PackageManagerService refusing the app:

    IllegalStateException: Signature|privileged permissions not in
    privapp-permissions whitelist: {com.android.systemui
    (/system_ext/priv-app/SystemUI): android.permission.OVERRIDE_WIFI_CONFIG, ...}

...six times, once per system_server retry.

The allowlist file for it exists in the build tree --
`out/target/product/rg34xxsp/system_ext/etc/permissions/com.android.systemui.xml`,
4,434 B, 57 entries -- but not on the device: the system_ext partition on the
card is from before SystemUI was ever built, so the file never landed. The
first stage now publishes that XML as well (same overlay trick, see
patches/patch-system-app-overlay.py); this property is the belt to that
braces:

  * with the XML present, PMS is satisfied and enforcement is a no-op;
  * if anything about the XML is wrong (wrong directory, wrong package, a
    permission SystemUI asks for that the file does not list), `log` turns the
    hard failure into the same line as a warning and the boot continues.

Values are checked in RoSystemProperties.CONTROL_PRIVAPP_PERMISSIONS
(`SystemProperties.get("ro.control_privapp_permissions")`). `log` is what
userdebug builds normally ship with; this product inherited `enforce`.

Deliberately NOT `disable`: that would hide real allowlist gaps forever. `log`
still prints every missing entry, so the next dump still tells us what to add.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

# Append after the boot-animation line that patch-skip-bootanim.py adds, so this
# script only has to run after it (see patches/apply.sh order).
ANCHOR = '    text += "debug.sf.nobootanimation=1\\n";\n'

ADD = ANCHOR + '''    // sd91: see patches/patch-privapp-log.py. PMS refuses SystemUI while a
    // privileged permission is not in the privapp allowlist, and the system_ext
    // image predates the app. The XML is published next to the APK; this keeps a
    // gap in it from aborting the boot.
    text += "ro.control_privapp_permissions=log\\n";
'''

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "ro.control_privapp_permissions" in text:
    print("already patched (privapp enforcement is set to log), nothing to do")
    sys.exit(0)

if ANCHOR not in text:
    sys.exit("anchor for the prop.default append not found "
             "(apply patch-skip-bootanim.py first)")

text = text.replace(ANCHOR, ADD, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))
