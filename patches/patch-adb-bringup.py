#!/usr/bin/env python3
"""sd113: bring up ADB over USB so debugging stops depending on card dumps.

The handheld has no touchscreen, so the "Allow USB debugging?" dialog cannot be
tapped. Secure adb therefore has to be satisfied head-less: the host's public key
is dropped on the card's FAT boot partition as `adbkey.pub` and installed into
/data/misc/adb/adb_keys before adbd is started, which makes the PC "already
authorized" with no prompt.

This publishes a class-core root one-shot (same mechanism the live snapshot
already uses) that:
  * waits for sys.boot_completed,
  * mounts /dev/block/mmcblk0p1 (the FAT boot partition) read-only and copies
    adbkey.pub -> /data/misc/adb/adb_keys (system:shell 0640),
  * sets adb_enabled, switches persist/sys.usb.config to adb, sets
    service.adb.tcp.port=5555, and starts adbd,
  * logs the resulting sys.usb.state / ffs.ready / init.svc.adbd so the next
    card dump explains a failure even if USB does not enumerate.

It runs at class core (late_start waits for vold.decrypt which never happens on
this device) and before the live snapshot's own mount of mmcblk0p1.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishAdbBringup"

ADB_SH = """#!/system/bin/sh
LOG=/metadata/dsh/adb-bringup.log
echo "adb-bringup: start" > $LOG
n=0
while [ $n -lt 120 ]; do
    [ "$(getprop sys.boot_completed)" = "1" ] && break
    n=$((n+1)); sleep 1
done
echo "adb-bringup: boot_completed after ${n}s" >> $LOG
CARD=/mnt/dshadb
got=0
i=0
while [ $i -lt 15 ]; do
    mkdir -p $CARD
    if mount -t vfat -o ro /dev/block/mmcblk0p1 $CARD 2>>$LOG; then
        got=1; break
    fi
    i=$((i+1)); sleep 2
done
if [ $got -eq 1 ]; then
    if [ -f $CARD/adbkey.pub ]; then
        mkdir -p /data/misc/adb
        cat $CARD/adbkey.pub > /data/misc/adb/adb_keys
        chown system:shell /data/misc/adb/adb_keys 2>>$LOG
        chmod 640 /data/misc/adb/adb_keys 2>>$LOG
        echo "adb-bringup: installed adbkey.pub" >> $LOG
    else
        echo "adb-bringup: no adbkey.pub on card (adb will be unauthorised)" >> $LOG
    fi
    umount $CARD 2>>$LOG
else
    echo "adb-bringup: could not mount boot partition for adbkey.pub" >> $LOG
fi
settings put global adb_enabled 1 >> $LOG 2>&1
setprop persist.sys.usb.config adb >> $LOG 2>&1
setprop sys.usb.config adb >> $LOG 2>&1
setprop service.adb.tcp.port 5555 >> $LOG 2>&1
setprop ctl.start adbd >> $LOG 2>&1
sleep 3
echo "adb-bringup: done usb.config=$(getprop sys.usb.config) state=$(getprop sys.usb.state) ffs=$(getprop sys.usb.ffs.ready) adbd=$(getprop init.svc.adbd)" >> $LOG
"""

ADB_RC = """# class core: late_start waits for vold.decrypt, which this device never reaches.
service dsh-adb-bringup /system/bin/sh /metadata/dsh/dsh-adb-bringup.sh
    class core
    oneshot
    user root
    group root
"""

NEW_CODE = r'''// sd113: install the host adb key off the card and bring up adbd over USB.
// See patches/patch-adb-bringup.py.
const char kAdbBringupSh[] =
%s
const char kAdbBringupRc[] =
%s
void PublishAdbBringup() {
    PublishTextFile("/metadata/dsh/dsh-adb-bringup.sh", kAdbBringupSh, 0755);
    PublishTextFile("/vendor/etc/init/92-dsh-adb-bringup.rc", kAdbBringupRc, 0644);
}

'''


def c_literal(text):
    out = []
    for line in text.splitlines():
        esc = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append('    "%s\\n"\n' % esc)
    return "".join(out).rstrip("\n") + ";\n"


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        print("already patched (PublishAdbBringup present), nothing to do")
        return 0

    anchor = "void PublishRamdiskFstab(DIR* ramdisk) {"
    if anchor not in text:
        sys.exit("anchor for function insert not found")

    text = text.replace(anchor,
                        (NEW_CODE % (c_literal(ADB_SH), c_literal(ADB_RC))) + anchor,
                        1)

    if "    PublishSkipSuw();\n" in text:
        text = text.replace("    PublishSkipSuw();\n",
                            "    PublishSkipSuw();\n    PublishAdbBringup();\n", 1)
    elif "    PublishLiveSnapshot();\n" in text:
        text = text.replace("    PublishLiveSnapshot();\n",
                            "    PublishLiveSnapshot();\n    PublishAdbBringup();\n", 1)
    else:
        sys.exit("anchor for call insert not found")

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("patched", PATH, "->", text.count("\n") + 1, "lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
