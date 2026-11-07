#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# mk-sdcard.sh -- lay out an Android SD card for the Anbernic RG34XXSP.
#
#   sudo ./mk-sdcard.sh /dev/sdX --uboot H700_DDR4_u-boot-sunxi-with-spl.bin \
#        --image-dir ../out/target/product/rg34xxsp \
#        --dtb ../kernel/arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg34xx-sp.dtb
#
# What it does:
#   1. writes a GPT with boot / misc / metadata / super / userdata
#   2. writes U-Boot (sunxi SPL + TF-A) raw at 8 KiB, like the stock OS does
#   3. populates the FAT boot partition: extlinux.conf + Image + dtb + initrd
#   4. writes super.img straight to the super partition (no fastboot needed)
#
# Everything on the target device is destroyed. Double-check the device name;
# the script refuses to run without --force.

set -euo pipefail

usage() {
    sed -n '4,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

[ $# -ge 1 ] || usage
DEV="$1"; shift || true

UBOOT=""
IMAGE_DIR=""
DTB=""
FORCE=0
DATA_SIZE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --uboot)      UBOOT="$2"; shift 2 ;;
        --image-dir)  IMAGE_DIR="$2"; shift 2 ;;
        --dtb)        DTB="$2"; shift 2 ;;
        --data-size)  DATA_SIZE="$2"; shift 2 ;;
        --force)      FORCE=1; shift ;;
        -h|--help)    usage ;;
        *) echo "unknown option: $1"; usage ;;
    esac
done

[ -b "$DEV" ] || { echo "$DEV is not a block device"; exit 1; }
[ "$FORCE" -eq 1 ] || { echo "refusing to wipe $DEV without --force"; exit 1; }
for t in sgdisk mkfs.vfat mkfs.ext4 mcopy mmd; do
    command -v "$t" >/dev/null || { echo "missing tool: $t (install gdisk, dosfstools, mtools)"; exit 1; }
done

UBOOT_OFFSET_KB=8      # sunxi: SPL at byte 8192, i.e. 1 KiB blocks, seek=8
BOOT_MIB=64
MISC_MIB=4
META_MIB=16
SUPER_MIB=4096

echo "==> wiping and partitioning $DEV"
sgdisk --zap-all "$DEV"
sgdisk --clear \
    --new=1:0:+${BOOT_MIB}M    --change-name=1:boot     --typecode=1:EF00 \
    --new=2:0:+${MISC_MIB}M    --change-name=2:misc     --typecode=2:0700 \
    --new=3:0:+${META_MIB}M    --change-name=3:metadata --typecode=3:0700 \
    --new=4:0:+${SUPER_MIB}M   --change-name=4:super    --typecode=4:0700 \
    --new=5:0:0                --change-name=5:userdata --typecode=5:0700 \
    "$DEV"
partprobe "$DEV" 2>/dev/null || true
sleep 1

# /dev/sdX -> /dev/sdX1 ; /dev/mmcblk0 -> /dev/mmcblk0p1
case "$DEV" in
    *mmcblk*|*nvme*) P="${DEV}p" ;;
    *)               P="${DEV}" ;;
esac

echo "==> formatting boot (vfat) and metadata (ext4)"
mkfs.vfat -F 32 -n BOOT "${P}1" >/dev/null
mkfs.ext4 -q -F -L metadata "${P}3"
# super and userdata are written by AOSP images / formatted by the device.

if [ -n "$UBOOT" ]; then
    echo "==> writing U-Boot at ${UBOOT_OFFSET_KB} KiB"
    dd if="$UBOOT" of="$DEV" bs=1K seek="$UBOOT_OFFSET_KB" conv=fsync,notrunc
fi

if [ -n "$IMAGE_DIR" ] && [ -n "$DTB" ]; then
    echo "==> populating boot partition"

    BOOTDIR="$(mktemp -d)"
    trap 'rm -rf "$BOOTDIR"' EXIT

    mcopy -i "${P}1" "$DTB" ::
    mcopy -i "${P}1" "$IMAGE_DIR/kernel" ::/Image
    if [ -f "$IMAGE_DIR/ramdisk.img" ]; then
        mcopy -i "${P}1" "$IMAGE_DIR/ramdisk.img" ::/initrd.img
    fi

    cat > "$BOOTDIR/extlinux.conf" <<'EOF'
default android
menu title Anbernic RG34XXSP

label android
    linux /Image
    devicetree /sun50i-h700-anbernic-rg34xx-sp.dtb
    initrd /initrd.img
    append console=ttyS0,115200 earlycon no_console_suspend coherent_pool=1M \
           androidboot.hardware=rg34xxsp androidboot.selinux=permissive \
           androidboot.boot_devices=mmcblk0

# Alternative: let U-Boot parse a classic Android boot image instead.
# label bootimg
#     linux /boot.img
#     append bootm
EOF
    mcopy -i "${P}1" "$BOOTDIR/extlinux.conf" ::/extlinux/extlinux.conf 2>/dev/null || {
        mmd -i "${P}1" ::/extlinux
        mcopy -i "${P}1" "$BOOTDIR/extlinux.conf" ::/extlinux/extlinux.conf
    }

    if [ -f "$IMAGE_DIR/super.img" ]; then
        echo "==> writing super.img"
        dd if="$IMAGE_DIR/super.img" of="${P}4" bs=4M conv=fsync,notrunc status=progress
    else
        echo "!! super.img not built yet -- flash it later with"
        echo "   dd if=out/target/product/rg34xxsp/super.img of=${P}4 bs=4M"
    fi
else
    echo "!! no --image-dir/--dtb given: boot partition left empty"
fi

sync
echo "==> done. Put the card in the TF slot (the second SD slot) and power on."
echo "    serial console: 115200 8N1 on the UART pads (UART0 / PH)"
