#!/usr/bin/env python3
"""Put /system/etc/public.libraries.txt on a machine whose system image lacks it.

sd44 finally handed over the reason.  The phone's own log (harvested from
/data/local/tmp/dsh-android.log) says, ten times:

  F DEBUG : pid: 3524, tid: 3524, name: main  >>> zygote64 <<<
  F DEBUG : Abort message: 'Error reading public native library list from
            "/system/etc/public.libraries.txt": No such file or directory'
  F DEBUG : #05 pc ... libnativeloader.so (android::nativeloader::
            preloadable_public_libraries()+80)

So zygote aborts before it ever becomes a zygote, SurfaceFlinger never gets a
client, nothing is drawn, and the panel keeps fbcon's last frame.  The file is
missing because this product does not copy it: the only rule that installs it
lives in build/make/target/product/media_system.mk (system/core/rootdir/etc/
public.libraries.android.txt -> system/etc/public.libraries.txt) and our
inheritance never includes it.

Rebuilding the system image would mean writing the 4 GiB super partition
again.  Instead, first stage leaves the list and a small shell script on
/metadata -- a real rw filesystem that survives SwitchRoot -- and publishes one
rc that runs the script at early-init, long before zygote starts.  The script
tries the direct write first, then a remount, then an overlay of /system/etc
with a tmpfs upper layer, so the partition is never written unless it says yes.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

LIB_LIST = """# See https://android.googlesource.com/platform/ndk/+/master/docs/PlatformApis.md
libandroid.so
libaaudio.so
libamidi.so
libbinder_ndk.so
libc.so
libcamera2ndk.so
libdl.so
libEGL.so
libGLESv1_CM.so
libGLESv2.so
libGLESv3.so
libicui18n.so
libicuuc.so
libjnigraphics.so
liblog.so
libmediandk.so
libm.so
libnativewindow.so
libneuralnetworks.so nopreload
libOpenMAXAL.so
libOpenSLES.so
libRS.so
libstdc++.so
libsync.so
libvulkan.so
libwebviewchromium_plat_support.so
libz.so
"""

FIX_SH = """#!/system/bin/sh
# Runs from /vendor/etc/init/96-system-fix.rc at early-init.
LIST=/system/etc/public.libraries.txt
SRC=/metadata/dsh/public.libraries.txt
LOG=/metadata/dsh/fix.log
echo "start" > $LOG
if cp $SRC $LIST 2>>$LOG; then
    echo "wrote $LIST directly" >> $LOG
    exit 0
fi
if mount -o rw,remount /system 2>>$LOG && cp $SRC $LIST 2>>$LOG; then
    echo "wrote $LIST after remounting /system rw" >> $LOG
    exit 0
fi
mkdir -p /dev/dsh_upper /dev/dsh_work 2>>$LOG
if mount -t overlay overlay -o lowerdir=/system/etc,upperdir=/dev/dsh_upper,workdir=/dev/dsh_work /system/etc 2>>$LOG \
        && cp $SRC $LIST 2>>$LOG; then
    echo "wrote $LIST through an overlay of /system/etc" >> $LOG
    exit 0
fi
echo "FAILED, see the messages above" >> $LOG
exit 0
"""

FIX_RC = """on early-init
    exec - root root -- /system/bin/sh /metadata/dsh/fix-public-libs.sh
"""


def c_literal(text):
    out = []
    for line in text.splitlines(keepends=True):
        esc = line.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        out.append('    "%s"\n' % esc)
    return "".join(out).rstrip("\n") + ";\n"


FUNC = '''// zymote -> zygote64 aborting on a missing public libraries list has one
// more cause worth naming: this product never inherits media_system.mk, the
// only rule that installs system/etc/public.libraries.txt.  The file is left
// on /metadata with a script, and the script runs at early-init through one
// published rc, long before zygote is forked.
const char kPublicLibraries[] =
%s
const char kFixPublicLibsSh[] =
%s
const char kFixPublicLibsRc[] =
%s
bool PublishTextFile(const char* path, const char* content, mode_t mode) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    if (fd < 0) {
        pubnote("fix: open %%s failed: %%d (%%s)", path, errno, strerror(errno));
        return false;
    }
    size_t len = strlen(content), written = 0;
    while (written < len) {
        ssize_t n = write(fd, content + written, len - written);
        if (n <= 0) break;
        written += (size_t)n;
    }
    fsync(fd);
    close(fd);
    pubnote("fix: wrote %%zu/%%zu bytes to %%s", written, len, path);
    return written == len;
}

void PublishSystemFix() {
    mkdir("/metadata/dsh", 0755);
    bool ok = PublishTextFile("/metadata/dsh/public.libraries.txt", kPublicLibraries, 0644);
    ok = PublishTextFile("/metadata/dsh/fix-public-libs.sh", kFixPublicLibsSh, 0755) && ok;
    ok = PublishTextFile("/vendor/etc/init/96-system-fix.rc", kFixPublicLibsRc, 0644) && ok;
    pubnote("fix: public libraries list staged, %%s", ok ? "ok" : "with errors");
}

'''

ANCHOR = "void PublishRamdiskFstab(DIR* ramdisk) {"
CALL_ANCHOR = "    if (published) PublishGraphicsRc();\n"


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        text = f.read()

    if "kPublicLibraries" in text:
        print("already applied")
        return 0
    if text.count(ANCHOR) != 1 or text.count(CALL_ANCHOR) != 1:
        sys.exit("anchors not found exactly once")

    func = FUNC % (c_literal(LIB_LIST), c_literal(FIX_SH), c_literal(FIX_RC))
    text = text.replace(ANCHOR, func + ANCHOR, 1)
    text = text.replace(CALL_ANCHOR, CALL_ANCHOR + "    if (published) PublishSystemFix();\n", 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("public libraries list plus its early-init fix are published")
    return 0


if __name__ == "__main__":
    sys.exit(main())