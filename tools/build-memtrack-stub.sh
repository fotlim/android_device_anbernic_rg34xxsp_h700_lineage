#!/bin/bash
# Build the empty memtrack legacy HAL and put it where the initrd's vendor
# overlay picks it up.
#
# Without it, every system_server dies 60 s into ActivityManagerService.<init>:
# Debug.getMemoryInfo -> memtrack_proc_get waits forever for the memtrack HIDL
# service, and that service can never start because hw_get_module("memtrack")
# has no module to load. See patches/memtrack-stub/memtrack.c.
#
#   tools/build-memtrack-stub.sh [tree] [embed]
set -e
A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
EMBED=${2:-/root/initramfs-embed}
SRC="$A/hardware/libhardware/memtrack-stub"
# Do not call this OUT: `lunch` exports OUT=<product dir> and clobbers it, which
# is how the first run of this script "failed" on a module that had just built.
PROD_SO="$A/out/target/product/rg34xxsp/vendor/lib64/hw/memtrack.default.so"
DST="$EMBED/vendor-overlay/lib64/hw/memtrack.default.so"

echo "=== stage the stub sources into the tree ==="
mkdir -p "$SRC"
cp -f "$REPO/patches/memtrack-stub/memtrack.c" "$SRC/memtrack.c"
cp -f "$REPO/patches/memtrack-stub/Android.bp" "$SRC/Android.bp"
ls -l "$SRC"

echo "=== build memtrack.rg34xxsp (installs as memtrack.default.so) ==="
cd "$A"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m memtrack.rg34xxsp -j"${JOBS:-4}" 2>&1 | tail -15

if [ ! -f "$PROD_SO" ]; then
    echo "!! build did not produce $PROD_SO" >&2
    exit 1
fi

echo "=== install into the initrd overlay ==="
mkdir -p "$(dirname "$DST")"
cp -f "$PROD_SO" "$DST"
chmod 644 "$DST"
ls -l "$DST"
file "$DST"
sha256sum "$DST"
echo "=== export symbols (HAL_MODULE_INFO_SYM must be a data symbol) ==="
command -v aarch64-linux-gnu-nm >/dev/null && aarch64-linux-gnu-nm -D "$DST" | grep -i 'HAL_MODULE_INFO_SYM' || echo "(nm unavailable; hw_get_module will verify at runtime)"