#!/usr/bin/env python3
"""sd50: find out whether the default mount namespace can see /dev/binder.

What the 4afef271 boot proved
-----------------------------
The chain runs: first stage -> selinux -> second stage -> post-fs-data ->
class_start hal/core/main/late_start all succeeded -> SurfaceFlinger started ->
zygote and zygote_secondary are accepting connections.  396 s, no panic.

What dies is every service that needs a binder service manager, and only two
kinds of abort message remain (227 aborts, 6 distinct strings):

    Failed to get ServiceManager        (keystore, audioserver)
    -2147483648 == 0x80000000 == UNKNOWN_ERROR
                                        (system-suspend, allocator, wificond)

The first is libbinder's defaultServiceManager() returning nullptr, the second
is an HIDL registration transaction failing.  Both service managers -- servicer
manager on /dev/binder and hwservicemanager on /dev/hwbinder -- are unreachable
for processes started after post-fs-data.

Why that is suspicious: servicemanager and hwservicemanager start at 24.4 s,
*after* early-init created /dev/binderfs and the three symlinks, and neither of
them ever gets killed or restarted (init would say so, it is a critical
service).  The clients start at ~41 s -- after enter_default_mount_ns at 26.5 s.
So the split is exactly the namespace switch.

What is already known about the device side
------------------------------------------
  * /proc/devices has "238 binder" (a dynamic char major, i.e. binderfs), not
    the misc 10:58 pair Android 11 expects, and devtmpfs' /dev has no binder
    node at all.  binder.c:7119 gates the CONFIG_ANDROID_BINDER_DEVICES block
    behind !IS_ENABLED(CONFIG_ANDROID_BINDERFS), and this kernel has
    CONFIG_ANDROID_BINDERFS=y, so that string is inert.
  * system/core/rootdir/init.rc:175-189 does mount binderfs in early-init and
    creates /dev/binder, /dev/hwbinder, /dev/vndbinder.  No command failure for
    any of those appears in the 4.7 MB kmsg, so they all succeeded.
  * The graphics composer did open /dev/binderfs/vndbinder (avc shows
    dev="binder" ino=6), so binderfs exists and has nodes.

Both "the nodes are there" and "no client can see them" cannot be true in one
namespace, so this probe asks the device, after the switch, read-only:

  * what /dev/binder, /dev/hwbinder, /dev/vndbinder are right now (symlink?
    char node? absent), where the symlinks point, and what /dev/binderfs holds;
  * which mount backs /dev in this namespace vs in pid 1's mountinfo;
  * whether this process can open the two nodes at all;
  * whether servicemanager is alive and holding a binder fd (i.e. whether it is
    the context manager), and, at "boot", what `service list` answers.

Nothing here writes to /dev, mounts anything or changes a mode: it only reads
and reports.  The two triggers are post-fs-data (right after
enter_default_mount_ns) and boot (after the failing services have come up), both
through exec_background so a hung helper can never stall the boot.

The script is idempotent: it replaces its own block if the markers are already
in the file, and inserts both the block and its call site otherwise.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

BEGIN = "// ---- sd50: does the default mount namespace see /dev/binder ---------------"
END = "// ---- end sd50 ---------------------------------------------------------------"

NEW = r'''// ---- sd50: does the default mount namespace see /dev/binder ---------------
// Every binder user that starts after enter_default_mount_ns fails, every one
// that starts before it works, and no init.rc command for the binderfs mount or
// the three symlinks reported a failure.  So ask the device, after the switch,
// what is actually reachable.  Read-only: no mount, no mknod, no chmod.
static const char kDevProbeSh[] =
    "#!/system/bin/sh\n"
    "T=\"$1\"\n"
    "K=/dev/kmsg\n"
    "echo \"devprobe[$T]: begin pid=$$ ctx=$(cat /proc/self/attr/current)\" > $K\n"
    "echo \"devprobe[$T]: names=$(ls /dev | grep -ic bind) md=$(ls /dev | grep -i md | head -1)\" > $K\n"
    "ls -l /dev/binder /dev/hwbinder /dev/vndbinder > $K 2>&1\n"
    "readlink /dev/binder /dev/hwbinder /dev/vndbinder > $K 2>&1\n"
    "echo \"devprobe[$T]: binderfs=[$(ls /dev/binderfs 2>&1)]\" > $K\n"
    "echo \"devprobe[$T]: self mounts:\" > $K\n"
    "grep -E \" /dev(/| )\" /proc/self/mountinfo > $K 2>&1\n"
    "echo \"devprobe[$T]: init mounts:\" > $K\n"
    "grep -E \" /dev(/| )\" /proc/1/mountinfo > $K 2>&1\n"
    "SM=$(pidof servicemanager)\n"
    "echo \"devprobe[$T]: servicemanager=$SM\" > $K\n"
    "if [ -n \"$SM\" ]; then\n"
    "    grep -E \"^State|^Threads|^SigBlk\" /proc/$SM/status > $K 2>&1\n"
    "    ls -l /proc/$SM/fd > $K 2>&1\n"
    "fi\n"
    "HM=$(pidof hwservicemanager)\n"
    "echo \"devprobe[$T]: hwservicemanager=$HM\" > $K\n"
    "if ( : < /dev/binder ) 2>/dev/null; then\n"
    "    echo \"devprobe[$T]: open /dev/binder OK\" > $K\n"
    "else\n"
    "    echo \"devprobe[$T]: open /dev/binder FAILED\" > $K\n"
    "fi\n"
    "if ( : < /dev/hwbinder ) 2>/dev/null; then\n"
    "    echo \"devprobe[$T]: open /dev/hwbinder OK\" > $K\n"
    "else\n"
    "    echo \"devprobe[$T]: open /dev/hwbinder FAILED\" > $K\n"
    "fi\n"
    "if [ \"$T\" = boot ]; then\n"
    "    echo \"devprobe[$T]: service list:\" > $K\n"
    "    timeout 5 /system/bin/service list > $K 2>&1\n"
    "    echo \"devprobe[$T]: service list rc=$?\" > $K\n"
    "fi\n"
    "echo \"devprobe[$T]: end\" > $K\n";

static const char kDevProbeRc[] =
    "on post-fs-data\n"
    "    exec_background - root root -- /system/bin/sh /metadata/dsh/devprobe.sh post-fs-data\n"
    "\n"
    "on boot\n"
    "    exec_background - root root -- /system/bin/sh /metadata/dsh/devprobe.sh boot\n";

static void PublishDevProbe() {
    bool ok = PublishTextFile("/metadata/dsh/devprobe.sh", kDevProbeSh, 0755);
    ok = PublishTextFile("/vendor/etc/init/95-devprobe.rc", kDevProbeRc, 0644) && ok;
    pubnote("devprobe: staged, %s", ok ? "ok" : "with errors");
}
// ---- end sd50 ---------------------------------------------------------------
'''


# Same anchors as the sd49 block: the new block is inserted in front of
# PublishRamdiskFstab (which is after PublishTextFile, so the helper is visible)
# and the call goes right after PublishSelinuxContexts(), i.e. after the
# /vendor/etc overlay is up and before the ramdisk is freed.
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
