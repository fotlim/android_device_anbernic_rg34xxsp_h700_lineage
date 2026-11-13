#!/usr/bin/env python3
"""Copy this boot's logcat and receipts onto the card, so a dump is not stale.

Everything debug-init harvests at startup is by definition the PREVIOUS boot's:
harvest_metadata() and harvest_data() run before this boot has written anything,
and the only filesystems Windows can read afterwards are the two FAT partitions
on the card. That is why p01..p05 always describe the boot before the one the
user just ran -- three separate rounds of analysis were spent on logs that were
one boot out of date, and the receipts that actually answered the question
(netd's NFLOG failure) had to wait a full extra boot to be readable.

So a oneshot service sleeps until the boot has had a minute to fail, mounts the
card's boot partition read-write, drops `DEBUG/live-*.txt` there, and unmounts.
The card is only ever written from this one place at that point, vold does not
manage mmcblk0p1, and everything is capped so a 64 MiB FAT cannot fill up.

The one thing to remember when reading a dump: `live-*` is THIS boot,
everything else (`p01..p06`, `meta-*`, `dmesg-*`, `kmsg-*`) is the previous one.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishLiveSnapshot"

NEW_CODE = r'''// sd57: a snapshot of the boot that is currently running, on the card.
//
// debug-init's harvest runs at the start of a boot, so every receipt it copies
// describes the boot BEFORE. Windows can only read the card's FAT partitions,
// and /metadata plus /data are ext4, so without this the newest readable logcat
// is always one boot old. That cost three rounds of analysis on stale evidence.
const char kLiveSnapshotSh[] =
    "#!/system/bin/sh\n"
    "# One shot, about a minute in: copy this boot's logcat and receipts to the\n"
    "# card under DEBUG/live-*. Everything else in DEBUG/ is the previous boot.\n"
    "LOG=/metadata/dsh/snapshot.log\n"
    "CARD=/mnt/dshcard\n"
    "mkdir -p $CARD /metadata/dsh 2>/dev/null\n"
    "echo \"snapshot: start\" > $LOG\n"
    "sleep 45\n"
    "if ! mount -t vfat -o rw /dev/block/mmcblk0p1 $CARD 2>>$LOG; then\n"
    "    echo \"snapshot: cannot mount the boot partition\" >> $LOG\n"
    "    exit 0\n"
    "fi\n"
    "mkdir -p $CARD/DEBUG 2>>$LOG\n"
    "for f in fix.log probe.log first-stage-publish.txt public.libraries.txt; do\n"
    "    [ -f /metadata/dsh/$f ] && cp /metadata/dsh/$f $CARD/DEBUG/live-$f 2>>$LOG\n"
    "done\n"
    "# 1.5 MiB is the tail that matters and keeps a 64 MiB card safe.\n"
    "tail -c 1572864 /data/local/tmp/dsh-android.log > $CARD/DEBUG/live-logcat.txt 2>>$LOG\n"
    "sync\n"
    "echo \"snapshot: wrote $(ls $CARD/DEBUG/live-* 2>/dev/null | wc -l) files\" >> $LOG\n"
    "umount $CARD 2>>$LOG\n"
    "cp $LOG /tmp/ 2>/dev/null\n"
    "exit 0\n";

const char kLiveSnapshotRc[] =
    "# class core, not late_start: late_start waits for vold.decrypt and nothing\n"
    "# on this device reaches that far yet, so the snapshot would never run.\n"
    "service dsh-live-snapshot /system/bin/sh /metadata/dsh/live-snapshot.sh\n"
    "    class core\n"
    "    oneshot\n"
    "    user root\n"
    "    group root\n";

void PublishLiveSnapshot() {
    PublishTextFile("/metadata/dsh/live-snapshot.sh", kLiveSnapshotSh, 0755);
    PublishTextFile("/vendor/etc/init/94-live-snapshot.rc", kLiveSnapshotRc, 0644);
}

'''

ANCHOR_FUNC = "void PublishRamdiskFstab(DIR* ramdisk) {"
ANCHOR_CALL = "    PublishServiceRcOverrides();\n"

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if MARKER in text:
    print("already patched (live snapshot is in place), nothing to do")
    sys.exit(0)

if ANCHOR_FUNC not in text:
    sys.exit("anchor for function insert not found")
if ANCHOR_CALL not in text:
    sys.exit("anchor for call insert not found")

text = text.replace(ANCHOR_FUNC, NEW_CODE + ANCHOR_FUNC, 1)
text = text.replace(ANCHOR_CALL, ANCHOR_CALL + "    PublishLiveSnapshot();\n", 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "->", text.count("\n") + 1, "lines")