#!/usr/bin/env python3
"""Replace PublishRamdiskFstab() with a version that leaves a receipt on
/metadata and falls back to an overlayfs upper layer if /vendor cannot be
remounted read-write.

sd32 published nothing that the second stage could find, and its own log lines
were swallowed by printk ratelimiting (fifteen init lines suppressed in the
same window), so the next build has to answer "did it work, and if not why"
without relying on the kernel log at all.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
ANCHOR = "// The ramdisk carries fstab.<hardware>"
FUNC = "void PublishRamdiskFstab(DIR* ramdisk) {"

NEW = r'''// The ramdisk carries fstab.<hardware> and first stage reads it straight off the
// root filesystem.  SwitchRoot() then replaces / with the system image and
// FreeRamdisk() deletes the ramdisk copy, so by the time second stage init and
// vold look for a fstab again -- fs_mgr_read_fstab_default() only checks
// /odm/etc, /vendor/etc and / -- there is none, /data never gets mounted, and
// zygote dies creating /data/dalvik-cache.  Leave the same file in /vendor/etc
// while /vendor is still reachable, before the ramdisk disappears.
//
// Two things here are learned the hard way:
//   * LOG() is not evidence.  Userspace writes to /dev/kmsg are ratelimited by
//     default ("printk.devkmsg=ratelimit"), and the sd32 run lost fifteen init
//     lines in exactly this window.  So every step also appends to a receipt
//     file on /metadata, which is a real filesystem and survives the reboot.
//   * /vendor is mounted read-only, and a remount may be refused.  If it is,
//     /vendor/etc is overlaid with a tmpfs upper layer instead -- the lowerdir
//     is the directory itself, so nothing is hidden and the partition is never
//     written.
std::string g_publish_receipt;

void pubnote(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void pubnote(const char* fmt, ...) {
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    g_publish_receipt += line;
    g_publish_receipt += '\n';
    LOG(INFO) << "publish: " << line;
}

void pubflush() {
    if (g_publish_receipt.empty()) return;
    for (const char* path : {"/metadata/first-stage-publish.txt"}) {
        int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        if (fd < 0) {
            PLOG(ERROR) << "Could not write the publish receipt to " << path;
            continue;
        }
        const char* p = g_publish_receipt.data();
        size_t left = g_publish_receipt.size();
        while (left > 0) {
            ssize_t n = write(fd, p, left);
            if (n <= 0) break;
            p += n;
            left -= static_cast<size_t>(n);
        }
        fsync(fd);
        close(fd);
    }
    sync();
}

bool PublishOne(const std::string& name, const std::string& content) {
    std::string path = "/vendor/etc/" + name;
    int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        pubnote("open %s failed: %d (%s)", path.c_str(), errno, strerror(errno));
        return false;
    }
    const char* p = content.data();
    size_t left = content.size();
    ssize_t written = 0;
    while (left > 0) {
        ssize_t n = write(fd, p, left);
        if (n <= 0) break;
        p += n;
        left -= static_cast<size_t>(n);
        written += n;
    }
    fsync(fd);
    close(fd);
    bool ok = written == static_cast<ssize_t>(content.size());
    pubnote("wrote %zd/%zu bytes to %s", written, content.size(), path.c_str());
    return ok;
}

void PublishRamdiskFstab(DIR* ramdisk) {
    g_publish_receipt.clear();
    int dfd = ramdisk ? dirfd(ramdisk) : -1;
    pubnote("start, ramdisk=%p dirfd=%d (%s)", ramdisk, dfd,
            dfd < 0 ? strerror(errno) : "ok");
    if (dfd < 0) {
        pubflush();
        return;
    }

    std::vector<std::pair<std::string, std::string>> fstabs;
    rewinddir(ramdisk);
    while (dirent* de = readdir(ramdisk)) {
        if (strncmp(de->d_name, "fstab.", 6) != 0) continue;
        int fd = openat(dfd, de->d_name, O_RDONLY | O_CLOEXEC);
        if (fd < 0) {
            pubnote("openat %s failed: %d (%s)", de->d_name, errno, strerror(errno));
            continue;
        }
        std::string content;
        char buf[4096];
        ssize_t n;
        while ((n = read(fd, buf, sizeof(buf))) > 0) content.append(buf, n);
        close(fd);
        pubnote("found %s (%zu bytes)", de->d_name, content.size());
        fstabs.emplace_back(de->d_name, std::move(content));

        // A copy on /metadata as well, so a failed publish can be recovered
        // and inspected without another build.
        std::string backup = "/metadata/" + fstabs.back().first;
        int bfd = open(backup.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        if (bfd >= 0) {
            write(bfd, fstabs.back().second.data(), fstabs.back().second.size());
            fsync(bfd);
            close(bfd);
            pubnote("copied it to %s", backup.c_str());
        } else {
            pubnote("could not copy to %s: %d (%s)", backup.c_str(), errno,
                    strerror(errno));
        }
    }

    if (fstabs.empty()) {
        pubnote("no fstab.* on the ramdisk, nothing to publish");
        pubflush();
        return;
    }
    if (access("/vendor/etc", F_OK) != 0) {
        pubnote("no /vendor/etc: %d (%s)", errno, strerror(errno));
        pubflush();
        return;
    }

    // Route A: remount /vendor read-write for the duration.
    bool published = false;
    int rc = mount("/vendor", "/vendor", nullptr, MS_REMOUNT, nullptr);
    pubnote("remount /vendor rw rc=%d errno=%d (%s)", rc, rc ? errno : 0,
            rc ? strerror(errno) : "ok");
    if (rc == 0) {
        for (const auto& entry : fstabs) published |= PublishOne(entry.first, entry.second);
        int rc2 = mount("/vendor", "/vendor", nullptr, MS_REMOUNT | MS_RDONLY, nullptr);
        pubnote("remount /vendor ro rc=%d errno=%d (%s)", rc2, rc2 ? errno : 0,
                rc2 ? strerror(errno) : "ok");
    }

    // Route B: overlay /vendor/etc with a tmpfs upper layer.
    if (!published) {
        mkdir("/dev/vendor_etc_upper", 0755);
        mkdir("/dev/vendor_etc_work", 0755);
        int rc3 = mount("overlay", "/vendor/etc", "overlay", 0,
                        "lowerdir=/vendor/etc,upperdir=/dev/vendor_etc_upper,"
                        "workdir=/dev/vendor_etc_work");
        pubnote("overlay /vendor/etc rc=%d errno=%d (%s)", rc3, rc3 ? errno : 0,
                rc3 ? strerror(errno) : "ok");
        if (rc3 == 0) {
            for (const auto& entry : fstabs) published |= PublishOne(entry.first, entry.second);
        }
    }

    for (const auto& entry : fstabs) {
        std::string path = "/vendor/etc/" + entry.first;
        int seen = access(path.c_str(), R_OK);
        pubnote("final access(%s)=%d (%s)", path.c_str(), seen,
                seen == 0 ? "readable" : strerror(errno));
    }
    pubnote("done, published=%d", published ? 1 : 0);
    pubflush();
}

'''


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        text = f.read()

    start = text.index(ANCHOR)
    fstart = text.index(FUNC, start)
    i = text.index("{", fstart)
    depth = 0
    while True:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    end = i + 1

    out = text[:start] + NEW + text[end:]

    if "#include <stdarg.h>" not in out:
        out = out.replace("#include <paths.h>", "#include <paths.h>\n#include <stdarg.h>", 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(out)

    print("rewrote PublishRamdiskFstab: %d chars replaced, file now %d lines"
          % (end - start, out.count("\n") + 1))


if __name__ == "__main__":
    sys.exit(main())