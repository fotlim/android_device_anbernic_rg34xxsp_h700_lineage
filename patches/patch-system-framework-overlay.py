#!/usr/bin/env python3
"""UNUSED since sd81 -- kept only as a reference technique.

sd79/sd80 built this to ship a patched org.lineageos.platform.jar that would add
a null guard to ProfileTriggerHelper. Two things killed it:

  * the jar build pulls frameworks/base + services and then fails on
    metalava's API-lint gate (`--lints-as-errors`) for a tree this stale;
  * more importantly the guard was treating the symptom. The real cause of the
    phase-550 death was init answering EPERM for ctl.start(wpa_supplicant)
    (see patches/patch-wpa-supplicant-stub.py), and the next blocker at phase 600
    is the missing FusedLocation app (see patches/patch-system-app-overlay.py).

Do not add this to patches/apply.sh while the jar is not shipped: its
oat-invalidation half would force a needless JIT of org.lineageos.platform.jar.

Original description follows.
"""

patches/patch-lineage-wifi-npe.py adds the null guard that keeps
ProfileManagerService from killing system_server at boot phase 550 on a wifi-less
device. That guard lives in lineage-sdk, so the jar that carries it has to be
replaced on the device. It is 3.8 MB of dex -- too big for a text publish, so it
travels in the initramfs like the vendor graphics files and is copied into a
tmpfs overlay at first-stage time (PublishVendorGraphics is the same trick fo
/vendor/lib64/hw and /vendor/bin/hw).

Its preopt must not survive either: /system/framework/oat/arm64 carries
org.lineageos.platform.odex/.vdex compiled from the ORIGINAL jar, and ART would
happily load those with a jar they do not match. The oat overlay already exists
(PublishStaleServicesOdex mounts it for services.odex), so the two files are
shadowed with the same deliberately-invalid content -- ART then JITs the new jar.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

CODE = r'''// sd79: see patches/patch-system-framework-overlay.py.
void PublishSystemFramework(int dfd) {
    if (dfd < 0) {
        pubnote("sysfw: no ramdisk handle, skipping the framework overlay");
        return;
    }
    if (!OverlayDir("/system/framework", "/dev/sysfw_upper", "/dev/sysfw_work")) {
        pubnote("sysfw: overlay of /system/framework failed");
        return;
    }
    chmod("/system/framework", 0755);
    chmod("/dev/sysfw_upper", 0755);
    chmod("/dev/sysfw_work", 0755);
    CopyFromRamdisk(dfd, "system-overlay/framework/org.lineageos.platform.jar",
                    "/system/framework/org.lineageos.platform.jar", 0644);
}

'''

FUNC_ANCHOR = "void PublishRamdiskFstab(DIR* ramdisk) {"
CALL_ANCHOR = "    PublishVendorGraphics(dfd);\n"

ODEX_ANCHOR = "    PublishStaleServicesOdex();\n"
ODEX_ADD = ODEX_ANCHOR + '''    // sd79: the replacing org.lineageos.platform.jar has different classes, so the
    // preopt made from the original one must not be loaded either.
    static const char kPlatformJarBogus[] =
        "stale preopt: org.lineageos.platform.jar was replaced by the ramdisk copy\\n"
        "(ProfileTriggerHelper null guard). LEFT DELIBERATELY INVALID so art falls\\n"
        "back to the dex and JITs it. See patches/patch-lineage-wifi-npe.py.\\n";
    PublishTextFile("/system/framework/oat/arm64/org.lineageos.platform.odex",
                    kPlatformJarBogus, 0644);
    PublishTextFile("/system/framework/oat/arm64/org.lineageos.platform.vdex",
                    kPlatformJarBogus, 0644);
'''

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "PublishSystemFramework" in text:
    print("already patched (the framework overlay is published), nothing to do")
    sys.exit(0)

for anchor, what in ((FUNC_ANCHOR, "function"), (CALL_ANCHOR, "call"),
                     (ODEX_ANCHOR, "oat invalidation")):
    if anchor not in text:
        sys.exit("anchor for the %s not found" % what)

text = text.replace(FUNC_ANCHOR, CODE + FUNC_ANCHOR, 1)
text = text.replace(CALL_ANCHOR, CALL_ANCHOR + "    PublishSystemFramework(dfd);\n", 1)
text = text.replace(ODEX_ANCHOR, ODEX_ADD, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))