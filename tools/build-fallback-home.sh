#!/bin/bash
# sd111: build the directBootAware fallback HOME and stage it into the initrd's
# dshsysapp/ so the first stage can publish it to /system/app.
#
#   tools/build-fallback-home.sh [tree] [embed]
#
# See patches/fallback-home/AndroidManifest.xml for why this app has to exist.
set -e
set -o pipefail

A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
EMBED=${2:-/root/initramfs-embed}
SRC="$A/packages/apps/DshFallbackHome"
PROD="$A/out/target/product/rg34xxsp/system/app/DshFallbackHome/DshFallbackHome.apk"
DST="$EMBED/dshsysapp/DshFallbackHome.apk"

echo "=== stage the module sources into the tree ==="
mkdir -p "$SRC/src/com/dsh/fallbackhome"
cp -f "$REPO/patches/fallback-home/Android.bp" "$SRC/Android.bp"
cp -f "$REPO/patches/fallback-home/AndroidManifest.xml" "$SRC/AndroidManifest.xml"
cp -f "$REPO/patches/fallback-home/src/com/dsh/fallbackhome/FallbackHome.java" \
      "$SRC/src/com/dsh/fallbackhome/FallbackHome.java"
ls -l "$SRC" "$SRC/src/com/dsh/fallbackhome"

echo "=== build DshFallbackHome (jobs=${JOBS:-4}) ==="
cd "$A"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m DshFallbackHome -j"${JOBS:-4}" 2>&1 | tail -25

if [ ! -f "$PROD" ]; then
	echo "!! build did not produce $PROD" >&2
	exit 1
fi

echo "=== stage into the initrd dshsysapp/ ==="
mkdir -p "$(dirname "$DST")"
cp -f "$PROD" "$DST"
chmod 644 "$DST"
ls -l "$DST"
sha256sum "$DST"
