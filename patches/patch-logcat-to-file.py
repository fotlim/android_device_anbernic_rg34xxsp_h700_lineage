#!/usr/bin/env python3
"""Point the framework's logcat at /data instead of /dev/kmsg.

sd41/sd42 put Android's log buffers into /dev/kmsg so the initramfs follower
could carry them to the card. Two problems showed up on the machine:

  - printk made it slow enough that logd dropped records wholesale:
    "logd: Skipping 182 entries from slow reader, pid 226" -- pid 226 is our
    logcat, and everything we most wanted (what SurfaceFlinger and the
    composer HAL said) was in the skipped window;
  - every one of those records also went through fbcon, which is what made
    the panel show a scrolling console while Android was booting.

/data is a real ext4 with room to spare, and harvest_data() already collects
files from it at the next boot (local/tmp is in its list now, and copy_capped()
keeps the tail of a large log rather than the head). So: write the log to
/data/local/tmp/dsh-android.log and leave the kernel log for the kernel.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

OLD_LINE = '"    exec_background - root root -- /system/bin/logcat -b crash -b main -v brief -f /dev/kmsg\\n";'
NEW_LINE = '"    exec_background - root root -- /system/bin/logcat -b main -b system -b crash -v threadtime -r 4096 -n 2 -f /data/local/tmp/dsh-android.log\\n";'

OLD_COMMENT = """// zygote starts and then vanishes without a word in the kernel log: services
// log to logd, and logd keeps its buffers in RAM. A crash takes those buffers
// with it, but anything written to /dev/kmsg survives -- debug-init already
// follows /dev/kmsg onto the card every 200 ms. So publish one more rc that
// pipes the log buffers into kmsg, on the same overlay as the fstab."""
NEW_COMMENT = """// zygote starts and then vanishes without a word in the kernel log: services
// log to logd, and logd keeps its buffers in RAM. /dev/kmsg was the first
// choice, but printk is slow enough that logd dropped hundreds of records
// ("Skipping 182 entries from slow reader") and every line repainted the
// panel through fbcon. /data/local/tmp is a real ext4 and debug-init picks it
// up at the next boot, so write the log there and keep kmsg for the kernel."""


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        text = f.read()

    if "dsh-android.log" in text:
        print("already applied")
        return 0
    if text.count(OLD_LINE) != 1:
        sys.exit("logcat line appears %d times, refusing" % text.count(OLD_LINE))

    text = text.replace(OLD_LINE, NEW_LINE, 1)
    text = text.replace(OLD_COMMENT, NEW_COMMENT, 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("logcat now writes /data/local/tmp/dsh-android.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())