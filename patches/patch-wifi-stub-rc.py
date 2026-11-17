#!/usr/bin/env python3
"""sd77: start the "wifi" binder stub before system_server reaches boot phase 550.

Boot a95db9e0 got all the way to DisplayManagerService ("Built-in Screen" 720x480
ON, 86 binder services) and then died in system_server:

    FATAL EXCEPTION IN SYSTEM PROCESS: main
    java.lang.RuntimeException: Failed to boot service
      org.lineageos.platform.internal.ProfileManagerService: onBootPhase threw
      an exception during phase 550
    Caused by: java.lang.NullPointerException: Attempt to invoke
      'android.net.wifi.WifiInfo android.net.wifi.WifiManager.getConnectionInfo()'
      on a null object reference
      at org.lineageos.platform.internal.ProfileTriggerHelper.getActiveSSID()

WifiManager comes back null because WifiFrameworkInitializer registers it with
SystemServiceRegistry.registerContextAwareService("wifi", ...), which returns null
while no process owns the "wifi" binder -- and the framework only starts
WifiService when the product declares FEATURE_WIFI. Patches/wifi-stub registers
that binder, so the wrapper exists and getConnectionInfo() reads a null WifiInfo,
which getActiveSSID() handles.

`class core`, not `hal`/`late_start`: this has to be up before system_server's
phase 550, which happens tens of seconds before zygote's late services.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

RC = '''const char kWifiStubRc[] =
    "\\n"
    "service dsh-wifi-stub /vendor/bin/hw/dsh-wifi-stub\\n"
    "    class core\\n"
    "    user system\\n"
    "    group system\\n";

'''

FUNC_ANCHOR = "void PublishServiceRcOverrides() {"

PUBLISH_ANCHOR = """    PublishTextFile("/vendor/etc/init/android.hardware.graphics.composer@2.2-service.rc",
                    kComposer22RcOff, 0644);
"""

PUBLISH_ADD = PUBLISH_ANCHOR + """    PublishTextFile("/vendor/etc/init/93-wifi-stub.rc", kWifiStubRc, 0644);
"""

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "kWifiStubRc" in text:
    print("already patched (the wifi stub rc is published), nothing to do")
    sys.exit(0)

if FUNC_ANCHOR not in text:
    sys.exit("anchor for the consts not found")
if PUBLISH_ANCHOR not in text:
    sys.exit("anchor for the 2.2 rc publish not found")

text = text.replace(FUNC_ANCHOR, RC + FUNC_ANCHOR, 1)
text = text.replace(PUBLISH_ANCHOR, PUBLISH_ADD, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "->", text.count("\n") + 1, "lines")