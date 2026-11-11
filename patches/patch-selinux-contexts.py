#!/usr/bin/env python3
"""sd49: give the second stage a service_contexts table it can reach.

Why v8 exists
-------------
The sd48 evidence pins the failure down further than v7 assumed.  In
plat_service_contexts the very last line is a wildcard:

    *   u:object_r:default_android_service:s0

service_lookup() matches with strcmp() on the key *or* on "*", so if that table
had been loaded, *no* name could ever produce "No match".  Yet every name did --
vold 489 times, and 40 more on the hwservice side.  The vendor tables have no
wildcard at all.  Therefore servicemanager opened its handle with the vendor
table only: access() on the platform path failed on the device.

So this block does two things:

  * it records access()/stat() for every candidate path, with errno, so the next
    boot says out loud which of the eight paths the device can see;
  * when merging it drops a platform line whose key the vendor file already
    has.  nodups_specs() treats "same key, same context" as a warning but
    "same key, different context" as fatal -- and a fatal table makes
    selabel_open() return NULL, which would abort servicemanager.  Keeping the
    vendor entry, which was generated for this device, avoids that.

The script is idempotent: it replaces its own block if the markers are already
in the file, and inserts both the block and its call site otherwise.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

BEGIN = "// ---- sd49: give the second stage a service_contexts table it can reach -------"
END = "// ---- end sd49 ---------------------------------------------------------------"

NEW = r'''// ---- sd49: give the second stage a service_contexts table it can reach -------
// servicemanager resolves every service name through service_contexts and, on
// this build, every lookup fails, so every binder registration is denied: vold
// cannot publish, vdc waits 50 s and exits 22, post-fs-data never finishes and
// class_start hal is never reached.  No SurfaceFlinger, no zygote, no frame.
//
// The platform table ends with a "*" wildcard, so a loaded platform table can
// never produce "No match" for any name.  The vendor tables have no wildcard.
// Every name failed, therefore the platform path was never added to the option
// list: access() on it failed.  Record each candidate path with its errno, and
// make the vendor tables self-sufficient by merging the platform entries into
// them -- /vendor is reachable for the whole boot, the platform paths are not.
static const char* kServiceCandidates[] = {
    "/system/etc/selinux/plat_service_contexts",
    "/etc/selinux/plat_service_contexts",
    "/plat_service_contexts",
    "/vendor/etc/selinux/vendor_service_contexts",
    "/vendor_service_contexts",
    "/vendor/etc/selinux/nonplat_service_contexts",
    "/nonplat_service_contexts",
};

static const char* kHwServiceCandidates[] = {
    "/system/etc/selinux/plat_hwservice_contexts",
    "/etc/selinux/plat_hwservice_contexts",
    "/plat_hwservice_contexts",
    "/vendor/etc/selinux/vendor_hwservice_contexts",
    "/vendor_hwservice_contexts",
};

static void ProbeContextPaths(const char* const* paths, size_t n) {
    for (size_t i = 0; i < n; i++) {
        struct stat st;
        int acc = access(paths[i], R_OK);
        int acc_errno = errno;
        int st_rc = stat(paths[i], &st);
        if (st_rc == 0) {
            pubnote("ctx: probe %s: access=%d(%s) stat=ok size=%lld mode=0%o",
                    paths[i], acc, acc == 0 ? "ok" : strerror(acc_errno),
                    (long long)st.st_size, (unsigned)(st.st_mode & 07777));
        } else {
            pubnote("ctx: probe %s: access=%d(%s) stat=%d(%s)", paths[i], acc,
                    acc == 0 ? "ok" : strerror(acc_errno), st_rc, strerror(errno));
        }
    }
}

static bool SlurpFile(const char* path, std::string* out) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        pubnote("ctx: %s: open failed errno=%d (%s)", path, errno, strerror(errno));
        return false;
    }
    char buf[16384];
    ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) out->append(buf, static_cast<size_t>(n));
    close(fd);
    size_t lines = 0;
    for (char c : *out) lines += (c == '\n');
    pubnote("ctx: %s: %zu bytes, %zu lines", path, out->size(), lines);
    return !out->empty();
}

static bool SpillFile(const char* path, const std::string& data) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        pubnote("ctx: %s: create failed errno=%d (%s)", path, errno, strerror(errno));
        return false;
    }
    size_t off = 0;
    while (off < data.size()) {
        ssize_t n = write(fd, data.data() + off, data.size() - off);
        if (n <= 0) {
            pubnote("ctx: %s: write failed errno=%d at %zu", path, errno, off);
            close(fd);
            return false;
        }
        off += static_cast<size_t>(n);
    }
    fsync(fd);
    close(fd);
    chmod(path, 0644);
    bool ok = access(path, R_OK) == 0;
    pubnote("ctx: %s: wrote %zu bytes (%s)", path, data.size(), ok ? "readable" : "NOT readable");
    return ok;
}

// "key context" -- the key is the first whitespace-delimited field.
static std::string KeyOf(const std::string& line) {
    size_t end = line.find_first_of(" \t");
    return end == std::string::npos ? line : line.substr(0, end);
}

static bool HasKey(const std::string& haystack, const std::string& key) {
    size_t pos = 0;
    while (pos < haystack.size()) {
        size_t nl = haystack.find('\n', pos);
        if (nl == std::string::npos) nl = haystack.size();
        std::string line = haystack.substr(pos, nl - pos);
        pos = nl + 1;
        if (line.empty() || line[0] == '#') continue;
        if (KeyOf(line) == key) return true;
    }
    return false;
}

// Merge the platform entries into the vendor table, dropping platform lines
// whose key the vendor file already carries: nodups_specs() fails the whole
// table when one key appears with two different contexts, and a failed table
// makes selabel_open() return NULL, which would abort servicemanager.
static void MirrorOneTable(const char* plat_abs, const char* plat_root,
                           const char* vendor_path) {
    std::string plat;
    if (!SlurpFile(plat_abs, &plat) && !SlurpFile(plat_root, &plat)) {
        pubnote("ctx: no platform table for %s, leaving it alone", vendor_path);
        return;
    }
    std::string vend;
    SlurpFile(vendor_path, &vend);

    std::string merged;
    size_t kept = 0, skipped = 0;
    size_t pos = 0;
    while (pos < plat.size()) {
        size_t nl = plat.find('\n', pos);
        if (nl == std::string::npos) nl = plat.size();
        std::string line = plat.substr(pos, nl - pos);
        pos = nl + 1;
        if (line.empty()) continue;
        if (line[0] == '#') continue;
        if (HasKey(vend, KeyOf(line))) {
            skipped++;
            continue;
        }
        merged += line;
        merged.push_back('\n');
        kept++;
    }
    merged += vend;
    if (!merged.empty() && merged.back() != '\n') merged.push_back('\n');
    pubnote("ctx: %s: keeping %zu platform lines, %zu already in vendor", vendor_path,
            kept, skipped);
    SpillFile(vendor_path, merged);
}

static void PublishSelinuxContexts() {
    ProbeContextPaths(kServiceCandidates, sizeof(kServiceCandidates) / sizeof(kServiceCandidates[0]));
    ProbeContextPaths(kHwServiceCandidates, sizeof(kHwServiceCandidates) / sizeof(kHwServiceCandidates[0]));

    if (mkdir("/dev/vs_upper", 0755) != 0 && errno != EEXIST)
        pubnote("ctx: mkdir upper errno=%d", errno);
    if (mkdir("/dev/vs_work", 0755) != 0 && errno != EEXIST)
        pubnote("ctx: mkdir work errno=%d", errno);
    if (mount("overlay", "/vendor/etc/selinux", "overlay", 0,
              "lowerdir=/vendor/etc/selinux,upperdir=/dev/vs_upper,workdir=/dev/vs_work") != 0) {
        pubnote("ctx: overlay /vendor/etc/selinux failed errno=%d (%s)", errno, strerror(errno));
        return;
    }
    pubnote("ctx: overlay /vendor/etc/selinux ok");
    MirrorOneTable("/system/etc/selinux/plat_service_contexts",
                   "/etc/selinux/plat_service_contexts",
                   "/vendor/etc/selinux/vendor_service_contexts");
    MirrorOneTable("/system/etc/selinux/plat_hwservice_contexts",
                   "/etc/selinux/plat_hwservice_contexts",
                   "/vendor/etc/selinux/vendor_hwservice_contexts");
}
// ---- end sd49 ---------------------------------------------------------------
'''


# Where the block goes, and where the call goes, on a tree that has never seen
# either version of this patch.
ANCHOR_FUNC = "void PublishRamdiskFstab(DIR* ramdisk) {"
ANCHOR_CALL = "    PublishVendorGraphics(dfd);\n"
CALL_LINE = "    PublishSelinuxContexts();\n"


def main() -> int:
    with open(PATH, encoding="utf-8") as f:
        src = f.read()

    block = NEW.strip("\n")

    begin = src.find(BEGIN)
    if begin >= 0:
        # v7 (or an earlier v8) is already in the tree: swap the block in place.
        end = src.find(END, begin)
        if end < 0:
            print("END marker not found", file=sys.stderr)
            return 1
        end += len(END)
        out = src[:begin] + block + src[end:]
        print("replaced block: %d -> %d bytes, %d -> %d lines"
              % (end - begin, len(block), src.count("\n"), out.count("\n")))
    else:
        # Fresh tree: insert the block and its call site ourselves.
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
