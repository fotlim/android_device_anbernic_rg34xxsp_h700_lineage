#!/usr/bin/env python3
"""sd78: declare FEATURE_WIFI so the framework starts WifiService itself.

The chain that keeps killing system_server at boot phase 550:

  ProfileTriggerHelper(:71)  mWifiManager = getSystemService(WIFI_SERVICE)
  ProfileTriggerHelper(:192) mWifiManager.getConnectionInfo()   -> NPE

getSystemService(WIFI_SERVICE) is null when no process owns the "wifi" binde
(WifiFrameworkInitializer registers WifiManager with registerContextAwareService).
The binder is absent because SystemServer only starts WifiService when the product
declares PackageManager.FEATURE_WIFI, and this product does not.

Publishing that binder from our own stub hit a wall worth recording: a
*vendor*-variant libbinder writes the Parcel interface-token header 'VNDR' where
the framework servicemanager insists on 'SYST'
(frameworks/native/libs/binder/Parcel.cpp:531). Boot ee9a6ca4 showed

    E Parcel: Expecting header 0x53595354 but found 0x564e4452
    addService(wifi) failed: -129      (EX_TRANSACTION_FAILED)

and building the stub as a system module did not help (boot b30feba9), because the
.so chosen at runtime is still the vendor flavoured libbinder.

So: let the framework do it. /system/etc/permissions is written by the first
stage already and PackageManagerService reads it long before phase 550; with the
feature declared SystemServer starts WifiService in-process, and the manager then
answers with "no connection" instead of the context service being null.

The wifi stub rc is blanked in the same pass: if WifiService owns the name first
the stub can never win, and a service that retries for 60 s and dies only adds
noise. kWifiStubRc itself stays defined by patch-wifi-stub-rc.py and would become
an unused-variable error under -Werror, so it is marked [[maybe_unused]] here.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

FEATURE_XML = '''// sd78: see patches/patch-wifi-feature.py.
// sd79: FEATURE_WIFI is *off* again. Declaring it did make WifiService registe
// the "wifi" binder (phase 480/500/550 all passed), but WifiService then crashed
// system_server itself while starting the supplicant:
//   FATAL EXCEPTION IN SYSTEM PROCESS: WifiHandlerThread
//   RuntimeException: failed to set system property
//     at SystemService.start -> SupplicantManager.start -> WifiNative.startSupplicant
// This device has no wpa_supplicant, so the feature simply is not true here. The
// phase-550 NPE is handled in lineage-sdk instead (patch-lineage-wifi-npe.py).
const char kWifiFeatureXml[] =
    "<?xml version=\\"1.0\\" encoding=\\"utf-8\\"?>\\n"
    "<permissions>\\n"
    "    <!-- sd79: intentionally declares no feature; see patch-wifi-feature.py -->\\n"
    "</permissions>\\n";

// The wifi stub must not run either way.
const char kWifiStubRcOff[] =
    "# sd79: replaced by a no-op, see patches/patch-wifi-feature.py\\n"
    "# The phase-550 NPE is fixed in lineage-sdk (patch-lineage-wifi-npe.py).\\n";

'''

FUNC_ANCHOR = "void PublishServiceRcOverrides() {"

PUBLISH_ANCHOR = """    PublishTextFile("/vendor/etc/init/93-wifi-stub.rc", kWifiStubRc, 0644);
"""

PUBLISH_ADD = """    PublishTextFile("/system/etc/permissions/android.hardware.wifi.xml", kWifiFeatureXml,
                    0644);
    PublishTextFile("/vendor/etc/init/93-wifi-stub.rc", kWifiStubRcOff, 0644);
"""

UNUSED_OLD = "const char kWifiStubRc[] ="
UNUSED_NEW = "[[maybe_unused]] const char kWifiStubRc[] ="

with open(PATH, encoding="utf-8") as f:
    text = f.read()

changed = []

if UNUSED_OLD in text and UNUSED_NEW not in text:
    text = text.replace(UNUSED_OLD, UNUSED_NEW, 1)
    changed.append("kWifiStubRc marked [[maybe_unused]]")

if "kWifiFeatureXml" not in text:
    if FUNC_ANCHOR not in text:
        sys.exit("anchor for the consts not found")
    text = text.replace(FUNC_ANCHOR, FEATURE_XML + FUNC_ANCHOR, 1)
    if PUBLISH_ANCHOR not in text:
        sys.exit("anchor for the wifi stub rc publish not found")
    text = text.replace(PUBLISH_ANCHOR, PUBLISH_ADD, 1)
    changed.append("FEATURE_WIFI xml published, stub rc blanked")

FEATURE_LINES = ('    "    <feature name=\\"android.hardware.wifi\\" />\\n"\n'
                 '    "    <feature name=\\"android.hardware.wifi.direct\\" />\\n"\n')
FEATURE_LINES_OFF = ('    "    <!-- sd79: intentionally declares no feature; '
                     'see patch-wifi-feature.py -->\\n"\n')

# sd80: the feature is needed again. It was revoked in sd79 because WifiService
# crashed -- but that crash was init answering EPERM for ctl.start(wpa_supplicant),
# not anything wrong with the feature itself; patches/patch-wpa-supplicant-stub.py
# now defines that service name. So put the features back.
if FEATURE_LINES_OFF in text:
    text = text.replace(FEATURE_LINES_OFF, FEATURE_LINES, 1)
    changed.append("FEATURE_WIFI declared again (wpa_supplicant name now exists)")

if not changed:
    print("already patched (FEATURE_WIFI is declared), nothing to do")
    sys.exit(0)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()), ";", ", ".join(changed))