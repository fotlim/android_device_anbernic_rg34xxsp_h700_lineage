#!/usr/bin/env python3
"""Only hand nativeloader the public libraries this build actually has.

sd45's published list did its job -- zygote stopped complaining that
/system/etc/public.libraries.txt was missing -- and immediately hit the next
line, 52 times:

  Abort message: 'Error preloading public library
  libwebviewchromium_plat_support.so: dlopen failed: library
  "libwebviewchromium_plat_support.so" not found'

nativeloader treats a missing public library as fatal, and this build has no
WebView (system/core/rootdir/etc/public.libraries.android.txt names the upstream
set, not this product's).  So the shell script now filters the list against what
is really on the device before installing it and records what it dropped in
fix.log, and the embedded copy loses the WebView line outright.

Idempotent, and it repairs its own first attempt: the first version assembled
the C literal through re.sub(), whose replacement string eats backslash
escapes, and left real newlines inside string literals.  This version slices
the region by hand and writes it back, so running it twice is harmless.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

FIX_SH = """#!/system/bin/sh
# Runs from /vendor/etc/init/96-system-fix.rc at early-init.
LIST=/system/etc/public.libraries.txt
SRC=/metadata/dsh/public.libraries.txt
FILT=/metadata/dsh/list.txt
LOG=/metadata/dsh/fix.log
echo "start" > $LOG
: > $FILT
while read -r lin; do
    case "$lin" in
        ''|'#'*) continue ;;
    esac
    name=${lin%% *}
    if [ -e /system/lib64/$name ] || [ -e /system/lib/$name ] || [ -e /vendor/lib64/$name ] || [ -e /vendor/lib/$name ]; then
        echo "$lin" >> $FILT
    else
        echo "skipping $name: not in this build" >> $LOG
    fi
done < $SRC
echo "kept $(wc -l < $FILT) entries" >> $LOG
if cp $FILT $LIST 2>>$LOG; then
    echo "wrote $LIST directly" >> $LOG
    exit 0
fi
if mount -o rw,remount /system 2>>$LOG && cp $FILT $LIST 2>>$LOG; then
    echo "wrote $LIST after remounting /system rw" >> $LOG
    exit 0
fi
# The overlay fallback is a last resort and it must not change the mode of
# /system/etc. Boot 888a17de proved what happens when it does: the directory
# came out drwx------ (0700 root root), because an overlay takes the mode of its
# mountpoint from the UPPERDIR, and `mkdir -p` created that upperdir with the
# shell's umask instead of 0755. From then on no non-root process could even
# enter /system/etc, so hwservicemanager could not read the framework VINTF
# manifest, media.codec could not read its seccomp policy and zygote could not
# read its font config -- which is one cause for every HIDL abort in the log
# (getTransport() returns EMPTY on an empty manifest, so getService() yields
# null and registerAsService() yields UNKNOWN_ERROR). Hence: create the upperdir
# with an explicit mode, and chmod the mountpoint back to 0755 immediately after
# the mount succeeds, before anything else runs.
mkdir -m 0755 /dev/dsh_upper /dev/dsh_work 2>>$LOG
if mount -t overlay overlay -o lowerdir=/system/etc,upperdir=/dev/dsh_upper,workdir=/dev/dsh_work /system/etc 2>>$LOG; then
    chmod 0755 /system/etc 2>>$LOG
    chmod 0755 /dev/dsh_upper /dev/dsh_work 2>>$LOG
    if cp $FILT $LIST 2>>$LOG; then
        echo "wrote $LIST through an overlay of /system/etc" >> $LOG
        exit 0
    fi
fi
echo "FAILED, see the messages above" >> $LOG
exit 0
"""

HEADER = "const char kFixPublicLibsSh[] =\n"
NEXT = "\nconst char kFixPublicLibsRc[] ="


def c_literal(text):
    out = []
    for line in text.splitlines():
        esc = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append('    "%s\\n"\n' % esc)
    return "".join(out).rstrip("\n") + ";\n"


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        text = f.read()

    if text.count(HEADER) != 1 or text.count(NEXT) != 1:
        sys.exit("anchors: header %d, next %d" % (text.count(HEADER), text.count(NEXT)))

    start = text.index(HEADER)
    end = text.index(NEXT)

    repaired = text[:start] + HEADER + c_literal(FIX_SH) + text[end:]

    webview = '    "libwebviewchromium_plat_support.so\\n"\n'
    if webview in repaired:
        if repaired.count(webview) != 1:
            sys.exit("webview line appears %d times" % repaired.count(webview))
        repaired = repaired.replace(webview, "", 1)

    if repaired == text:
        print("already applied")
        return 0

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(repaired)
    print("the fix script now filters the list against the device")
    return 0


if __name__ == "__main__":
    sys.exit(main())