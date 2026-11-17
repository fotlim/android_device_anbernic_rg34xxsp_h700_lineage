#!/bin/bash
# Build the do-nothing "wifi" binder stub and put it where the initrd's vendo
# overlay picks it up. See patches/wifi-stub/wifi_stub.cpp for why it exists
# (boot a95db9e0: ProfileManagerService NPEs on a null WifiManager at boot phase
# 550 and takes system_server with it).
#
#   tools/build-wifi-stub.sh [tree] [embed]
set -e
# Without pipefail a failed `m` is hidden by `| tail`, and this script then
# installs whatever binary was already staged (boot e1625b6a tested an old stub
# because of exactly that).
set -o pipefail
A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
EMBED=${2:-/root/initramfs-embed}
SRC="$A/hardware/libhardware/dsh-wifi-stub"
# The module is a *system* variant on purpose (see patches/wifi-stub/Android.bp:
# a vendor variant writes the VNDR Parcel header and the framework servicemanage
# rejects it), so the build product lives under system/bin even though the
# binary is installed into /vendor/bin/hw by the ramdisk overlay.
PROD="$A/out/target/product/rg34xxsp/system/bin/dsh-wifi-stub"
DST="$EMBED/vendor-overlay/bin/hw/dsh-wifi-stub"

echo "=== stage the stub sources into the tree ==="
mkdir -p "$SRC"
cp -f "$REPO/patches/wifi-stub/wifi_stub.cpp" "$SRC/wifi_stub.cpp"
cp -f "$REPO/patches/wifi-stub/Android.bp" "$SRC/Android.bp"
ls -l "$SRC"

echo "=== build dsh-wifi-stub ==="
cd "$A"
export CCACHE_DIR=/root/.ccache
mkdir -p "$CCACHE_DIR/tmp"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m dsh-wifi-stub -j"${JOBS:-4}" 2>&1 | tail -12

if [ ! -f "$PROD" ]; then
    echo "!! build did not produce $PROD" >&2
    exit 1
fi

echo "=== install into the initrd overlay ==="
mkdir -p "$(dirname "$DST")"
cp -f "$PROD" "$DST"
chmod 755 "$DST"
ls -l "$DST"
file "$DST"
sha256sum "$DST"
readelf -d "$DST" 2>/dev/null | grep NEEDED || true