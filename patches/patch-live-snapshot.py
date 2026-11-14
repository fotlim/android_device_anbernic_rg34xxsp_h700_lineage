#!/usr/bin/env python3
"""Copy this boot's logcat and receipts onto the card, so a dump is not stale.

Everything debug-init harvests at startup is by definition the PREVIOUS boot's:
harvest_metadata() and harvest_data() run before this boot has written anything,
and the only filesystems Windows can read afterwards are the FAT partitions on
the card. That mismatch cost several rounds of analysis on stale evidence.

A class-core oneshot sleeps until the boot has had a minute to fail, mounts the
card's boot partition read-write, writes DEBUG/live-*, and unmounts. class core
rather than late_start on purpose: late_start waits for vold.decrypt and this
device never gets that far.

Boot 9ac27bf6 showed two defects in the first version, both fixed here:

  * `logcat -d`, not the /data/local/tmp file. That file is written by a logcat
    service a later patch replaced, so it has been stale since boot 702a1e18 --
    the snapshot copied 1.5 MiB of it and every line was an old boot.
  * it now also copies /system/etc/seccomp_policy/mediacodec.policy and
    /system/etc/init/netd.rc, the two files whose state kept being guessed at,
    so one dump proves whether the early-init fix and the rc override landed.
    (Boot 04213c6b is why both paths are written down twice in comments: the
    first version asked /vendor/etc/init/netd.rc, which never exists, and
    /metadata/dsh/first-stage-publish.txt, which lives one level up.)

  * later still, DEBUG/live-preload.txt records the three numbers that decide
    whether zygote can survive its preload: renderscript lines left in
    preloaded-classes (must be 0), onrestart lines in netd.rc, and the seccomp
    policy's line/include counts.

This script can REGENERATE its literals: an insert-only patch silently keeps the
first version forever once its marker is in the file, which is how the stale
`tail -c` line survived. Both literals are re-sliced on every run.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishLiveSnapshot"

LIVE_SH = """#!/system/bin/sh
# One shot, about a minute in: copy THIS boot's logcat and receipts to the card
# under DEBUG/live-*. Everything else in DEBUG/ is the previous boot.
LOG=/metadata/dsh/snapshot.log
CARD=/mnt/dshcard
mkdir -p $CARD /metadata/dsh 2>/dev/null
echo "snapshot: start" > $LOG
sleep 45
if ! mount -t vfat -o rw /dev/block/mmcblk0p1 $CARD 2>>$LOG; then
    echo "snapshot: cannot mount the boot partition" >> $LOG
    exit 0
fi
mkdir -p $CARD/DEBUG 2>>$LOG
for f in fix.log probe.log public.libraries.txt; do
    [ -f /metadata/dsh/$f ] && cp /metadata/dsh/$f $CARD/DEBUG/live-$f 2>>$LOG
