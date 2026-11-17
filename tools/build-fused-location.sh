#!/bin/bash
# Build the AOSP reference fused-location provider and stage it for the
# /system/priv-app overlay (patches/patch-system-app-overlay.py).
#
# Why: boot ec9f8a19 reached boot phase 600 and died on
#   IllegalStateException: Unable to find a direct boot aware fused location provide
#     at LocationManagerService.initializeProvidersLocked(:555)
# frameworks/base/packages/FusedLocation declares exactly that service, but it is
# listed only in build/make/target/product/handheld_system.mk, which this product's
# inheritance chain never reaches (same root cause as the missing fonts).
#
#   tools/build-fused-location.sh [tree] [embed]
set -e
# `m ... | tail` hides a failed build (see tools/build-wifi-stub.sh).
set -o pipefail
A=${1:-/root/android/lineage}
EMBED=${2:-/root/initramfs-embed}
PROD="$A/out/target/product/rg34xxsp/system/priv-app/FusedLocation/FusedLocation.apk"
DST="$EMBED/system-overlay/priv-app/FusedLocation/FusedLocation.apk"

echo "=== build FusedLocation ==="
cd "$A"
export CCACHE_DIR=/root/.ccache
mkdir -p "$CCACHE_DIR/tmp"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m FusedLocation -j"${JOBS:-4}" 2>&1 | tail -8

if [ ! -f "$PROD" ]; then
    echo "!! build did not produce $PROD" >&2
    exit 1
fi

echo "=== stage into the initramfs overlay ==="
mkdir -p "$(dirname "$DST")"
cp -f "$PROD" "$DST"
chmod 644 "$DST"
ls -l "$DST"
sha256sum "$DST"