#!/usr/bin/env python3
"""sd48: carry the rebuilt graphics modules onto the device through the ramdisk.

The three files this round changes normally live in vendor.img, and vendor.img
lives inside the 4 GiB super partition: changing it means rewriting the whole
card. But first-stage init already publishes files under /vendor without
writing the partition -- it overlays the directory with a tmpfs upper layer kept
on /dev. The same trick carries binaries, so the update stays three files on the
boot partition.

The files travel inside the initramfs (CONFIG_INITRAMFS_SOURCE points at a
directory, so anything under it is packed), in vendor-overlay/.

Why only three files:
  * gralloc.minigbm.so is the module hw_get_module() loads for the composer;
    it links libminigbm_cros_gralloc statically, so it needs the new backend.
  * the mapper passthrough .so is what SurfaceFlinger loads; same reason.
  * the allocator service binary advertises IAllocator/default; same reason.
hwcomposer.drm_minigbm.so is NOT on the list: drm_hwcomposer's minigbm target
only includes cros_gralloc's headers and calls hw_get_module() at runtime, so it
carries no copy of the allocator and needs no rebuild.

The SELinux labels of these paths stay wrong for one more round (vendor_file
instead of the HAL exec type) because file_contexts is compiled into
precompiled_sepolicy inside vendor.img. The board runs permissive, so the
services still start; the label rules are already committed for the next vendor
rebuild.
"""

import sys

SRC = "/root/android/lineage/system/core/init/first_stage_init.cpp"

NEW_CODE = r'''// Graphics modules are the one thing a ramdisk publish cannot write as text:
// they are hundreds of kilobytes of code, and they live in vendor.img, inside
// the 4 GiB super partition where every change costs a whole-card rewrite.
// Carrying them in the initramfs and overlaying /vendor/lib64/hw and
// /vendor/bin/hw keeps the loop at three files on the boot partition. The upper
// layer sits on /dev's tmpfs, like the fstab overlay; overlayfs holds its own
// reference to that tmpfs, so the files stay reachable even after the second
// stage puts a new filesystem on /dev.
struct VendorGraphicsFile {
    const char* ramdisk_path;
    const char* install_path;
    bool executable;
};

const VendorGraphicsFile kVendorGraphicsFiles[] = {
    {"vendor-overlay/lib64/hw/gralloc.minigbm.so",
     "/vendor/lib64/hw/gralloc.minigbm.so", false},
    {"vendor-overlay/lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm.so",
     "/vendor/lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm.so", false},
    {"vendor-overlay/bin/hw/android.hardware.graphics.allocator@4.0-service.minigbm",
     "/vendor/bin/hw/android.hardware.graphics.allocator@4.0-service.minigbm", true},
    // Not a graphics module, but it has the same problem and the same cure: the
    // health@2.1 binderized service dlopen()s its passthrough implementation and
    // dies with "Cannot find passthrough implementation of health 2.1 HAL for
    // instance default" when that .so is absent. The module is
    // android.hardware.health@2.1-impl, whose stem makes it
    // android.hardware.health@2.0-impl-2.1.so; nothing in the product pulled it
    // into vendor.img, so it never shipped. Carrying it on the ramdisk fixes a
    // whole card-flash cycle per attempt.
    {"vendor-overlay/lib64/hw/android.hardware.health@2.0-impl-2.1.so",
     "/vendor/lib64/hw/android.hardware.health@2.0-impl-2.1.so", false},
};

bool OverlayDir(const char* target, const char* upper, const char* work) {
    mkdir(upper, 0755);
    mkdir(work, 0755);
    std::string opts = std::string("lowerdir=") + target + ",upperdir=" + upper +
                       ",workdir=" + work;
    int rc = mount("overlay", target, "overlay", 0, opts.c_str());
    pubnote("gfx: overlay %s rc=%d errno=%d (%s)", target, rc, rc ? errno : 0,
            rc ? strerror(errno) : "ok");
    return rc == 0;
}

bool CopyFromRamdisk(int dfd, const char* src_rel, const char* dst, mode_t mode) {
    int in = openat(dfd, src_rel, O_RDONLY | O_CLOEXEC);
    if (in < 0) {
        pubnote("gfx: %s is not on the ramdisk: %d (%s)", src_rel, errno, strerror(errno));
        return false;
    }
    int out = open(dst, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    if (out < 0) {
        pubnote("gfx: open %s failed: %d (%s)", dst, errno, strerror(errno));
        close(in);
        return false;
    }
    char buf[65536];
    ssize_t n;
    size_t total = 0;
    bool ok = true;
    while ((n = read(in, buf, sizeof(buf))) > 0) {
        ssize_t off = 0;
        while (off < n) {
            ssize_t w = write(out, buf + off, static_cast<size_t>(n - off));
            if (w <= 0) {
                ok = false;
                break;
            }
            off += w;
            total += static_cast<size_t>(w);
        }
        if (!ok) break;
    }
    fsync(out);
    close(out);
    close(in);
    if (ok && chmod(dst, mode) != 0) {
        pubnote("gfx: chmod %s failed: %d (%s)", dst, errno, strerror(errno));
    }
    if (ok && access(dst, R_OK) != 0) {
        pubnote("gfx: %s is not readable after the copy: %d (%s)", dst, errno,
                strerror(errno));
        ok = false;
    }
    pubnote("gfx: copied %s -> %s (%zu bytes, %s)", src_rel, dst, total,
            ok ? "ok" : "failed");
    return ok && total > 0;
}

void PublishVendorGraphics(int dfd) {
    if (dfd < 0) {
        pubnote("gfx: no ramdisk handle, skipping the vendor overlay");
        return;
    }
    const bool lib_ok = OverlayDir("/vendor/lib64/hw", "/dev/vg_lib_upper", "/dev/vg_lib_work");
    const bool bin_ok = OverlayDir("/vendor/bin/hw", "/dev/vg_bin_upper", "/dev/vg_bin_work");
    int copied = 0;
    for (const auto& file : kVendorGraphicsFiles) {
        const bool is_bin = strncmp(file.install_path, "/vendor/bin/", 12) == 0;
        if (is_bin ? !bin_ok : !lib_ok) {
            pubnote("gfx: skipping %s, its overlay is not mounted", file.install_path);
            continue;
        }
        if (CopyFromRamdisk(dfd, file.ramdisk_path, file.install_path,
                            file.executable ? 0755 : 0644)) {
            copied++;
        }
    }
    pubnote("gfx: vendor graphics files published: %d", copied);
}

'''

