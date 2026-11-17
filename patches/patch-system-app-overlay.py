#!/usr/bin/env python3
"""sd81: publish the fused-location provider through a /system/priv-app overlay.

Boot ec9f8a19 got past the wifi work -- kmsg now shows
    init: Control message: Processed ctl.start for 'wpa_supplicant' from pid: 468
(no more "Could not find"), there is no WifiHandlerThread fatal, and the boot
reached **phase 600** -- and then died on:

    IllegalStateException: Unable to find a direct boot aware fused location provide
      at com.android.server.location.LocationManagerService.initializeProvidersLocked(:555)

which is an unconditional
    Preconditions.checkState(!queryIntentServicesAsUser(
        ACTION_FUSED_LOCATION_SERVICE, MATCH_DIRECT_BOOT_AWARE|MATCH_SYSTEM_ONLY, ...).isEmpty())

frameworks/base/packages/FusedLocation declares exactly that service, but the
module is listed only in build/make/target/product/handheld_system.mk, which this
product's inheritance chain never reaches -- the same root cause as the missing
fonts and media_system.mk (HANDOVER §9). Nothing else in the tree provides it.

The APK is 16.8 KB, so it travels in the initramfs (like the vendor graphics files)
and first stage overlays /system/priv-app with a tmpfs upper layer and copies it in.
PackageManagerService scans /system/priv-app during system_server startup, long
after the overlay exists, so the app installs as a privileged system app and the
intent filter resolves.

Proper fix for a later full rebuild: add FusedLocation to PRODUCT_PACKAGES in
device/anbernic/rg34xxsp/device.mk, or inherit handheld_system.mk.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

CODE = r'''// sd81: see patches/patch-system-app-overlay.py.
void PublishSystemApp(int dfd) {
    if (dfd < 0) {
        pubnote("sysapp: no ramdisk handle, skipping the /system/priv-app overlay");
        return;
    }
    if (!OverlayDir("/system/priv-app", "/dev/sysapp_upper", "/dev/sysapp_work")) {
        pubnote("sysapp: overlay of /system/priv-app failed");
        return;
    }
    chmod("/system/priv-app", 0755);
    chmod("/dev/sysapp_upper", 0755);
    chmod("/dev/sysapp_work", 0755);
    if (mkdir("/system/priv-app/FusedLocation", 0755) != 0 && errno != EEXIST) {
        pubnote("sysapp: mkdir FusedLocation failed: %d (%s)", errno, strerror(errno));
    }
    chmod("/system/priv-app/FusedLocation", 0755);
    CopyFromRamdisk(dfd, "system-overlay/priv-app/FusedLocation/FusedLocation.apk",
                    "/system/priv-app/FusedLocation/FusedLocation.apk", 0644);
}

'''

FUNC_ANCHOR = "void PublishRamdiskFstab(DIR* ramdisk) {"
CALL_ANCHOR = "    PublishVendorGraphics(dfd);\n"

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "PublishSystemApp" in text:
    print("already patched (the /system/priv-app overlay is published), nothing to do")
    sys.exit(0)

if FUNC_ANCHOR not in text:
    sys.exit("anchor for the function insert not found")
if CALL_ANCHOR not in text:
    sys.exit("anchor for the call insert not found")

text = text.replace(FUNC_ANCHOR, CODE + FUNC_ANCHOR, 1)
text = text.replace(CALL_ANCHOR, CALL_ANCHOR + "    PublishSystemApp(dfd);\n", 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))