done
# The first stage writes this one at the metadata root, not under dsh/ (boot
# 04213c6b is missing it precisely because the old line looked in dsh/).
[ -f /metadata/first-stage-publish.txt ] && cp /metadata/first-stage-publish.txt $CARD/DEBUG/live-first-stage-publish.txt 2>>$LOG
# The three files the last rounds kept getting wrong, in the state this boot
# actually used: the policy must show 61 lines / 0 includes, netd.rc must be the
# rewritten one (no 'onrestart restart zygote'), and the preload list must have
# dropped its android.renderscript lines or zygote will die again.
cp /system/etc/seccomp_policy/mediacodec.policy $CARD/DEBUG/live-mediacodec.policy 2>>$LOG
cp /system/etc/init/netd.rc $CARD/DEBUG/live-netd.rc 2>>$LOG
{
    echo "preloaded-classes: $(wc -l < /system/etc/preloaded-classes) lines, $(grep -c android.renderscript /system/etc/preloaded-classes) renderscript lines"
    echo "netd.rc onrestart-restart-zygote: $(grep -c onrestart /system/etc/init/netd.rc) lines"
    echo "mediacodec.policy: $(wc -l < /system/etc/seccomp_policy/mediacodec.policy) lines, $(grep -c @include /system/etc/seccomp_policy/mediacodec.policy) includes"
    echo "systemserver-classpath rc files: $(grep -l SYSTEMSERVERCLASSPATH /vendor/etc/init/*.rc 2>/dev/null | wc -l)"
} > $CARD/DEBUG/live-preload.txt 2>>$LOG
# -d, and not /data/local/tmp/dsh-android.log: logcat's own ring holds THIS boot,
# while that file has been stale since boot 702a1e18 (the service writing it was
# replaced by a later patch). 1.5 MiB keeps a 64 MiB card safe.
logcat -d -b main -b system -b crash -v threadtime 2>/dev/null | tail -c 1572864 > $CARD/DEBUG/live-logcat.txt 2>>$LOG
# 45 s is too early to see the interesting half. Boot 18c6c7be reached
# "SystemServer: Entered the Android system server!" at 47 s and the snapshot
# stopped exactly there, so the failure (or the stall) after it was invisible.
# Wait another 75 s and take a second sample: services that started, a hang or a
# watchdog kill all show up in this window, and the property table answers
# "which of them is actually running" even when logcat has lost the lines.
sleep 75
logcat -d -b main -b system -b crash -b events -v threadtime 2>/dev/null | tail -c 1048576 > $CARD/DEBUG/live-logcat-late.txt 2>>$LOG
getprop 2>/dev/null | grep -E 'init\\.svc\\.|boot_completed|dev\\.bootcomplete' > $CARD/DEBUG/live-props.txt 2>>$LOG
ps -A 2>/dev/null | grep -E 'zygote|system_server|surfaceflinger|bootanim|netd|installd|audioserver|media' > $CARD/DEBUG/live-ps.txt 2>>$LOG
# 已注册的 binder 服务：system_server 卡在哪个 startService，这张表比日志直接
service list 2>/dev/null > $CARD/DEBUG/live-services.txt 2>>$LOG
sync
echo "snapshot: wrote $(ls $CARD/DEBUG/live-* 2>/dev/null | wc -l) files" >> $LOG
umount $CARD 2>>$LOG
exit 0
"""

LIVE_RC = """# class core, not late_start: late_start waits for vold.decrypt and nothing on
# this device reaches that far yet, so the snapshot would never run.
service dsh-live-snapshot /system/bin/sh /metadata/dsh/live-snapshot.sh
    class core
    oneshot
    user root
    group root
"""

NEW_CODE = r'''// sd57: a snapshot of the boot that is currently running, on the card.
//
// debug-init's harvest runs at the start of a boot, so every receipt it copies
// describes the boot BEFORE; Windows can only read the card, and /metadata plus
// /data are ext4. Without this the newest readable logcat is always one boot
// old, which cost several rounds of analysis on stale evidence.
const char kLiveSnapshotSh[] =
%s
const char kLiveSnapshotRc[] =
%s
void PublishLiveSnapshot() {
    PublishTextFile("/metadata/dsh/live-snapshot.sh", kLiveSnapshotSh, 0755);
    PublishTextFile("/vendor/etc/init/94-live-snapshot.rc", kLiveSnapshotRc, 0644);
}

'''


def c_literal(text):
    """C string literal lines, one source line per shell line."""
    out = []
    for line in text.splitlines():
        esc = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append('    "%s\\n"\n' % esc)
    return "".join(out).rstrip("\n") + ";\n"


def reslice(text, head, tail, body):
    """Replace the literal between `head` and `tail`; None if the anchors are gone."""
    i = text.find(head)
    if i < 0:
        return None
    j = text.find(tail, i + len(head))
    if j < 0:
        return None
    return text[:i] + head + c_literal(body) + text[j:]


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        # Already inserted: regenerate the two literals so edits here take effect.
        sh_head = "const char kLiveSnapshotSh[] =\n"
        sh_tail = "\nconst char kLiveSnapshotRc[] ="
        rc_head = "const char kLiveSnapshotRc[] =\n"
        rc_tail = "\nvoid PublishLiveSnapshot()"

        for head, tail, body, name in ((sh_head, sh_tail, LIVE_SH, "kLiveSnapshotSh"),
                                       (rc_head, rc_tail, LIVE_RC, "kLiveSnapshotRc")):
            new = reslice(text, head, tail, body)
            if new is None:
                sys.exit("anchors for %s not found" % name)
            text = new

        with open(PATH, "w", encoding="utf-8") as f:
            f.write(text)
        print("regenerated kLiveSnapshotSh / kLiveSnapshotRc in", PATH)
        return 0

    anchor = "void PublishRamdiskFstab(DIR* ramdisk) {"
    call = "    PublishServiceRcOverrides();\n"
    if anchor not in text:
        sys.exit("anchor for function insert not found")
    if call not in text:
        sys.exit("anchor for call insert not found")

    text = text.replace(anchor, (NEW_CODE % (c_literal(LIVE_SH), c_literal(LIVE_RC))) + anchor, 1)
    text = text.replace(call, call + "    PublishLiveSnapshot();\n", 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("patched", PATH, "->", text.count("\n") + 1, "lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())