#!/usr/bin/env python3
"""Add PublishMountRc() and call it once the fstab is published.

The device rc (vendor/etc/init/init.rg34xxsp.rc) has an `on fs` block that
mounts nothing at all, so the second stage never mounts /data: post-fs-data
runs against the read-only system root, /data/dalvik-cache cannot be created,
zygote aborts, and init gives up after four crashes. Publishing an extra rc
file next to the fstab fixes that without rebuilding vendor.img.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

NEW_FUNC = r'''// Nothing in the shipped init rc ever runs mount_all, so /data stays unmounted
// and post-fs-data works against the read-only system root. init imports every
// *.rc under /vendor/etc/init, so a file published there -- next to the fstab,
// on the same overlay -- is enough until vendor.img carries the mount_all
// itself. "on fs" is where a default-mode mount_all has always belonged.
const char kMountDataRc[] =
    "# Written by first-stage init (patches/init-first-stage-publish-ramdisk-fstab).\n"
    "#\n"
    "# The device rc mounts nothing, so /data was never mounted and post-fs-data\n"
    "# ran against the read-only system root: \"mkdir() failed on\n"
    "# /data/dalvik-cache: Read-only file system\", zygote could not create its\n"
    "# cache, and init gave up after four crashes. See meta-first-stage-publish.txt\n"
    "# on /metadata for what this boot managed to publish.\n"
    "on fs\n"
    "    mount_all /vendor/etc/fstab.rg34xxsp\n";

void PublishMountRc() {
    const char* path = "/vendor/etc/init/99-mount-data.rc";
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        pubnote("rc: open %s failed: %d (%s)", path, errno, strerror(errno));
        return;
    }
    size_t len = strlen(kMountDataRc);
    ssize_t written = write(fd, kMountDataRc, len);
    fsync(fd);
    close(fd);
    pubnote("rc: wrote %zd/%zu bytes to %s", written, len, path);
}

'''

OLD_CALL = '    pubnote("done, published=%d", published ? 1 : 0);'
NEW_CALL = ('    if (published) PublishMountRc();\n'
            '    pubnote("done, published=%d", published ? 1 : 0);')


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        text = f.read()

    if "PublishMountRc" in text:
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
    print("added PublishMountRc, file now %d lines" % (text.count("\n") + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())