ANCHOR_FUNC = "void PublishRamdiskFstab(DIR* ramdisk) {"
ANCHOR_CALL = "    if (published) PublishSystemFix();\n"

with open(SRC, encoding="utf-8") as f:
    text = f.read()

# The block itself is insert-only, but the file list inside it has to be able to
# grow later. Without this, adding a fourth entry to kVendorGraphicsFiles in this
# script had no effect at all: apply.sh said "already patched" and the binary came
# out byte-identical (the health@2.1 passthrough .so never got published).
ARRAY_ANCHOR = "const VendorGraphicsFile kVendorGraphicsFiles[] = {"

# Entries added after the block itself was first applied. Each is inserted only
# if its install path is not in the source yet; before this loop existed, adding
# an entry here had no effect at all (apply.sh said "already patched" and the
# binary came out byte-identical).
EXTRA_ENTRIES = [
    ("/vendor/lib64/hw/android.hardware.health@2.0-impl-2.1.so",
     """    {"vendor-overlay/lib64/hw/android.hardware.health@2.0-impl-2.1.so",
     "/vendor/lib64/hw/android.hardware.health@2.0-impl-2.1.so", false},
"""),
    ("/vendor/lib64/hw/android.hardware.memtrack@1.0-impl.so",
     """    // android.hardware.memtrack@1.0-service is a passthrough wrapper as well:
    // it dlopen()s this .so and, when it is missing, prints
    //   "Could not get passthrough implementation for
    //    android.hardware.memtrack@1.0::IMemtrack/default"
    // and exits with status 1 every 5 s (boot d85ef342). The impl module is
    // now in PRODUCT_PACKAGES too, but vendor.img only picks it up on a full
    // rebuild, so carry it on the ramdisk meanwhile.
    {"vendor-overlay/lib64/hw/android.hardware.memtrack@1.0-impl.so",
     "/vendor/lib64/hw/android.hardware.memtrack@1.0-impl.so", false},
"""),
    ("/vendor/bin/hw/android.hardware.graphics.composer@2.1-service",
     """    // sd75: SurfaceFlinger is a composer@2.1 client (boot df79ab2d:
    // hwservicemanager "Since android.hardware.graphics.composer@2.1::
    // IComposer/default is not registered" + init "Could not find ... for
    // ctl.interface_start"), but vendor.img only carries the 2.2 service.
    // tools/build-hwc-probe.sh builds the 2.1 one from this tree; its rc is
    // published by patches/patch-composer-rc.py.
    {"vendor-overlay/bin/hw/android.hardware.graphics.composer@2.1-service",
     "/vendor/bin/hw/android.hardware.graphics.composer@2.1-service", true},
"""),
    ("/vendor/bin/hw/dsh-hwcprobe",
     """    // dsh-hwcprobe walks hw_get_module -> methods->open -> getCapabilities ->
    // getFunction and prints every step to stdout, because not one byte of the
    // composer service's own ALOG output reaches the snapshot's logcat. See
    // patches/hwc-probe/probe.c.
    {"vendor-overlay/bin/hw/dsh-hwcprobe",
     "/vendor/bin/hw/dsh-hwcprobe", true},
"""),
    ("/vendor/bin/hw/dsh-wifi-stub",
     """    // sd77: a do-nothing "wifi" binder (patches/wifi-stub). Without an owner for
    // that binder, Context.getSystemService(WIFI_SERVICE) is null and Lineage's
    // ProfileManagerService NPEs at boot phase 550, taking system_server down
    // (boot a95db9e0). The rc is published by patches/patch-wifi-stub-rc.py.
    {"vendor-overlay/bin/hw/dsh-wifi-stub",
     "/vendor/bin/hw/dsh-wifi-stub", true},
"""),
    ("/vendor/lib64/hw/memtrack.default.so",
     """    // memtrack.default.so is the empty legacy HAL (patches/memtrack-stub) that
    // lets android.hardware.memtrack@1.0-service register at all. Without it
    // hw_get_module("memtrack") fails, the HIDL service never publishes, and
    // Debug.getMemoryInfo -> memtrack_proc_get blocks system_server's main
    // thread inside ActivityManagerService.<init> until the Watchdog kills it
    // 60 s later (boot b8c70ff4: "Blocked in handler on main thread (main)").
    {"vendor-overlay/lib64/hw/memtrack.default.so",
     "/vendor/lib64/hw/memtrack.default.so", false},
"""),
    # NOTE: keep new entries LAST.  The insert loop below puts every entry it
    # adds at the end of the array, so an entry placed mid-list makes an
    # incremental run and a full replay produce different layouts -- which is
    # what tools/verify-patch-chain.sh reported ("+7 lines at 488, -7 at 502")
    # the first time dsh-glprobe was added in the middle.
    ("/vendor/bin/hw/dsh-glprobe",
     """    // sd93: does SwiftShader itself work on this SoC, or is the memory we hand
    // it through minigbm the problem? SurfaceFlinger dies in
    // sw::Renderer::executeTask(int)+324 the first time it is asked to
    // composite a layer (boot 35526bea), and both causes need opposite fixes.
    // See patches/gl-probe/probe.c and the live snapshot's live-gl-probe.txt.
    {"vendor-overlay/bin/hw/dsh-glprobe",
     "/vendor/bin/hw/dsh-glprobe", true},
"""),
]

