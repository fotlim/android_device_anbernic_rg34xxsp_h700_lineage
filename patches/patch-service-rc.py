#!/usr/bin/env python3
"""Shadow two service rc files so init reads a version this device can honour.

Boot a25179f8 finally had clean kernel logs (`drm.debug=0x0` plus a 4 MiB ring),
and the four-second cycle that had been eating three boots became a sentence:

    init: Service 'netd' (pid 494) exited with status 1
    init: Sending signal 9 to service 'netd' (pid 494) process group...
    init: Sending signal 9 to service 'zygote' (pid 491) process group...
    init: Sending signal 9 to service 'zygote_secondary' (pid 492) process group...
    init: Service 'zygote' (pid 491) received signal 9

netd cannot start on this kernel:

    E Netd: Unable to create netlink socket for family 5: Protocol not supported
    E Netd: Unable to create netlink socket for family 12: Protocol not supported
    E Netd: Unable to open strict socket
    E Netd: Unable to create NFLogListener: Status[code: 93,
            msg: "[Protocol not supported] : socket() failed"]

(no NETFILTER_NETLINK / NFLOG, and `iptables-restore: unable to initialize
table 'filter'` -- that is a kernel .config gap, tracked separately), and
netd.rc reacts to its death with

    onrestart restart zygote
    onrestart restart zygote_secondary

so every netd exit kills both zygotes, and zygote never lives long enough to
fork system_server. Drop those two lines: netd still fails, but quietly.

The second file is the memtrack HAL. Its service is a passthrough wrapper around
hw_get_module("memtrack"), and hardware/libhardware/modules/memtrack does not
exist in this tree at all, so it can only exit 1 every five seconds forever.
Nothing on the way to the UI needs it, so it is not defined.

Both files are shadowed rather than patched in the image: init parses rc files
only after the first stage has run, and the first stage already overlays
/vendor/etc. /system/etc gets the same treatment here (and gains the useful side
effect that the later fix-public-libs.sh script can write its file directly
instead of mounting a second overlay of its own).

Boot 04213c6b then showed the loop had nothing to do with netd any more: the
kmsg held zero `onrestart restart zygote` entries, and logcat finally carried
zygote's own words -- `Error preloading android.renderscript.Element$1`,
`ExceptionInInitializerError`, `librs_jni.so not found`. /system/lib64 has no
librs_jni.so at all and preloaded-classes lists 19 android.renderscript lines,
so preloadClasses() rethrows the Error and zygote dies before forkSystemServer.
The same /system/etc overlay now publishes a filtered preloaded-classes.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishServiceRcOverrides"

NEW_CODE = r'''// sd56: two rc files have to read differently from what the image ships. Both
// can be shadowed by an overlay because init parses rc files only after this
// first stage has finished.
//
//  * /system/etc/init/netd.rc -- netd cannot start on this kernel at all
//    (no NETFILTER_NETLINK / NFLOG, no iptables 'filter' table), so it exits 1
//    a few seconds in; netd.rc answers with
//        onrestart restart zygote
//        onrestart restart zygote_secondary
//    and boot a25179f8 showed that killing both zygotes every four seconds, in
//    four consecutive cycles. zygote never lives long enough to fork
//    system_server, which is the whole reason the UI never came up.
//
//  * /vendor/etc/init/android.hardware.memtrack@1.0-service.rc -- the service is
//    a passthrough wrapper around hw_get_module("memtrack"), and this tree has
//    no legacy memtrack HAL module at all, so it can only exit 1 every 5 s.
//    Nothing on the way to the UI needs it.
const char kNetdRc[] =
    "service netd /system/bin/netd\n"
    "    class main\n"
    "    capabilities CHOWN DAC_OVERRIDE DAC_READ_SEARCH FOWNER IPC_LOCK KILL "
    "NET_ADMIN NET_BIND_SERVICE NET_RAW SETUID SETGID\n"
    "    socket dnsproxyd stream 0660 root inet\n"
    "    socket mdns stream 0660 root system\n"
    "    socket fwmarkd stream 0660 root inet\n"
    "    # onrestart restart zygote / zygote_secondary removed: this kernel has\n"
    "    # no netfilter netlink, so netd always exits 1, and those two lines\n"
    "    # then kill both zygotes before either can fork system_server.\n"
    "    updatable\n";

const char kMemtrackRc[] =
    "# android.hardware.memtrack@1.0-service is deliberately not defined.\n"
    "# See patches/patch-service-rc.py: hw_get_module(\"memtrack\") can never\n"
    "# succeed on this tree, and the failure only restarts a HAL nobody needs.\n";

// sd59: /system/etc/preloaded-classes ships 19 android.renderscript lines, but
// this image has no librs_jni.so *at all* (checked in
// out/target/product/rg34xxsp/system/lib64). RenderScript.<clinit> therefore
// throws ExceptionInInitializerError, and ZygoteInit.preloadClasses() answers
// any Error by rethrowing it -- so zygote dies in the middle of preload, before
// forkSystemServer is ever called. Boot 04213c6b proved it: 59 zygote lives of
// five seconds each, "Error preloading android.renderscript.Element$1", and not
// one system_server. Nothing else on this device needs those classes, so drop
// the lines from the list instead of shipping a RenderScript that cannot load.
void PublishFilteredPreloadedClasses() {
    static const char kPath[] = "/system/etc/preloaded-classes";
    FILE* fp = fopen(kPath, "re");
    if (fp == nullptr) {
        pubnote("preload: open %s failed: %d (%s)", kPath, errno, strerror(errno));
        return;
    }
    std::string kept;
    char line[400];
    size_t kept_lines = 0, dropped = 0;
    while (fgets(line, sizeof(line), fp) != nullptr) {
        if (strstr(line, "android.renderscript") != nullptr) {
            dropped++;
            continue;
        }
        kept += line;
        kept_lines++;
    }
    fclose(fp);
    if (kept.empty()) {
        pubnote("preload: %s read back empty, not publishing", kPath);
        return;
    }
    if (!PublishTextFile(kPath, kept.c_str(), 0644)) {
        return;
    }
    chmod(kPath, 0644);
    pubnote("preload: kept %zu lines, dropped %zu renderscript lines (%zu bytes)",
            kept_lines, dropped, kept.size());
}

void PublishServiceRcOverrides() {
    mkdir("/dev/sys_etc_upper", 0755);
    mkdir("/dev/sys_etc_work", 0755);
    if (OverlayDir("/system/etc", "/dev/sys_etc_upper", "/dev/sys_etc_work")) {
        chmod("/system/etc", 0755);
        chmod("/dev/sys_etc_upper", 0755);
        chmod("/dev/sys_etc_work", 0755);
        PublishTextFile("/system/etc/init/netd.rc", kNetdRc, 0644);
        PublishFilteredPreloadedClasses();
    } else {
        pubnote("sd56: /system/etc overlay failed, netd.rc keeps its onrestart");
    }
    PublishTextFile("/vendor/etc/init/android.hardware.memtrack@1.0-service.rc",
                    kMemtrackRc, 0644);
}

'''

ANCHOR_FUNC = "void PublishRamdiskFstab(DIR* ramdisk) {"
ANCHOR_CALL = "    PublishVendorGraphics(dfd);\n"

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if MARKER in text:
    print("already patched (service rc overrides are in place), nothing to do")
    sys.exit(0)

if ANCHOR_FUNC not in text:
    sys.exit("anchor for function insert not found")
if ANCHOR_CALL not in text:
    sys.exit("anchor for call insert not found")

text = text.replace(ANCHOR_FUNC, NEW_CODE + ANCHOR_FUNC, 1)
text = text.replace(ANCHOR_CALL, ANCHOR_CALL + "    PublishServiceRcOverrides();\n", 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "->", text.count("\n") + 1, "lines")