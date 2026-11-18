#!/bin/bash
# Build both graphics probes and put them where the boot will find them.
#
#   dsh-glprobe   vendor binary  -> initrd vendor-overlay -> /vendor/bin/hw
#                   (EGL init, pbuffer, shaders, fences, two threads, JIT churn)
#   dsh-glprobe2  system binary  -> cardside staging       -> /system/bin
#                   (the gralloc/minigbm import test; AHardwareBuffer lives in
#                    libandroid, which has no vendor variant in this tree, so it
#                    has to be a system binary -- see patches/gl-probe/probe_ahb.c)
#
# The system probe is copied to /root/initramfs-embed/cardfiles/ so the card
# step can pick it up like the APKs: debug-init stages it into /dshsysapp and
# the first stage publishes it to /system/bin (patches/patch-system-app-overlay.py).
#
set -e
set -o pipefail

A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
EMBED=${2:-/root/initramfs-embed}
SRC="$A/hardware/libhardware/dsh-glprobe"
PROD_V="$A/out/target/product/rg34xxsp/vendor/bin/hw"
PROD_S="$A/out/target/product/rg34xxsp/system/bin"
OUT_EMBED="$EMBED/vendor-overlay/bin/hw"
OUT_CARD="$EMBED/cardfiles"

echo "=== stage the probe sources into the tree ==="
mkdir -p "$SRC"
cp -f "$REPO/patches/gl-probe/probe.c" "$SRC/probe.c"
cp -f "$REPO/patches/gl-probe/probe_ahb.c" "$SRC/probe_ahb.c"
cp -f "$REPO/patches/gl-probe/Android.bp" "$SRC/Android.bp"
ls -l "$SRC"

echo "=== build both ==="
cd "$A"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m dsh-glprobe dsh-glprobe2 -j"${JOBS:-4}" 2>&1 | tail -20

for f in "$PROD_V/dsh-glprobe" "$PROD_S/dsh-glprobe2"; do
	if [ ! -f "$f" ]; then
		echo "!! build did not produce $f" >&2
		exit 1
	fi
done

echo "=== install dsh-glprobe into the initrd vendor overlay ==="
mkdir -p "$OUT_EMBED"
cp -f "$PROD_V/dsh-glprobe" "$OUT_EMBED/dsh-glprobe"
chmod 755 "$OUT_EMBED/dsh-glprobe"
ls -l "$OUT_EMBED/dsh-glprobe"

echo "=== install dsh-glprobe2 for the card staging ==="
mkdir -p "$OUT_CARD"
cp -f "$PROD_S/dsh-glprobe2" "$OUT_CARD/dsh-glprobe2"
chmod 755 "$OUT_CARD/dsh-glprobe2"
ls -l "$OUT_CARD/dsh-glprobe2"

echo "=== hashes ==="
sha256sum "$OUT_EMBED/dsh-glprobe" "$OUT_CARD/dsh-glprobe2"
echo "=== NEEDED libs (vendor probe: all from /vendor/lib64 or the VNDK) ==="
readelf -d "$OUT_EMBED/dsh-glprobe" 2>/dev/null | grep NEEDED || true
echo "=== NEEDED libs (system probe: /system/lib64 + VNDK) ==="
readelf -d "$OUT_CARD/dsh-glprobe2" 2>/dev/null | grep NEEDED || true
