#!/system/bin/sh
# Runs from /vendor/etc/init/97-seccomp-fix.rc at early-init, immediately after
# 96-system-fix.rc has mounted its overlay of /system/etc and rewritten the
# mediacodec policy's mremap line.
#
# Why this exists. The stock policy ends with two @include lines:
#
#     @include /system/etc/seccomp_policy/crash_dump.arm.policy        (line 61)
#     @include /system/etc/seccomp_policy/code_coverage.arm.policy     (line 63)
#
# libminijail fopen()s them successfully, but compile_file() checks errno only
# AFTER the getline loop has already hit EOF:
#
#     /* getline(3) returned -1. This can mean EOF or the below errors. */
#     if (errno == EINVAL || errno == ENOMEM) { ... warn("getmultiline() failed"); }
#
# and getline() does not clear errno at EOF, so a stale EINVAL left by earlier
# parsing makes a perfectly good included file look unreadable:
#
#     libminijail: getmultiline() failed
#     libminijail: compile_file: <fd>(63): '@include .../code_coverage.arm.policy' failed
#     libminijail: compile_filter: compile_file() failed
#     libminijail: failed to compile seccomp filter BPF program from fd 6
#
# and then die() aborts the whole service. vendor.media.omx comes back every
# 5 s (init's default restart_period) and drags media/wificond with it, so
# nothing in class main survives long enough to bring up system_server.
#
# The filter is a hardening layer and this build is permissive, so dropping the
# two includes is safe. Every syscall rule above them is kept untouched.
#
# Boot 702a1e18 is the evidence: mremap was already patched (the file on the
# device was 1247 bytes, mtime 06:56) and the parse still died on line 63 --
# the earlier "missing MREMAP_MAYMOVE constant" theory was wrong.
LOG=/metadata/dsh/seccomp-fix.log
POL=/system/etc/seccomp_policy/mediacodec.policy

echo start > $LOG

if [ ! -f "$POL" ]; then
	echo "no $POL -- nothing to do" >> $LOG
	exit 0
fi

echo "before: $(wc -l < $POL) lines, $(grep -c '^@include' $POL) includes, $(ls -l "$POL" | awk '{print $1}')" >> $LOG

# /dev is devtmpfs and writable here; sed -i would need a writable /system too.
grep -v '^@include' "$POL" > /dev/pol.sec 2>>$LOG
if [ ! -s /dev/pol.sec ]; then
	echo "grep produced nothing, policy left alone" >> $LOG
	exit 0
fi

if ! cp /dev/pol.sec "$POL" 2>>$LOG; then
	echo "cp failed -- /system/etc is read-only, did 96 not mount its overlay?" >> $LOG
	exit 0
fi
# This runs as an init service, so umask is 077 and cp would leave 0600 root;
# media.codec (uid 1046) could not read it.
chmod 0644 "$POL" 2>>$LOG

echo "after:  $(wc -l < $POL) lines, $(grep -c '^@include' $POL) includes, $(ls -l "$POL" | awk '{print $1}')" >> $LOG
rm -f /dev/pol.sec
exit 0