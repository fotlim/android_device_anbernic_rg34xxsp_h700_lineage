#!/bin/bash
# Build mesa's Android GL stack with the panfrost driver (sd97).
#
# Why: SwiftShader turned out to be unable to *sample* minigbm/dma-buf buffers
# (boot bbb58a26, DEBUG/live-ahb-probe.txt: rendering into a gralloc buffer
# reads back correctly, sampling the same buffer reads back all black, and the
# CPU-written one segfaults in glFinish). SurfaceFlinger samples every layer
# every frame, so with SwiftShader the screen can never show anything.
#
# This tree already carries mesa 20.3.4 (external/mesa3d) with
# src/gallium/drivers/panfrost, and its Android.mk builds libGLES_mesa as soon
# as BOARD_GPU_DRIVERS is non-empty (see device/anbernic/rg34xxsp/BoardConfig.mk).
# mesa 20.3 wants libdrm >= 2.4.89 and this tree has 2.4.99, so no libdrm
# upgrade is needed -- the "needs 2.4.109" note in HANDOVER was for mesa 23.
#
#   tools/build-mesa.sh                # libGLES_mesa (and whatever it pulls in)
#   tools/build-mesa.sh libEGL_mesa    # a specific module
set -e
set -o pipefail

A=${1:-}
A=/root/android/lineage
MODULE=${1:-libGLES_mesa}
JOBS=${JOBS:-4}

cd "$A"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true

echo "=== BOARD_GPU_DRIVERS / mesa config as the build sees it ==="
grep -n -e BOARD_GPU_DRIVERS -e MESA_ANDROID_MAJOR_VERSION \
	device/anbernic/rg34xxsp/BoardConfig.mk | head

echo "=== building $MODULE (jobs=$JOBS) ==="
CCACHE_DIR=/tmp/ccache m "$MODULE" -j"$JOBS" 2>&1 | tail -40

echo "=== what landed where ==="
for d in \
	out/target/product/rg34xxsp/vendor/lib64/egl \
	out/target/product/rg34xxsp/vendor/lib64/hw \
	out/target/product/rg34xxsp/vendor/lib64/dri \
	out/target/product/rg34xxsp/vendor/lib64 \
	out/target/product/rg34xxsp/vendor/bin/hw; do
	if [ -d "$d" ]; then
		echo "--- $d"
		ls -l "$d" | grep -i -e mesa -e gallium -e panfrost -e GLES -e EGL || true
	fi
done
