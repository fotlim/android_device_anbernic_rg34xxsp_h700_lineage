#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# build.sh -- build LineageOS 22.2 (Android 15) for the Anbernic RG34XXSP.
#
# Runs inside WSL/Linux. Prereqs: repo, git, ccache, and ~200 GB free space.
#
#   ./tools/build.sh              # full build
#   JOBS=4 ./tools/build.sh       # smaller machine
#   SKIP_SYNC=1 ./tools/build.sh  # tree already synced
#
# The kernel is prebuilt (see kernel/README.md) and is copied into
# device/anbernic/rg34xxsp-kernel before the Android build starts.

set -euo pipefail

BRANCH="${BRANCH:-lineage-22.2}"
TREE="${TREE:-$HOME/android/lineage}"
JOBS="${JOBS:-$(nproc)}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE_REL="device/anbernic/rg34xxsp"

if [ ! -d "$TREE/.repo" ] && [ "${SKIP_SYNC:-0}" != "1" ]; then
    echo "==> repo init/sync $BRANCH into $TREE"
    mkdir -p "$TREE"
    ( cd "$TREE"
      repo init -u https://github.com/LineageOS/android.git -b "$BRANCH" --git-lfs
      mkdir -p .repo/local_manifests
      cat > .repo/local_manifests/rg34xxsp.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <!-- this repository, dropped in as the device tree -->
</manifest>
EOF
      repo sync -c -j"$(nproc)" --force-sync --no-clone-bundle --no-tags
    )
fi

echo "==> linking device tree"
mkdir -p "$TREE/$DEVICE_REL"
rsync -a --delete --exclude '.git' "$HERE/device/anbernic/rg34xxsp/" "$TREE/$DEVICE_REL/"

echo "==> checking prebuilt kernel"
if [ ! -f "$TREE/device/anbernic/rg34xxsp-kernel/Image" ]; then
    echo "!! device/anbernic/rg34xxsp-kernel/Image missing."
    echo "   Build the kernel first (kernel/README.md), then re-run."
    exit 1
fi

cd "$TREE"
source build/envsetup.sh
breakfast lineage_rg34xxsp-userdebug

LOG="$HOME/build-rg34xxsp-$(date +%Y%m%d-%H%M).log"
echo "==> mka bacon -j$JOBS   (log: $LOG)"
mka bacon -j"$JOBS" 2>&1 | tee "$LOG"

OUT="$TREE/out/target/product/rg34xxsp"
echo "==> artifacts in $OUT"
ls -lh "$OUT" | grep -E 'super\.img|boot\.img|recovery\.img|.*\.zip' || true

cat <<'EOF'

Next:
  sudo ./tools/mk-sdcard.sh /dev/sdX --force \
       --uboot H700_DDR4_u-boot-sunxi-with-spl.bin \
       --image-dir out/target/product/rg34xxsp \
       --dtb ../kernel/arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg34xxsp.dtb

U-Boot binaries: ROCKNIX / Anbernic stock images ship
  H700_DDR3_u-boot-sunxi-with-spl.bin   (vdd-dram = 1.20 V)
  H700_DDR4_u-boot-sunxi-with-spl.bin   (vdd-dram = 1.10 V)
Pick the one matching YOUR board; the wrong one will not boot.
EOF