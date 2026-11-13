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

# Every policy in that directory ends with two @include lines:
#     @include /system/etc/seccomp_policy/crash_dump.<arch>.policy
#     @include /system/etc/seccomp_policy/code_coverage.<arch>.policy
# libminijail fopen()s them just fine and then reports 'getmultiline()
# failed', because compile_file() checks errno only AFTER the getline loop has
# hit EOF (syscall_filter.c:730) and getline does not clear errno. A stale
# EINVAL makes a healthy, 68-byte, comments-only file look unreadable,
# compile_filter() fails, die() aborts the service, and the HAL then restarts
# every 5 s dragging its neighbours with it.
#
# Boot 702a1e18 proved it was never the mremap constant; boot d85ef342 proved
# this fix works (61 lines, no includes, vendor.media.omx 58 -> 0 restarts).
# mediaextractor.policy carries the same two lines, so do the whole directory.
#
# THIS MUST RUN BEFORE ANY 'exit 0' BELOW. Boot 9ac27bf6 lost it that way: the
# first stage now overlays /system/etc, so `cp $FILT $LIST` succeeded on the
# direct path and the script exited before reaching this block -- the policy
# went back to 63 lines with both includes and vendor.media.omx aborted 159
# times. Defining it as a function and calling it first makes the order
# independent of which write path happens to work.
fix_policies() {
    for POL in /system/etc/seccomp_policy/*.policy; do
        [ -f "$POL" ] || continue
        have=$(grep -c '^@include' "$POL" 2>/dev/null)
        case "$have" in ''|0) continue ;; esac
        grep -v '^@include' "$POL" > /dev/pol.in 2>>$LOG
        if [ -s /dev/pol.in ] && cp /dev/pol.in "$POL" 2>>$LOG; then
            chmod 0644 "$POL" 2>>$LOG      # umask is 077 for init services
            echo "policy: $(basename "$POL") $(wc -l < "$POL") lines, includes left: $(grep -c '^@include' "$POL")" >> $LOG
        else
            echo "policy: $(basename "$POL") postponed (/system/etc is read-only here)" >> $LOG
        fi
    done
}

echo "start" > $LOG
fix_policies
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
    chmod 0644 "$LIST" 2>>$LOG      # umask is 077 for init services
    echo "wrote $LIST directly" >> $LOG
    exit 0
fi
if mount -o rw,remount /system 2>>$LOG && cp $FILT $LIST 2>>$LOG; then
    chmod 0644 "$LIST" 2>>$LOG
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
    # The policies were already fixed at the top of this script; call again only
    # in case that ran while /system/etc was still read-only (the case in which
    # this overlay had to be created). It is a no-op once the includes are gone.
    fix_policies
    if cp $FILT $LIST 2>>$LOG; then
        # This runs as an init service, so umask is 077 and cp creates 0600 root.
        # public.libraries.txt is read by ordinary app processes, so without this
        # chmod it is effectively empty for everyone but root.
        chmod 0644 "$LIST" 2>>$LOG
        echo "wrote $LIST through an overlay of /system/etc ($(ls -l "$LIST" | cut -d' ' -f1))" >> $LOG
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