if "PublishVendorGraphics" in text:
    added = []
    for probe, entry in EXTRA_ENTRIES:
        if probe in text:
            continue
        start = text.find(ARRAY_ANCHOR)
        if start < 0:
            sys.exit("kVendorGraphicsFiles not found although PublishVendorGraphics is present")
        end = text.find("};", start)
        if end < 0:
            sys.exit("end of kVendorGraphicsFiles not found")
        text = text[:end] + entry + text[end:]
        added.append(probe.rsplit("/", 1)[-1])
    if not added:
        print("already patched: every extra entry is listed, nothing to do")
        sys.exit(0)
    with open(SRC, "w", encoding="utf-8") as f:
        f.write(text)
    print("added to kVendorGraphicsFiles: " + ", ".join(added))
    sys.exit(0)

if ANCHOR_FUNC not in text:
    sys.exit("anchor for function insert not found")
if ANCHOR_CALL not in text:
    sys.exit("anchor for call insert not found")

text = text.replace(ANCHOR_FUNC, NEW_CODE + ANCHOR_FUNC, 1)
text = text.replace(ANCHOR_CALL, ANCHOR_CALL + "    PublishVendorGraphics(dfd);\n", 1)

with open(SRC, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", SRC, "->", text.count("\n") + 1, "lines")
