#!/usr/bin/env python3
"""Mirror the parts of Android's log that a card pull can actually carry.

sd43's run gave the machine a stable six-minute life (the watchdog is dead for
good) but the reason zygote aborts 122 times was written to
/data/local/tmp/dsh-android.log -- which nobody can read while /data is on the
card the phone is booting from.  So duplicate the *small* pieces into
/dev/kmsg, where the initramfs follower carries them off:

  - the crash buffer, which holds crash_dump's "Abort message: ..." and is
    written once per abort, not continuously;
  - SurfaceFlinger, hwcomposer and libc lines from main/system.

The flooding that made sd42 unusable came from streaming the whole main and
system buffers into printk. Tag-filtered streams are a few hundred lines, and
with drm.debug gone the kernel log is quiet enough to keep up.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

DATA_LINE = '"    exec_background - root root -- /system/bin/logcat -b main -b system -b crash -v threadtime -r 4096 -n 2 -f /data/local/tmp/dsh-android.log\\n";'

EXTRA = (
    '"on post-fs-data\\n"\n'
    '    "    exec_background - root root -- /system/bin/logcat -b crash -v threadtime -f /dev/kmsg\\n"\n'
    '"on post-fs-data\\n"\n'
    '    "    exec_background - root root -- /system/bin/logcat -b main -b system -v threadtime"\n'
    '    " -s SurfaceFlinger -s hwcomposer -s libc -s DEBUG -f /dev/kmsg\\n";'
)

MARKER = "-b crash -v threadtime -f /dev/kmsg"
MARK_START = "// zygote starts and then vanishes without a word in the kernel log"


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        print("already applied")
        return 0
    if text.count(DATA_LINE) != 1:
        sys.exit("the /data logcat line appears %d times, refusing"
                 % text.count(DATA_LINE))

    text = text.replace(DATA_LINE, DATA_LINE + "\n" + EXTRA, 1)

    if MARK_START in text:
        text = text.replace(
            MARK_START,
            "// The complete log goes to /data/local/tmp/dsh-android.log, but\n"
            "// /data is on the card the machine boots from, so the crash buffer\n"
            "// and the graphics tags are duplicated into /dev/kmsg, where\n"
            "// debug-init carries them to the same card's DEBUG directory.\n"
            "// zygote starts and then vanishes without a word in the kernel log",
            1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("crash buffer and the graphics tags now also go to /dev/kmsg")
    return 0


if __name__ == "__main__":
    sys.exit(main())