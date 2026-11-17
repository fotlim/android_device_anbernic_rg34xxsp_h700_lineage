#!/usr/bin/env python3
"""sd79: stop Lineage's ProfileManagerService from taking system_server down.

Boots a95db9e0 -> b30feba9 all died at boot phase 550 on:

    java.lang.NullPointerException: Attempt to invoke virtual method
      'android.net.wifi.WifiInfo android.net.wifi.WifiManager.getConnectionInfo()'
      on a null object reference
      at org.lineageos.platform.internal.ProfileTriggerHelper.getActiveSSID(:192)

`mWifiManager` is null because this device has no wifi and therefore no "wifi"
binder: Context.getSystemService(WIFI_SERVICE) goes through
SystemServiceRegistry.registerContextAwareService(), which returns null while the
binder is absent.

Two attempts to make the binder exist did not work:

  * patches/wifi-stub published one ourselves -- impossible from a vendor binary
    (Parcel interface-token magic 'VNDR' vs 'SYST', see Parcel.cpp:531) and the
    runtime .so decides the magic anyway;
  * declaring FEATURE_WIFI made framework WifiService register the name
    (boot: "WifiService: Registering wifi", phase 480/500/550 all passed) but
    WifiService then crashed system_server while starting the supplicant:

        FATAL EXCEPTION IN SYSTEM PROCESS: WifiHandlerThread
        RuntimeException: failed to set system property
          at SystemService.start -> SupplicantManager.start
          at SupplicantStaIfaceHal.startDaemon -> WifiNative.startSupplicant

So the honest fix is the one-line guard upstream would want for a wifi-less
device: treat "no wifi manager" as "no SSID". That edits lineage-sdk, so the
rebuilt org.lineageos.platform.jar has to be shipped (see
patches/patch-system-framework-overlay.py).
"""

import sys

PATH = ("/root/android/lineage/lineage-sdk/lineage/lib/main/java/org/lineageos/"
        "platform/internal/ProfileTriggerHelper.java")

OLD = """    private String getActiveSSID() {
        WifiInfo wifiinfo = mWifiManager.getConnectionInfo();
"""

NEW = """    private String getActiveSSID() {
        // rg34xxsp: this device has no wifi, so Context.getSystemService(WIFI_SERVICE)
        // is null and the unconditional dereference below took system_server down at
        // boot phase 550 (boots a95db9e0 … b30feba9). Mirrors the null check already
        // present for the WifiInfo itself.
        if (mWifiManager == null) {
            return null;
        }
        WifiInfo wifiinfo = mWifiManager.getConnectionInfo();
"""

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "if (mWifiManager == null)" in text:
    print("already patched (getActiveSSID guards a null WifiManager), nothing to do")
    sys.exit(0)

if OLD not in text:
    sys.exit("anchor for getActiveSSID not found")

text = text.replace(OLD, NEW, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))