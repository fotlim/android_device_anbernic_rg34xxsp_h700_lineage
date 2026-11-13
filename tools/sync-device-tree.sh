#!/bin/bash
# Copy this repository's device tree into the Android build tree.
#
# Why this exists: /root/android/lineage/device/anbernic/rg34xxsp is a **real
# directory**, not a symlink to this checkout. Editing lineage_rg34xxsp.mk or
# device.mk here does nothing until it is copied in, and the symptom is silent:
# kati sees no change, ninja prints "no work to do", and the image keeps the old
# product configuration. That is how the ro.sf.lcd_density and the font
# inheritance were first "fixed" without effect (2026-11-13).
#
# tools/build.sh does the same rsync as part of a full build; use this script
# when you only changed the device tree and want to rebuild one target.
#
# Usage:  sync-device-tree.sh [android-tree]        (default /root/android/lineage)
set -e

TREE=${1:-/root/android/lineage}
HERE=$(cd "$(dirname "$0")/.." && pwd)
SRC="$HERE/device/anbernic/rg34xxsp/"
DST="$TREE/device/anbernic/rg34xxsp/"

[ -d "$SRC" ] || { echo "!! no device tree at $SRC" >&2; exit 1; }
[ -d "$TREE" ] || { echo "!! no Android tree at $TREE" >&2; exit 1; }

if [ -L "$DST" ]; then
	echo "==> $DST is a symlink to a checkout; nothing to copy" >&2
	exit 0
fi

echo "==> rsync $SRC -> $DST"
rsync -a --delete --exclude '.git' "$SRC" "$DST"

# Prove the interesting lines actually arrived.
for probe in 'fonts.mk' 'lcd_density' 'hwui'; do
	printf '    %-14s %s\n' "$probe" \
		"$(grep -c -- "$probe" "$DST/lineage_rg34xxsp.mk" || true)"
done
echo "==> done (non-zero counts above mean the change is in the build tree)"