#!/bin/bash
# Build the patched LineageSDK jar and stage it for the /system/framework overlay.
#
# patches/patch-lineage-wifi-npe.py adds the null guard that stops
# ProfileManagerService from killing system_server at boot phase 550; the guard is
# in lineage-sdk, so org.lineageos.platform.jar must be replaced on the device.
# See patches/patch-system-framework-overlay.py for how it travels.
#
#   tools/build-platform-jar.sh [tree] [embed]
set -e
# `m ... | tail` would hide a failed build (the same trap that shipped stale
# binaries twice); see tools/build-wifi-stub.sh.
set -o pipefail
A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
EMBED=${2:-/root/initramfs-embed}
PROD="$A/out/target/product/rg34xxsp/system/framework/org.lineageos.platform.jar"
DST="$EMBED/system-overlay/framework/org.lineageos.platform.jar"

echo "=== apply the java patch (idempotent) ==="
python3 "$REPO/patches/patch-lineage-wifi-npe.py"

echo "=== build org.lineageos.platform ==="
cd "$A"
export CCACHE_DIR=/root/.ccache
mkdir -p "$CCACHE_DIR/tmp"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m org.lineageos.platform -j"${JOBS:-4}" 2>&1 | tail -10

if [ ! -f "$PROD" ]; then
    echo "!! build did not produce $PROD" >&2
    exit 1
fi

echo "=== confirm the guard is in the dex ==="
unzip -p "$PROD" classes.dex > /tmp/platform.dex 2>/dev/null || true
strings -a "$PROD" | grep -c "getActiveSSID" || true

echo "=== install into the initrd overlay ==="
mkdir -p "$(dirname "$DST")"
cp -f "$PROD" "$DST"
chmod 644 "$DST"
ls -l "$DST"
sha256sum "$DST"