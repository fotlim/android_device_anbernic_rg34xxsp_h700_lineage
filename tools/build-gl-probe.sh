#!/bin/bash
# Build the GL probe and install it where the initrd's vendor overlay picks it up.
#
# Why: boot 35526bea is the first one that ever asked SurfaceFlinger to
# composite anything (SystemUI's keyguard started at last), and SF then dies in
# SwiftShader, eight times, always at the same place:
#
#   #01 pc 00000000003b31d4  /vendor/lib64/egl/libGLESv2_swiftshader.so
#                            (sw::Renderer::executeTask(int)+324)
#
# See patches/gl-probe/probe.c for the questions the probe answers in order
# (EGL init -> pbuffer clear -> shader triangle -> gralloc-backed FBO).
#
#   tools/build-gl-probe.sh [tree] [embed]
set -e
set -o pipefail

A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
EMBED=${2:-/root/initramfs-embed}
SRC="$A/hardware/libhardware/dsh-glprobe"
PROD="$A/out/target/product/rg34xxsp/vendor/bin/hw"
OUT_EMBED="$EMBED/vendor-overlay/bin/hw"

echo "=== stage the probe sources into the tree ==="
mkdir -p "$SRC"
cp -f "$REPO/patches/gl-probe/probe.c" "$SRC/probe.c"
cp -f "$REPO/patches/gl-probe/Android.bp" "$SRC/Android.bp"
ls -l "$SRC"

echo "=== build ==="
cd "$A"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m dsh-glprobe -j"${JOBS:-4}" 2>&1 | tail -20

if [ ! -f "$PROD/dsh-glprobe" ]; then
    echo "!! build did not produce $PROD/dsh-glprobe" >&2
    exit 1
fi

echo "=== install into the initrd overlay ==="
mkdir -p "$OUT_EMBED"
cp -f "$PROD/dsh-glprobe" "$OUT_EMBED/dsh-glprobe"
chmod 755 "$OUT_EMBED/dsh-glprobe"
ls -l "$OUT_EMBED/dsh-glprobe"
file "$OUT_EMBED/dsh-glprobe" 2>/dev/null || true
sha256sum "$OUT_EMBED/dsh-glprobe"
echo "=== NEEDED libs (must all resolve under /vendor/lib64 or the VNDK) ==="
readelf -d "$OUT_EMBED/dsh-glprobe" 2>/dev/null | grep NEEDED || true
