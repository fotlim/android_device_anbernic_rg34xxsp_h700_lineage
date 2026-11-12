#!/usr/bin/env python3
"""sd50 (v2): ask the device, in the caller's own uid, whether /dev/hwbinder works.

Why this version exists
-----------------------
v1 of this probe ran as root and tested `: < /dev/binder`.  That cannot see the
failure mode that is now the prime suspect, because root bypasses the permission
bits: a node that is 0600 root and a node that is 0666 both open fine for pid 1.

What the source says (all read, not guessed):

  system/libhidl/transport/ServiceManagement.cpp:174  defaultServiceManager1_2()
      if (access("/dev/hwbinder", F_OK|R_OK|W_OK) != 0) {
          // HwBinder not available on this device or not accessible to this
          // process.
          return nullptr;                       <-- no log, no crash
      }
      waitForHwServiceManager();
      while (gDefaultServiceManager == nullptr) { ... sleep(1); }   <-- never returns null

    So HIDL's service manager object is null *only* when that access() fails.
    Every other outcome either blocks or succeeds.

  system/security/keystore/keystore_main.cpp:105
      auto serviceManager = IServiceManager::getService();
      CHECK(serviceManager.get()) << "Failed to get ServiceManager";

    IServiceManager here is the HIDL one, so keystore's abort message is that
    access() failing for uid 1017 (the keystore user).

  drivers/android/binderfs.c:158      init_special_inode(inode, S_IFCHR | 0600, ...)
    binderfs creates its nodes 0600 root.  init.rc:187-189 chmods all three to
    0666 afterwards, and none of those chmods reported a failure, so in theory
    access() should pass -- which is exactly the contradiction this version is
    built to resolve.

  system/hwservicemanager/hwservicemanager.rc    user system, disabled, critical
    It runs as uid 1000 and did not crash, so the node is openable O_RDWR by
    system.  That is compatible with 0666 and with 0660 root:system, but not with
    0600 root.

So v2 adds a second probe that init starts as **uid of keystore** (1017), because
`[ -r ]` / `[ -w ]` / the shell's redirect go through access() exactly like
HIDL's check does.  Both probes still run through exec_background, so nothing can
stall the boot.

The script is idempotent: it replaces its own block when the markers are present.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

BEGIN = "// ---- sd50: does the default mount namespace see /dev/binder ---------------"
END = "// ---- end sd50 ---------------------------------------------------------------"

NEW = r'''// ---- sd50: does the default mount namespace see /dev/binder ---------------
// HIDL's defaultServiceManager1_2() returns nullptr only when
// access("/dev/hwbinder", F_OK|R_OK|W_OK) fails; every other outcome blocks or
// succeeds.  keystore's "Failed to get ServiceManager" is that nullptr, so the
// question is whether /dev/hwbinder is rw for uid 1017 -- not for root.  Hence
// two probes: one as root (existence, symlink target, mount layout) and one as
// the keystore user (the access() test HIDL actually performs).
static const char kDevProbeSh[] =
    "#!/system/bin/sh\n"
    "T=\"$1\"\n"
    "K=/dev/kmsg\n"
    "echo \"devprobe[$T]: begin pid=$$ id=$(id) ctx=$(cat /proc/self/attr/current)\" > $K\n"
    "echo \"devprobe[$T]: names=$(ls /dev | grep -ic bind)\" > $K\n"
    "ls -l /dev/binder /dev/hwbinder /dev/vndbinder > $K 2>&1\n"
    "ls -lL /dev/binder /dev/hwbinder /dev/vndbinder > $K 2>&1\n"
    "readlink /dev/binder /dev/hwbinder /dev/vndbinder > $K 2>&1\n"
    "echo \"devprobe[$T]: binderfs=[$(ls /dev/binderfs 2>&1)]\" > $K\n"
    "ls -ll /dev/binderfs > $K 2>&1\n"
    "echo \"devprobe[$T]: self mounts:\" > $K\n"
    "grep -E \" /dev(/| )\" /proc/self/mountinfo > $K 2>&1\n"
    "echo \"devprobe[$T]: init mounts:\" > $K\n"
    "grep -E \" /dev(/| )\" /proc/1/mountinfo > $K 2>&1\n"
    "SM=$(pidof servicemanager)\n"
    "HM=$(pidof hwservicemanager)\n"
    "echo \"devprobe[$T]: servicemanager=$SM hwservicemanager=$HM\" > $K\n"
    "if [ -n \"$HM\" ]; then\n"
    "    grep -E \"^State|^Threads|^Uid\" /proc/$HM/status > $K 2>&1\n"
    "    ls -l /proc/$HM/fd > $K 2>&1\n"
    "fi\n"
    "for p in /dev/binder /dev/hwbinder /dev/vndbinder; do\n"
    "    if ( : < $p ) 2>/dev/null; then echo \"devprobe[$T]: open $p OK (as root)\" > $K; else echo \"devprobe[$T]: open $p FAILED (as root)\" > $K; fi\n"
    "done\n"
    "if [ \"$T\" = boot ]; then\n"
    "    echo \"devprobe[$T]: service list:\" > $K\n"
    "    timeout 5 /system/bin/service list > $K 2>&1\n"
    "    echo \"devprobe[$T]: service list rc=$?\" > $K\n"
    "fi\n"
    "echo \"devprobe[$T]: end\" > $K\n";

// Runs as a service user (keystore / system), i.e. with the same permissions
// HIDL's access() check and hwservicemanager's manifest read have.  [ -r ] and
// [ -w ] go through access(), exactly like the check that returns nullptr.
static const char kDevProbeUidSh[] =
    "#!/system/bin/sh\n"
    "T=\"$1\"\n"
    "K=/dev/kmsg\n"
    "echo \"devprobe[$T]: id=$(id)\" > $K\n"
    "for p in /dev/binder /dev/hwbinder /dev/vndbinder; do\n"
    "    R=no; W=no\n"
    "    [ -r $p ] && R=yes\n"
    "    [ -w $p ] && W=yes\n"
    "    echo \"devprobe[$T]: $p read=$R write=$W\" > $K\n"
    "    if ( : < $p ) 2>/dev/null; then echo \"devprobe[$T]: open $p OK\" > $K; else echo \"devprobe[$T]: open $p FAILED\" > $K; fi\n"
    "done\n"
    "for f in /system/etc/vintf/manifest.xml /system/manifest.xml /system/etc/vintf/manifest /system/etc/vintf/manifest/android.system.suspend@1.0-service.xml /vendor/etc/vintf/manifest.xml /system/etc/seccomp_policy/mediacodec.policy /system/etc/fonts.xml; do\n"
    "    R=no\n"
    "    [ -r $f ] && R=yes\n"
    "    echo \"devprobe[$T]: read $f $R\" > $K\n"
    "done\n"
    "ls -l /dev/hwbinder /dev/binderfs/hwbinder /system/etc/vintf/manifest.xml /system/etc/vintf > $K 2>&1\n"
    "echo \"devprobe[$T]: done\" > $K\n";

// Two probes, because the two failure classes need different witnesses:
//   * post-fs-data, as root: what the system left behind (modes, symlinks,
//     mount layout, who holds a binder fd).  This runs *before* the chmods
//     below, so it captures the state the boot actually failed in.
//   * boot, after chmod 0666 on the three binderfs nodes: the same report plus
//     the access() test as keystore and as system, i.e. what HIDL and
//     hwservicemanager would see, and whether the chmod was what mattered.
static const char kDevProbeRc[] =
    "on post-fs-data\n"
    "    exec_background - root root -- /system/bin/sh /metadata/dsh/devprobe.sh post-fs-data\n"
    "\n"
    "on boot\n"
    "    chmod 0666 /dev/binderfs/binder\n"
    "    chmod 0666 /dev/binderfs/hwbinder\n"
    "    chmod 0666 /dev/binderfs/vndbinder\n"
    "    exec_background - root root -- /system/bin/sh /metadata/dsh/devprobe.sh boot\n"
    "    start devprobe_keystore\n"
    "    start devprobe_system\n"
    "\n"
    "# The access() test HIDL performs, with the permissions keystore has.\n"
    "service devprobe_keystore /system/bin/sh /metadata/dsh/devprobe-uid.sh keystore\n"
    "    class late_start\n"
    "    user keystore\n"
    "    group keystore drmrpc readproc log\n"
    "    oneshot\n"
    "    disabled\n"
    "\n"
    "# Same, as system: hwservicemanager runs as system and is the one that has to\n"
    "# read the VINTF manifest and be able to open /dev/hwbinder.\n"
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

# Same anchors as before: the block goes in front of PublishRamdiskFstab (after
# PublishTextFile, so the helper is visible) and the call right after the
# selinux-contexts publish, when the /vendor/etc overlay is already up.
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
