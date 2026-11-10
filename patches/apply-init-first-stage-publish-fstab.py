#!/usr/bin/env python3
"""Publish the ramdisk fstab into /vendor/etc during first stage.

Why: first stage reads fstab.<hw> straight off the ramdisk root.  SwitchRoot()
then swaps / for the system image, FreeRamdisk() deletes the ramdisk copy, and
second stage init -- plus vold, which is the thing that mounts /data -- go
looking again through fs_mgr_read_fstab_default(), which only checks
/odm/etc/fstab.*, /vendor/etc/fstab.* and /fstab.*.  None of those exist, so
vold has no fstab, /data never gets mounted, and zygote dies with
"Error creating cache dir /data/dalvik-cache/arm64 : No such file or directory".
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

HELPER_ANCHOR = """bool ForceNormalBoot(const std::string& cmdline) {
    return cmdline.find("androidboot.force_normal_boot=1") != std::string::npos;
}

}  // namespace
"""

HELPER = """bool ForceNormalBoot(const std::string& cmdline) {
    return cmdline.find("androidboot.force_normal_boot=1") != std::string::npos;
}

// The ramdisk carries fstab.<hardware> and first stage reads it straight off the
// root filesystem.  SwitchRoot() then replaces / with the system image and
// FreeRamdisk() deletes the ramdisk copy, so by the time second stage init and
// vold look for a fstab again -- fs_mgr_read_fstab_default() only checks
// /odm/etc, /vendor/etc and / -- there is none, /data never gets mounted, and
// zygote dies creating /data/dalvik-cache.  Leave the same file in /vendor/etc
// while /vendor is still reachable, before the ramdisk disappears.
void PublishRamdiskFstab(DIR* ramdisk) {
    if (ramdisk == nullptr) return;
    int dfd = dirfd(ramdisk);
    if (dfd < 0) return;

    std::vector<std::pair<std::string, std::string>> fstabs;
    rewinddir(ramdisk);
    while (dirent* de = readdir(ramdisk)) {
        if (strncmp(de->d_name, "fstab.", 6) != 0) continue;
        int fd = openat(dfd, de->d_name, O_RDONLY | O_CLOEXEC);
        if (fd < 0) {
            PLOG(ERROR) << "Could not open ramdisk " << de->d_name;
            continue;
        }
        std::string content;
        char buf[4096];
        ssize_t n;
        while ((n = read(fd, buf, sizeof(buf))) > 0) {
            content.append(buf, n);
        }
        close(fd);
        fstabs.emplace_back(de->d_name, std::move(content));
    }
    if (fstabs.empty()) {
        LOG(INFO) << "No ramdisk fstab to publish for the second stage";
        return;
    }
    if (access("/vendor/etc", F_OK) != 0) {
        LOG(ERROR) << "No /vendor/etc, cannot publish the fstab for the second stage";
        return;
    }
    if (mount("/vendor", "/vendor", nullptr, MS_REMOUNT, nullptr) != 0) {
        PLOG(ERROR) << "Could not remount /vendor read-write, cannot publish the fstab";
        return;
    }
    for (const auto& [name, content] : fstabs) {
        std::string path = "/vendor/etc/" + name;
        int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        if (fd < 0) {
            PLOG(ERROR) << "Could not create " << path;
            continue;
        }
        ssize_t written = write(fd, content.data(), content.size());
        if (written == static_cast<ssize_t>(content.size())) {
            fsync(fd);
            LOG(INFO) << "Published " << path << " (" << content.size()
                      << " bytes) for the second stage";
        } else {
            PLOG(ERROR) << "Could not write " << path;
        }
        close(fd);
    }
    sync();
    if (mount("/vendor", "/vendor", nullptr, MS_REMOUNT | MS_RDONLY, nullptr) != 0) {
        PLOG(ERROR) << "Could not put /vendor back to read-only";
    }
}

}  // namespace
"""

CALL_ANCHOR = """    if (!DoFirstStageMount()) {
        LOG(FATAL) << "Failed to mount required partitions early ...";
    }
"""

CALL = """    if (!DoFirstStageMount()) {
        LOG(FATAL) << "Failed to mount required partitions early ...";
    }

    // /vendor is mounted and the ramdisk is still reachable through the DIR* we
    // opened before the switch: this is the last moment the fstab can be copied
    // to where the second stage and vold will look for it.
    PublishRamdiskFstab(old_root_dir.get());
"""


def main():
    with open(PATH, encoding="utf-8") as f:
        src = f.read()

    if "PublishRamdiskFstab" in src:
        print("already patched")
        return 0

    if src.count(HELPER_ANCHOR) != 1:
        print("helper anchor not unique/missing")
        return 1
    src = src.replace(HELPER_ANCHOR, HELPER)

    if src.count(CALL_ANCHOR) != 1:
        print("call anchor not unique/missing")
        return 1
    src = src.replace(CALL_ANCHOR, CALL)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(src)
    print("patched", PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())