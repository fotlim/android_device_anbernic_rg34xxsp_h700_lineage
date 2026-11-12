#!/usr/bin/env python3
"""sd51: /system/etc is readable by root and by nobody else.  Confirm it on device.

What boot 8f4589fb proved (sd50 v3 probe)
-----------------------------------------
  * /dev/binder, /dev/hwbinder, /dev/vndbinder are `crw-rw-rw- 1 root root`,
    major 238, and `/dev/binderfs` is mounted with shared:24 in BOTH the probe's
    and pid 1's mountinfo  -> the nodes and the mount are fine.
  * `: < /dev/hwbinder` succeeds as root, and `service list` answers with
    "Found 6 services: ... vold" -> the AIDL servicemanager is healthy and even
    vold registered.  So the system binder path is not the problem.
  * hwservicemanager (pid 180, user system) holds fd 6 -> /dev/binderfs/hwbinder,
    so it can open its own node too.
  * Nevertheless 217 keystore aborts still say "Failed to get ServiceManager",
    and p03 names the HIDL side out loud:
        E HidlServiceManagement: Service ... must be in VINTF manifest in order
        to register/get.
    while hwservicemanager says, every single time:
        W hwservicemanager: Cannot fetch /system/etc/vintf/manifest.xml:
          Cannot read /system/etc/vintf/manifest.xml: Permission denied

The pattern that matters
------------------------
Three unrelated non-root processes fail to read files under /system/etc, and
only under /system/etc:

    /system/etc/vintf/manifest.xml              hwservicemanager (uid 1000)
    /system/etc/seccomp_policy/mediacodec.policy media.codec
    /system/etc/fonts.xml                        zygote

while root reads /system/etc/selinux/plat_service_contexts with access=0 and
init parses /system/etc/init/*.rc without complaint.  One unreadable subtree
explains all six abort classes (four VINTF/HIDL, media.codec, zygote preload),
because with an empty framework manifest every HIDL getTransport() returns
EMPTY: getService() returns null and registerAsService() returns UNKNOWN_ERROR.

So the question this probe settles is exactly: on the DEVICE (not in system.img),
what are the modes, owners, symlink targets and labels of those files, and can
uid 1017 / uid 1000 actually read them?

Note the earlier mistake that made this invisible: the sd50 probe grepped
mountinfo for " /dev" only, so a /system/etc overlay would not have shown up.
This version dumps the whole mountinfo and stats /system and /system/etc.

Why the output goes to a file
-----------------------------
The sd50 v3 "on boot" block fell inside a 35.5 s - 36.0 s window where the ring
buffer lost 433 lines ("kauditd_printk_skb: 627 callbacks suppressed"), and with
it every uid-probe line.  /dev/kmsg is ratelimited and lossy; /metadata is ext4
and survives the next reboot, where debug-init copies dsh/probe.log back to the
card as DEBUG/meta-dsh_probe.log.  Both channels are written, the file is the
authoritative one.

Idempotent: replaces its own block when the markers are present.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

BEGIN = "// ---- sd50: does the default mount namespace see /dev/binder ---------------"
END = "// ---- end sd50 ---------------------------------------------------------------"

NEW = r'''// ---- sd50: does the default mount namespace see /dev/binder ---------------
// The nodes, their mode and the binderfs mount are all fine (sd50 v3).  What is
// left is that no non-root process can read anything under /system/etc, which
// empties the framework VINTF manifest and takes every HIDL user down with it.
// Record the device-side truth to /metadata/dsh/probe.log: modes, owners,
// readlink targets and actual read tests as root, as keystore and as system.
static const char kDevProbeSh[] =
    "#!/system/bin/sh\n"
    "T=\"$1\"\n"
    "K=/dev/kmsg\n"
    "L=/metadata/dsh/probe.log\n"
    "echo \"===== devprobe[$T] pid=$$ id=$(id) ct=$(cat /proc/self/attr/current) =====\" >> $L\n"
    "note() { echo \"$*\" >> $L; echo \"devprobe[$T]: $*\" > $K; }\n"
    "note \"root sees: $(id -u)\"\n"
    "note \"--- the three binder nodes ---\"\n"
    "for p in /dev/binder /dev/hwbinder /dev/vndbinder; do\n"
    "    note \"$(ls -lL $p 2>&1 | tr -s ' ')\"\n"
    "    note \"readlink: $(readlink $p 2>&1)\"\n"
    "done\n"
    "note \"--- /system and /system/etc themselves ---\"\n"
    "note \"$(ls -ld /system /system/etc /system/etc/vintf /system/etc/seccomp_policy 2>&1 | tr -s ' ')\"\n"
    "note \"--- the four files that EACCES ---\"\n"
    "for p in /system/etc/vintf/manifest.xml /system/etc/vintf/manifest /system/etc/seccomp_policy/mediacodec.policy /system/etc/fonts.xml; do\n"
    "    note \"$(ls -lL $p 2>&1 | tr -s ' ')\"\n"
    "    note \"readlink[$p]=$(readlink $p 2>&1)\"\n"
    "    R=no; [ -r $p ] && R=yes\n"
    "    note \"root read $p = $R\"\n"
    "    if ( : < $p ) 2>/dev/null; then note \"root open $p OK\"; else note \"root open $p FAILED\"; fi\n"
    "done\n"
    "note \"--- fragment dir ---\"\n"
    "note \"$(ls -l /system/etc/vintf/manifest 2>&1 | tr -s ' ')\"\n"
    "note \"--- selinux label of the manifest (root only) ---\"\n"
    "note \"$(ls -Zd /system/etc/vintf/manifest.xml 2>&1)\"\n"
    "note \"--- full mountinfo (no filter: an overlay of /system/etc must not hide again) ---\"\n"
    "cat /proc/self/mountinfo >> $L\n"
    "note \"--- servicemenagers ---\"\n"
    "note \"servicemanager=$(pidof servicemanager) hwservicemanager=$(pidof hwservicemanager)\"\n"
    "HM=$(pidof hwservicemanager)\n"
    "if [ -n \"$HM\" ]; then ls -l /proc/$HM/fd >> $L 2>&1; fi\n"
    "note \"--- service list ---\"\n"
    "timeout 5 /system/bin/service list >> $L 2>&1\n"
    "note \"service list rc=$?\"\n"
    "note \"===== end devprobe[$T] =====\"\n";

// Same checks with the permissions of a service user.  [ -r ] is access(R_OK),
// which is the call HIDL's defaultServiceManager() makes before returning null.
static const char kDevProbeUidSh[] =
    "#!/system/bin/sh\n"
    "T=\"$1\"\n"
    "K=/dev/kmsg\n"
    "L=/metadata/dsh/probe.log\n"
    "echo \"===== devprobe[$T] id=$(id) ct=$(cat /proc/self/attr/current) =====\" >> $L\n"
    "note() { echo \"$*\" >> $L; echo \"devprobe[$T]: $*\" > $K; }\n"
    "for p in /dev/binder /dev/hwbinder /dev/vndbinder; do\n"
    "    R=no; W=no\n"
    "    [ -r $p ] && R=yes\n"
    "    [ -w $p ] && W=yes\n"
    "    note \"$p read=$R write=$W\"\n"
    "    if ( : < $p ) 2>/dev/null; then note \"open $p OK\"; else note \"open $p FAILED\"; fi\n"
    "done\n"
    "for p in /system/etc/vintf/manifest.xml /system/etc/vintf/manifest /system/etc/seccomp_policy/mediacodec.policy /system/etc/fonts.xml /system/etc/init/hw/init.rc /system/bin/sh; do\n"
    "    R=no\n"
    "    [ -r $p ] && R=yes\n"
    "    note \"read $p = $R\"\n"
    "done\n"
    "note \"$(ls -ld /system /system/etc /system/etc/vintf 2>&1 | tr -s ' ')\"\n"
    "note \"$(ls -lL /system/etc/vintf/manifest.xml 2>&1 | tr -s ' ')\"\n"
    "note \"===== end devprobe[$T] =====\"\n";

static const char kDevProbeRc[] =
    "on post-fs-data\n"
    "    exec_background - root root -- /system/bin/sh /metadata/dsh/devprobe.sh root\n"
    "    start devprobe_keystore\n"
    "    start devprobe_system\n"
    "\n"
    "# The read test HIDL performs, with the permissions keystore has.\n"
    "service devprobe_keystore /system/bin/sh /metadata/dsh/devprobe-uid.sh keystore\n"
    "    class late_start\n"
    "    user keystore\n"
    "    group keystore drmrpc readproc log\n"
    "    oneshot\n"
    "    disabled\n"
    "\n"
    "# Same, as system: hwservicemanager's identity, and the one that has to read\n"
    "# the framework VINTF manifest.\n"
    "service devprobe_system /system/bin/sh /metadata/dsh/devprobe-uid.sh system\n"
    "    class late_start\n"
    "    user system\n"
    "    group system readproc\n"
    "    oneshot\n"
    "    disabled\n";

static void PublishDevProbe() {
    bool ok = PublishTextFile("/metadata/dsh/devprobe.sh", kDevProbeSh, 0755);
    ok = PublishTextFile("/metadata/dsh/devprobe-uid.sh", kDevProbeUidSh, 0755) && ok;
    ok = PublishTextFile("/vendor/etc/init/95-devprobe.rc", kDevProbeRc, 0644) && ok;
    pubnote("devprobe: staged, %s", ok ? "ok" : "with errors");
}
// ---- end sd50 ---------------------------------------------------------------
'''

ANCHOR_FUNC = "void PublishRamdiskFstab(DIR* ramdisk) {"
ANCHOR_CALL = "    PublishSelinuxContexts();\n"
CALL_LINE = "    PublishDevProbe();\n"


def main() -> int:
    with open(PATH, encoding="utf-8") as f:
        src = f.read()

    block = NEW.strip("\n")

    begin = src.find(BEGIN)
    if begin >= 0:
        end = src.find(END, begin)
        if end < 0:
            print("END marker not found", file=sys.stderr)
            return 1
        end += len(END)
        out = src[:begin] + block + src[end:]
        print("replaced block: %d -> %d bytes, %d -> %d lines"
              % (end - begin, len(block), src.count("\n"), out.count("\n")))
    else:
        if ANCHOR_FUNC not in src:
            print("MISSING anchor: %s" % ANCHOR_FUNC, file=sys.stderr)
            return 1
        if ANCHOR_CALL not in src:
            print("MISSING anchor: %s" % ANCHOR_CALL.strip(), file=sys.stderr)
            return 1
        out = src.replace(ANCHOR_FUNC, "\n" + block + "\n\n" + ANCHOR_FUNC, 1)
        out = out.replace(ANCHOR_CALL, ANCHOR_CALL + CALL_LINE, 1)
        print("inserted block: %d bytes, %d -> %d lines"
              % (len(block), src.count("\n"), out.count("\n")))

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())