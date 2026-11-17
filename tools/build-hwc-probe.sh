#!/bin/bash
# Build the hwcomposer 2.1 service and the hwcomposer probe, then install both
# where the initrd's vendor overlay picks them up.
#
# Why the 2.1 service: SurfaceFlinger in this tree is a composer@2.1 client.
# Boot df79ab2d shows it asking hwservicemanager fo
#   android.hardware.graphics.composer@2.1::IComposer/default
# and hwservicemanager answering
#   "Could not find 'android.hardware.graphics.composer@2.1::IComposer/default'
#    for ctl.interface_start"
# because the only composer this image ships is the 2.2 one
# (/vendor/bin/hw/android.hardware.graphics.composer@2.2-service, whose rc has
# no `interface` line at all).  A 2.2 service does not answer a 2.1 lookup, so
# every SurfaceFlinger start has been racing a HAL it can never bind.
#
# Why the probe: the 2.2 service exits 1 within a millisecond of dlopen()ing
# hwcomposer.drm_minigbm.so, and none of its ALOG output reaches the snapshot's
# logcat.  See patches/hwc-probe/probe.c for what it prints.
#
#   tools/build-hwc-probe.sh [tree] [embed]
set -e
# `m ... | tail` hides a failed build behind tail's exit status; see the same
# fix in tools/build-wifi-stub.sh.
set -o pipefail
A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
EMBED=${2:-/root/initramfs-embed}
SRC="$A/hardware/libhardware/dsh-hwcprobe"
PROD="$A/out/target/product/rg34xxsp/vendor/bin/hw"
OUT_EMBED="$EMBED/vendor-overlay/bin/hw"

echo "=== stage the probe sources into the tree ==="
mkdir -p "$SRC"
cp -f "$REPO/patches/hwc-probe/probe.c" "$SRC/probe.c"
cp -f "$REPO/patches/hwc-probe/Android.bp" "$SRC/Android.bp"
ls -l "$SRC"

echo "=== build both binaries ==="
cd "$A"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m dsh-hwcprobe android.hardware.graphics.composer@2.1-service -j"${JOBS:-4}" 2>&1 | tail -20

for f in dsh-hwcprobe android.hardware.graphics.composer@2.1-service; do
    if [ ! -f "$PROD/$f" ]; then
        echo "!! build did not produce $PROD/$f" >&2
        exit 1
    fi
done

echo "=== install into the initrd overlay ==="
mkdir -p "$OUT_EMBED"
for f in dsh-hwcprobe android.hardware.graphics.composer@2.1-service; do
    cp -f "$PROD/$f" "$OUT_EMBED/$f"
    chmod 755 "$OUT_EMBED/$f"
done
ls -l "$OUT_EMBED"
file "$OUT_EMBED"/* 2>/dev/null || true
sha256sum "$OUT_EMBED"/*
echo "=== composer 2.1 service NEEDED libs (must all exist in /vendor/lib64) ==="
readelf -d "$OUT_EMBED/android.hardware.graphics.composer@2.1-service" 2>/dev/null | grep NEEDED || true
echo "=== probe NEEDED libs ==="
readelf -d "$OUT_EMBED/dsh-hwcprobe" 2>/dev/null | grep NEEDED || true