#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
#
# build.sh -- cross-compile debug-init.c into a static aarch64 /init.
#
#   ./build.sh                 # -> ./init
#   ./build.sh -o /tmp/init
#
# The binary has to be static: it runs as PID 1 out of a 650 KiB initramfs,
# where no shared libraries exist yet. It is then packed into initrd-debug.img
# by tools/mk-sdcard.sh --debug-init, which adds a "debug" label to
# extlinux.conf.
#
# Needs the aarch64 cross toolchain plus its headers:
#   apt-get install -y gcc-aarch64-linux-gnu libc6-dev-arm64-cross \
#                      linux-libc-dev-arm64-cross
# (linux-libc-dev-arm64-cross is easy to miss: without it <linux/errno.h>
#  pulls in <asm/errno.h>, which the plain gcc-aarch64-linux-gnu package does
#  not provide, and the build dies on the first #include.)
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/init"
CC=${CC:-aarch64-linux-gnu-gcc}

while [ $# -gt 0 ]; do
    case "$1" in
        -o) OUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

command -v "$CC" >/dev/null || {
    echo "!! $CC not found; apt-get install -y gcc-aarch64-linux-gnu" >&2
    exit 1
}

echo "==> compiling $HERE/debug-init.c"
"$CC" -static -O2 -o "$OUT" "$HERE/debug-init.c"

echo "==> verifying"
file "$OUT"
if command -v aarch64-linux-gnu-readelf >/dev/null; then
    if aarch64-linux-gnu-readelf -d "$OUT" 2>/dev/null | grep -q NEEDED; then
        echo "!! $OUT has dynamic dependencies; it must be static" >&2
        exit 1
    fi
    echo "    static, no NEEDED entries"
fi
echo "==> done: $OUT ($(stat -c%s "$OUT") B)"
