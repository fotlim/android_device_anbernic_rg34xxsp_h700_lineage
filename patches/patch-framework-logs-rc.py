#!/usr/bin/env python3
"""Add PublishLogRc() and call it next to the fstab.

/data mounts, zygote starts at 25.8 s -- and at 27.5 s init is killing its
process group, with not one line from zygote in the kernel log. Its output
goes to logd, and logd's buffers live in RAM: a panic loses everything except
what reached /dev/kmsg. So pipe the log buffers into /dev/kmsg, where
debug-init's follower copies them onto the card every 200 ms.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

NEW_FUNC = r'''// zygote starts and then vanishes without a word in the kernel log: services
// log to logd, and logd keeps its buffers in RAM. A crash takes those buffers
// with it, but anything written to /dev/kmsg survives -- debug-init already
// follows /dev/kmsg onto the card every 200 ms. So publish one more rc that
// pipes the log buffers into kmsg, on the same overlay as the fstab.
const char kFrameworkLogsRc[] =
    "# Written by first-stage init (patches/init-first-stage-publish-ramdisk-fstab).\n"
    "#\n"
    "# zygote starts at 25.8 s and init kills its process group at 27.5 s with\n"
    "# nothing from zygote in the kernel log, because services log to logd and\n"
    "# logd's buffers die with the machine. Rewriting them into /dev/kmsg puts\n"
    "# them where debug-init can copy them to the card before the panic lands.\n"
    "on post-fs-data\n"
    "    exec_background - root root -- /system/bin/logcat -b crash -b main -v brief -f /dev/kmsg\n";

void PublishLogRc() {
    const char* path = "/vendor/etc/init/98-framework-logs.rc";
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        pubnote("logrc: open %s failed: %d (%s)", path, errno, strerror(errno));
        return;
    }
    size_t len = strlen(kFrameworkLogsRc);
    ssize_t written = write(fd, kFrameworkLogsRc, len);
    fsync(fd);
    close(fd);
    pubnote("logrc: wrote %zd/%zu bytes to %s", written, len, path);
}

'''

OLD_CALL = '    if (published) PublishMountRc();'
NEW_CALL = ('    if (published) PublishMountRc();\n'
            '    if (published) PublishLogRc();')


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        text = f.read()

    if "PublishLogRc" in text:
        print("already applied")
        return 0

    anchor = "void PublishRamdiskFstab(DIR* ramdisk) {"
    text = text.replace(anchor, NEW_FUNC + anchor, 1)

    if OLD_CALL not in text:
        print("call site not found", file=sys.stderr)
        return 1
    text = text.replace(OLD_CALL, NEW_CALL, 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("added PublishLogRc, file now %d lines" % (text.count("\n") + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())