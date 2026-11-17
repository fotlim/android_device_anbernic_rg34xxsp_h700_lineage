#!/usr/bin/env python3
"""sd80: give init a service named wpa_supplicant so WifiService can start it.

Boot b30feba9 (the FEATURE_WIFI run) got past boot phase 550 and then died in
system_server's WifiHandlerThread:

    FATAL EXCEPTION IN SYSTEM PROCESS: WifiHandlerThread
    java.lang.RuntimeException: failed to set system property
      at android.os.SystemProperties.native_set
      at android.os.SystemService.start
      at com.android.server.wifi.SupplicantManager.start
      at com.android.server.wifi.WifiNative.startSupplicant

and kmsg had the real reason:

    init: Control message: Could not find 'wpa_supplicant' for ctl.start
          from pid: 465 (system_server)
    libc: Unable to set property "ctl.start" to "wpa_supplicant": error code: 0x20

init returns EPERM for a ctl.start that names an unknown service, so the Java
write fails and the wifi handler thread takes the whole system process down.

So define the name. Nothing here is wifi: it is /system/bin/sleep, which keeps
the service "running" long enough for WifiNative's connect wait to give up
normally (it then logs a failure instead of crashing). No class is given, so the
service never starts at boot -- only an explicit ctl.start starts it.

This is a placeholder: a real port with a wifi driver would install the real
wpa_supplicant and this rc would be deleted.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

RC = '''const char kWpaSupplicantStubRc[] =
    "\\n"
    "# sd80: see patches/patch-wpa-supplicant-stub.py. init answers EPERM for a\\n"
    "# ctl.start that names a service it does not know, and WifiService does\\n"
    "# SystemService.start(\\"wpa_supplicant\\") -- so the name has to exist.\\n"
    "service wpa_supplicant /system/bin/sleep 1000000\\n"
    "    user root\\n"
    "    group root\\n";

'''

FUNC_ANCHOR = "void PublishServiceRcOverrides() {"

PUBLISH_ANCHOR = """    PublishTextFile("/vendor/etc/init/93-wifi-stub.rc", kWifiStubRcOff, 0644);
"""

PUBLISH_ADD = PUBLISH_ANCHOR + """    PublishTextFile("/vendor/etc/init/93-wpa-supplicant.rc", kWpaSupplicantStubRc, 0644);
"""

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "kWpaSupplicantStubRc" in text:
    print("already patched (the wpa_supplicant placeholder rc is published), nothing to do")
    sys.exit(0)

if FUNC_ANCHOR not in text:
    sys.exit("anchor for the consts not found")
if PUBLISH_ANCHOR not in text:
    sys.exit("anchor for the wifi stub rc publish not found")

text = text.replace(FUNC_ANCHOR, RC + FUNC_ANCHOR, 1)
text = text.replace(PUBLISH_ANCHOR, PUBLISH_ADD, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))