#!/usr/bin/env python3
"""sd97d: publish mesa + panfrost WITHOUT touching /vendor/lib64 itself.

Boot a1e0aab9 finally got mesa running:

    GLES: Panfrost, Mali G31 (Panfrost), OpenGL ES 2.0 Mesa 20.3.4
    live-ahb-probe.txt: all six steps passed, exit=0   (step 5 used to SIGSEGV)

so the crash is gone. Two problems remain, and this patch fixes the regression it
caused:

1. REGRESSION. sd97 mounted an overlay on /vendor/lib64 so it could create
   /vendor/lib64/dri inside the upper layer. /vendor/lib64/hw already had its
   own overlay mounted from the gfx patch, i.e. a mountpoint inside a lower
   layer -- which overlayfs does not support. Result: /vendor/lib64/hw became
   invisible, and both passthrough HALs that load through hw_get_module died:

       health@2.1-service:  Check failed: passthrough != nullptr
                            Cannot find passthrough implementation of health 2.1
                            HAL for instance default          (abort, every 5 s)
       system_server main:  memtrack_proc_get -> getRawServiceInternal ...
                            (ANR at ActivityManagerService.<init>, 75 s restart loop)

   Fix: do not overlay /vendor/lib64 at all. Everything that used to need it
   goes into /vendor/lib64/hw instead -- that directory is *already* overlaid by
   patches/patch-vendor-graphics.py and is therefore writable, and it is in the
   vendor linker namespace's search path, which is where libglapi.so will be
   found from (libGLES_mesa.so has it as DT_NEEDED).

2. Still open: sampling a minigbm buffer returns black (probe steps 4 and 5
   read 00 00 00 ff). Not a crash any more, so it is a real format/import
   question now, tracked as I-图形-05.

Driver lookup: mesa's DRI2 loader honours the LIBGL_DRIVERS_PATH env var
(src/egl/drivers/dri2/egl_dri2.c:807, search_path_vars), falling back to the
compile-time DEFAULT_DRIVER_DIR=/vendor/lib64/dri. Since /vendor/lib64/dri
cannot be created on a read-only /vendor, this publishes a tiny rc that exports
the path at early-init, before surfaceflinger starts.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

MARKER = "// sd97: publish mesa"
END = "// sd97-end"

CODE = r'''// sd97: publish mesa + panfrost (patches/patch-mesa-publish.py).
// Sources are FLAT files in vendor-overlay/ because new ramdisk subdirectories
// come back ENOTDIR (see docs/ISSUES.md I-工具-09/11).
//
// NOTHING here overlays /vendor/lib64: doing that hid /vendor/lib64/hw -- which
// already has its own overlay -- and killed both passthrough HALs (health
// aborted, memtrack made system_server ANR in ActivityManagerService.<init>).
// Instead the shared objects land in /vendor/lib64/hw, which the gfx patch
// already makes writable and which the vendor linker namespace searches.
static const char kMesaRc[] =
    "on early-init\n"
    "    # sd97: mesa's loader falls back to DEFAULT_DRIVER_DIR=/vendor/lib64/dri,\n"
    "    # which cannot exist on the read-only /vendor. It honours this env var\n"
    "    # instead (src/egl/drivers/dri2/egl_dri2.c:807).\n"
    "    export LIBGL_DRIVERS_PATH /vendor/lib64/hw\n"
    "    # sd103: the fbdev console (fb0, sun4i-drmdrmfb) owns the CRTC and holds\n"
    "    # the DRM master, so the composer's drmSetMaster() fails with EBUSY\n"
    "    # (boot 7bd253be: \"Failed to set DRM master on /dev/dri/card1:\n"
    "    # Device or resource busy\") and every atomic commit is still -EACCES.\n"
    "    # Unbinding the vtconsole makes fbdev release the device. Doing it at\n"
    "    # early-init means it happens before the composer service starts.\n"
    "    write /sys/class/vtconsole/vtcon0/bind 0\n"
    "    write /sys/class/vtconsole/vtcon1/bind 0\n"
    "    write /sys/class/vtconsole/vtcon2/bind 0\n"
    "    write /sys/class/vtconsole/vtcon3/bind 0\n"
    "\n";

// sd107: the minigbm allocator opens card1 first, so the kernel makes it the
// DRM master. patches/patch-gralloc-drop-master.py makes it drop that right
// after open, but drmDropMaster() needs CAP_SYS_ADMIN and the stock rc runs it
// as `system`, so the drop is refused and master stays taken -- the composer's
// drmSetMaster() then gets EBUSY forever and every atomic commit is -EACCES.
// Running the allocator as root lets the drop succeed.
static const char kAllocatorRc[] =
    "service vendor.graphics.allocator-4-0 /vendor/bin/hw/android.hardware.graphics.allocator@4.0-service.minigbm\n"
    "    interface android.hardware.graphics.allocator@4.0::IAllocator default\n"
    "    class hal animation\n"
    "    user root\n"
    "    group graphics drmrpc\n"
    "    capabilities SYS_NICE\n"
    "    onrestart restart surfaceflinger\n"
    "    writepid /dev/cpuset/system-background/tasks\n";

void PublishMesa(int dfd) {
    // Self-diagnosis: show what the ramdisk really holds before copying.
    {
        static const char* kCheck[] = {
            "vendor-overlay",
            "vendor-overlay/lib64",
            "vendor-overlay/lib64/hw",
            "vendor-overlay/mesa_egl.so",
            "vendor-overlay/mesa_glesv2.so",
            "vendor-overlay/mesa_panfrost_dri.so",
            "vendor-overlay/libglapi.so",
        };
        for (const char* p : kCheck) {
            struct stat st;
            int rc = fstatat(dfd, p, &st, 0);
            if (rc == 0) {
                pubnote("mesa: %s = mode 0%o size %lld", p, st.st_mode,
                        static_cast<long long>(st.st_size));
            } else {
                pubnote("mesa: %s stat failed: %d (%s)", p, errno, strerror(errno));
            }
        }
    }

    // /vendor/lib64/egl exists in the vendor image (it held libEGL_swiftshader),
    // so it can be overlaid without touching its parent.
    if (!OverlayDir("/vendor/lib64/egl", "/dev/vg_egl_upper", "/dev/vg_egl_work")) {
        pubnote("mesa: overlay of /vendor/lib64/egl failed");
        return;
    }
    chmod("/vendor/lib64/egl", 0755);
    chmod("/dev/vg_egl_upper", 0755);
    chmod("/dev/vg_egl_work", 0755);

    // Mesa's libGLES_mesa.so shipped under the swiftshader names: the loader
    // dlopens libEGL_<ro.hardware.egl>.so and ro.hardware.egl is "swiftshader".
    CopyAppAt(dfd, "vendor-overlay/mesa_egl.so", "/vendor/lib64/egl",
              "/vendor/lib64/egl/libEGL_swiftshader.so");
    CopyAppAt(dfd, "vendor-overlay/mesa_glesv2.so", "/vendor/lib64/egl",
              "/vendor/lib64/egl/libGLESv2_swiftshader.so");
    // libglapi.so is libGLES_mesa.so's DT_NEEDED; /vendor/lib64/hw is in the
    // vendor namespace search path, so it resolves from there.
    CopyAppAt(dfd, "vendor-overlay/libglapi.so", "/vendor/lib64/hw",
              "/vendor/lib64/hw/libglapi.so");
    // The gallium driver itself; found via LIBGL_DRIVERS_PATH below.
    CopyAppAt(dfd, "vendor-overlay/mesa_panfrost_dri.so", "/vendor/lib64/hw",
              "/vendor/lib64/hw/panfrost_dri.so");
    // sd102: the composer rebuilt with drmSetMaster(). Without it every
    // drmModeAtomicCommit returns -EACCES and the panel keeps the console.
    CopyAppAt(dfd, "vendor-overlay/lib64/hw/hwcomposer.drm_minigbm.so", "/vendor/lib64/hw",
              "/vendor/lib64/hw/hwcomposer.drm_minigbm.so");

    pubnote("mesa: rc exporting LIBGL_DRIVERS_PATH");
}

// sd97-end

'''

CALL_ANCHOR = "    PublishVendorGraphics(dfd);\n"


def strip(text):
    if MARKER not in text:
        return text, False
    start = text.index(MARKER)
    stop = text.index(END, start) + len(END)
    return text[:start] + text[stop:].lstrip("\n"), True


with open(PATH, encoding="utf-8") as f:
    text = f.read()

text, removed = strip(text)
if removed:
    print("removed the previous block")

if CALL_ANCHOR not in text:
    sys.exit("anchor for the call insert not found")

# PublishMesa must run AFTER PublishVendorGraphics: it copies into
# /vendor/lib64/hw, which that function overlays. Insert it right after, and
# keep it after PublishSystemApp for a stable, reproducible order.
if "    PublishMesa(dfd);\n" not in text:
    anchor = "    PublishSystemApp(dfd);\n"
    if anchor in text:
        text = text.replace(anchor, anchor + "    PublishMesa(dfd);\n", 1)
    else:
        text = text.replace(CALL_ANCHOR, CALL_ANCHOR + "    PublishMesa(dfd);\n", 1)

ANCHOR88 = "// sd88-end\n"
ANCHOR_FN = "void PublishRamdiskFstab(DIR* ramdisk) {"
if ANCHOR88 in text:
    text = text.replace(ANCHOR88, ANCHOR88 + "\n" + CODE, 1)
elif ANCHOR_FN in text:
    text = text.replace(ANCHOR_FN, CODE + ANCHOR_FN, 1)
else:
    sys.exit("no anchor for the function insert")

# The rc that exports LIBGL_DRIVERS_PATH has to be written somewhere the first
# stage already writes rc files: /vendor/etc/init. PublishTextFile is declared
# above this point.
RC_CALL = ('    PublishTextFile("/vendor/etc/init/96-mesa.rc", kMesaRc, 0644);\n'
           '    PublishTextFile("/vendor/etc/init/android.hardware.graphics.allocator@4.0-service.minigbm.rc",\n'
           '                    kAllocatorRc, 0644);\n')
if RC_CALL not in text:
    text = text.replace("    pubnote(\"mesa: rc exporting LIBGL_DRIVERS_PATH\");\n",
                        "    pubnote(\"mesa: rc exporting LIBGL_DRIVERS_PATH\");\n" + RC_CALL, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "lines:", len(text.splitlines()))
