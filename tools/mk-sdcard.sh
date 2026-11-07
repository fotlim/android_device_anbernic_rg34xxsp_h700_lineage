#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# mk-sdcard.sh -- build a bootable Android SD-card *image* for the
#                Anbernic RG34XXSP (Allwinner H700), no root-on-hardware needed.
#
#   ./mk-sdcard.sh                                   # -> rg34xxsp-sd.img, 8 GiB
#   ./mk-sdcard.sh --out /tmp/card.img --total-gib 30
#
# Then write it to the TF card with Rufus (dd mode) or balenaEtcher on Windows.
#
# Why an image and not the card directly: Windows refuses raw writes to
# removable media (ERROR_INVALID_ARGUMENT on the first write) unless every
# mounted volume on the disk is locked and dismounted first. Producing an image
# keeps that step in the user's hands, where picking the wrong disk is at least
# visible.
#
# Layout (must stay in sync with device/anbernic/rg34xxsp/fstab.rg34xxsp and
# BoardConfig.mk -- the super partition in particular has to be exactly
# BOARD_SUPER_PARTITION_SIZE or the dm-linear metadata inside super.img
# disagrees with the partition and first-stage init fails to map system):
#
#   offset      size      name        content
#   8 KiB       ~646 KiB  -           u-boot-sunxi-with-spl.bin (sunxi SPL+ATF)
#   1 MiB       64 MiB    boot        FAT32: extlinux/extlinux.conf, Image, initrd, dtb
#   65 MiB      4 MiB     misc        zeroed
#   69 MiB      16 MiB    metadata    ext4 (Android metadata encryption / vold)
#   85 MiB      4 GiB     super       raw super.img (system/_ext/product/vendor)
#   4181 MiB    rest      userdata    ext4
#
# The GPT is created with table-length: 8 on purpose. A default GPT has 128
# entries and its entry array covers sectors 2..33, which is exactly where the
# sunxi SPL lives (sector 16). sfdisk/sgdisk with defaults silently overwrite
# the bootloader -- the stock Anbernic partition table also uses 8 entries.

set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)

OUT="${REPO}/rg34xxsp-sd.img"
TOTAL_GIB=8
UBOOT="${REPO}/kernel/uboot/H700-DDR3-u-boot-sunxi-with-spl.bin"
DTB="${REPO}/kernel/dtb/sun50i-h700-anbernic-rg34xx-sp.dtb"
DTB_V2="${REPO}/kernel/dtb/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb"
KERNEL="${REPO}/device/anbernic/rg34xxsp/kernel/Image"
PRODUCT_OUT="${REPO}/out/target/product/rg34xxsp"
RAMDISK=""
SUPER=""
DEBUG_INIT=""
FORCE=0

usage() {
    sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-1}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --out)         OUT="$2"; shift 2 ;;
        --total-gib)   TOTAL_GIB="$2"; shift 2 ;;
        --uboot)       UBOOT="$2"; shift 2 ;;
        --dtb)         DTB="$2"; shift 2 ;;
        --kernel)      KERNEL="$2"; shift 2 ;;
        --ramdisk)     RAMDISK="$2"; shift 2 ;;
        --super)       SUPER="$2"; shift 2 ;;
        --debug-init)  DEBUG_INIT="$2"; shift 2 ;;
        --product-out) PRODUCT_OUT="$2"; shift 2 ;;
        --force)       FORCE=1; shift ;;
        -h|--help)     usage 0 ;;
        *) echo "unknown option: $1" >&2; usage ;;
    esac
done

[ -n "$RAMDISK" ] || RAMDISK="${PRODUCT_OUT}/ramdisk.img"
[ -n "$SUPER" ]   || SUPER="${PRODUCT_OUT}/super.img"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m!!\033[0m %s\n' "$*" >&2; exit 1; }

for f in "$UBOOT" "$DTB" "$KERNEL" "$RAMDISK" "$SUPER"; do
    [ -f "$f" ] || die "missing input: $f"
done

for t in sfdisk losetup mkfs.vfat mkfs.ext4 dd python3; do
    command -v "$t" >/dev/null || die "missing tool: $t"
done

SIMG2IMG=""
for c in "$(command -v simg2img 2>/dev/null || true)" \
         "${PRODUCT_OUT%/rg34xxsp}/../../../../host/linux-x86/bin/simg2img" \
         "/root/android/lineage/out/host/linux-x86/bin/simg2img"; do
    [ -n "$c" ] && [ -x "$c" ] && { SIMG2IMG="$c"; break; }
done

if [ "$FORCE" -eq 0 ] && [ -e "$OUT" ]; then
    die "$OUT exists; pass --force to overwrite"
fi

# --- geometry --------------------------------------------------------------
SECT=512
MiB=$((1024 * 1024 / SECT))

BOOT_START=$((1 * MiB));      BOOT_SIZE=$((64 * MiB));   BOOT_NAME=boot
MISC_START=$((65 * MiB));     MISC_SIZE=$((4 * MiB));    MISC_NAME=misc
META_START=$((69 * MiB));     META_SIZE=$((16 * MiB));   META_NAME=metadata
SUPER_START=$((85 * MiB));    SUPER_SIZE=$((4 * 1024 * MiB)); SUPER_NAME=super
DATA_START=$((SUPER_START + SUPER_SIZE)); DATA_NAME=userdata

TOTAL_SECT=$((TOTAL_GIB * 1024 * MiB))
# Reserve the tail: the backup GPT header lives in the very last sector and the
# 8-entry backup entry array in the two before it. sfdisk computes its own
# "last usable sector" and rejects a partition that runs into it.
TAIL_RESERVE=34
DATA_SIZE=$((TOTAL_SECT - DATA_START - TAIL_RESERVE))
[ "$DATA_SIZE" -gt $((256 * MiB)) ] || \
    die "--total-gib $TOTAL_GIB leaves only $((DATA_SIZE / MiB)) MiB for userdata"

say "layout: boot/$((BOOT_SIZE / MiB))M misc/$((MISC_SIZE / MiB))M meta/$((META_SIZE / MiB))M \
super/$((SUPER_SIZE / MiB))M userdata/$((DATA_SIZE / MiB))M (total ${TOTAL_GIB} GiB)"

# --- 1. blank image --------------------------------------------------------
say "creating $OUT (${TOTAL_GIB} GiB sparse)"
rm -f "$OUT"
truncate -s $((TOTAL_SECT * SECT)) "$OUT"

# --- 2. GPT with only 8 entries -------------------------------------------
say "writing GPT (table-length 8 so the SPL at sector 16 survives)"
sfdisk --quiet "$OUT" <<EOF
label: gpt
label-id: 52473334-5859-5350-4f53-000000000001
unit: sectors
first-lba: $BOOT_START
table-length: 8
sector-size: 512

start=$BOOT_START, size=$BOOT_SIZE, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name="$BOOT_NAME"
start=$MISC_START, size=$MISC_SIZE, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="$MISC_NAME"
start=$META_START, size=$META_SIZE, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="$META_NAME"
start=$SUPER_START, size=$SUPER_SIZE, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="$SUPER_NAME"
start=$DATA_START, size=$DATA_SIZE, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name="$DATA_NAME"
EOF

# --- 3. bootloader at 8 KiB ------------------------------------------------
say "writing U-Boot at 8 KiB ($(stat -c%s "$UBOOT") B)"
dd if="$UBOOT" of="$OUT" bs=1K seek=8 conv=fsync,notrunc status=none
python3 - "$OUT" <<'PY'
import sys
raw = open(sys.argv[1], 'rb').read(8192 + 64)
hdr = raw[8192:8192 + 16]
# The sunxi SPL ("eGON") starts with an ARM branch instruction whose word
# encoding is 0xEAxxxxxx -> little-endian bytes xx xx 00 ea, and carries the
# ASCII tag "eGON.BT0" at +4. The branch *offset* differs per build (Anbernic's
# stock SPL uses be0400ea, ROCKNIX's uses 160000ea), so only the tag and the
# branch opcode are asserted.
assert hdr[:4][2:] == b'\x00\xea', 'no ARM branch instruction at 8 KiB: %r' % hdr
assert hdr[4:12] == b'eGON.BT0', 'no eGON.BT0 tag at 8 KiB: %r' % hdr
print('    sunxi SPL confirmed: branch %s + eGON.BT0 at 8 KiB' % hdr[:4].hex())
PY

# --- 4. attach the image as a loop device ---------------------------------
LOOP=$(losetup -f --show -P "$OUT")
cleanup() {
    mountpoint -q /tmp/sdcard-boot && umount /tmp/sdcard-boot || true
    losetup -d "$LOOP" 2>/dev/null || true
}
trap cleanup EXIT
sleep 1
partprobe "$LOOP" 2>/dev/null || true
sleep 1
[ -b "${LOOP}p1" ] || die "kernel did not expose ${LOOP}p1 (partscan?)"

P1="${LOOP}p1"; P2="${LOOP}p2"; P3="${LOOP}p3"; P4="${LOOP}p4"; P5="${LOOP}p5"

# --- 5. filesystems -------------------------------------------------------
say "formatting boot (FAT32) / metadata / userdata (ext4)"
mkfs.vfat -F 32 -n BOOT -i 48454C4C "$P1" >/dev/null
mkfs.ext4 -q -F -L metadata  "$P3" >/dev/null
mkfs.ext4 -q -F -L userdata "$P5" >/dev/null
# misc stays zeroed: Android uses it for the bootloader message buffer only.

# --- 6. super (sparse -> raw) --------------------------------------------
say "converting super.img (sparse) to raw"
TMP_SUPER=$(mktemp /tmp/super-raw.XXXXXX)
trap 'rm -f "$TMP_SUPER"; cleanup' EXIT

HEAD4=$(dd if="$SUPER" bs=4 count=1 status=none | od -An -tx1 | tr -d ' \n')
if [ "$HEAD4" = "3aff26ed" ]; then
    [ -n "$SIMG2IMG" ] || die "super.img is sparse but simg2img was not found (pass --super, or install it)"
    "$SIMG2IMG" "$SUPER" "$TMP_SUPER"
else
    cp "$SUPER" "$TMP_SUPER"
fi

RAW=$(stat -c%s "$TMP_SUPER")
[ "$RAW" -eq $((SUPER_SIZE * SECT)) ] || \
    die "super raw size $RAW != partition size $((SUPER_SIZE * SECT)) -- BOARD_SUPER_PARTITION_SIZE changed?"
say "writing super ($((RAW / 1048576)) MiB)"
dd if="$TMP_SUPER" of="$P4" bs=4M conv=fsync,notrunc status=none

# --- 7. boot partition ---------------------------------------------------
say "populating boot partition"
mkdir -p /tmp/sdcard-boot
mount -t vfat "$P1" /tmp/sdcard-boot
mkdir -p /tmp/sdcard-boot/extlinux
cp "$KERNEL"  /tmp/sdcard-boot/Image
cp "$RAMDISK" /tmp/sdcard-boot/initrd.img
cp "$DTB"     /tmp/sdcard-boot/sun50i-h700-anbernic-rg34xx-sp.dtb
[ -f "$DTB_V2" ] && cp "$DTB_V2" /tmp/sdcard-boot/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb

# Optional debug initramfs: a tiny static PID 1 that mounts the FAT partition,
# writes /proc/cmdline + the whole kernel ring buffer to DEBUG/dmesg.txt and
# powers off. This is the only way to read a failing boot on this board -- no
# UART is exposed, USB never enumerates when the kernel dies early, and pstore
# is not compiled in. Build it with tools/debug-init/build.sh.
DEBUG_LABEL=""
if [ -n "$DEBUG_INIT" ]; then
    [ -f "$DEBUG_INIT" ] || die "missing --debug-init file: $DEBUG_INIT"
    say "packing debug initramfs from $DEBUG_INIT"
    rm -rf /tmp/debug-initrd
    mkdir -p /tmp/debug-initrd
    cp "$DEBUG_INIT" /tmp/debug-initrd/init
    chmod 0755 /tmp/debug-initrd/init
    ( cd /tmp/debug-initrd && find . | cpio -o -H newc --quiet | gzip -9 ) \
        > /tmp/sdcard-boot/initrd-debug.img
    ls -l /tmp/sdcard-boot/initrd-debug.img
    DEBUG_LABEL=yes
fi

# No LABEL= shortcut in APPEND on purpose: Android needs androidboot.* on the
# kernel command line, and the dm-linear mapping for /system comes out of the
# super partition found through /dev/block/by-name, so no root= is needed.
#
# Two APPEND details that are load-bearing:
#
#  * androidboot.boot_devices=4020000.mmc -- NOT mmcblk0. init compares this
#    string against the *platform device name* (see
#    system/core/init/devices.cpp:353, where `device` comes out of
#    FindPlatformDevice() as "4020000.mmc" because `soc` is a simple-bus and
#    therefore not itself a platform device). Only a match creates the global
#    /dev/block/by-name/* symlinks that first-stage init follows to find
#    metadata/userdata/super. With the wrong name those symlinks never appear
#    and first-stage mount dies with "Failed to mount required partitions
#    early" -> LOG(FATAL) -> boot loop.
#
#  * console=tty0 + ignore_loglevel -- the handheld has no exposed UART, so
#    ttyS0 is a black hole. tty0 makes the kernel ring buffer and init's
#    stderr visible on the panel once fbcon binds, which is the only way to
#    see where a boot fails on this device.
cat > /tmp/sdcard-boot/extlinux/extlinux.conf <<'EOF'
default android
menu title Anbernic RG34XXSP
prompt 0
timeout 0

label android
    menu label Android 11 (LineageOS 18.1)
    linux /Image
    devicetree /sun50i-h700-anbernic-rg34xx-sp.dtb
    initrd /initrd.img
    append console=ttyS0,115200 console=tty0 earlycon no_console_suspend ignore_loglevel loglevel=7 coherent_pool=1M androidboot.hardware=rg34xxsp androidboot.selinux=permissive androidboot.boot_devices=4020000.mmc androidboot.super_partition=super

# Same Android image but with the v2 panel firmware. The two DTB files differ
# only in rocknix-dt-id and the panel node's first compatible string, which is
# what panel-mipi uses to pick panels/<compatible>.panel -- so this label is
# how we find out which panel an individual unit was built with.
label android-v2
    menu label Android 11 (v2 panel)
    linux /Image
    devicetree /sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb
    initrd /initrd.img
    append console=ttyS0,115200 console=tty0 earlycon no_console_suspend ignore_loglevel loglevel=7 coherent_pool=1M androidboot.hardware=rg34xxsp androidboot.selinux=permissive androidboot.boot_devices=4020000.mmc androidboot.super_partition=super

# Same kernel, but no Android ramdisk and no init: the kernel panics after
# init_mount_root() because there is nothing to exec. Kept as an escape hatch
# for when the panel never lights up.
label shell
    menu label U-Boot shell
    linux /Image
    append console=ttyS0,115200 console=tty0
EOF

if [ -n "$DEBUG_LABEL" ]; then
    cat >> /tmp/sdcard-boot/extlinux/extlinux.conf <<'EOF'

# Log collector: boots the same kernel with a 650 KiB static PID 1 that dumps
# everything to DEBUG/dmesg.txt on this FAT partition and powers off.
label debug
    menu label Debug log dump
    linux /Image
    devicetree /sun50i-h700-anbernic-rg34xx-sp.dtb
    initrd /initrd-debug.img
    append console=ttyS0,115200 console=tty0 earlycon no_console_suspend ignore_loglevel loglevel=7 coherent_pool=1M androidboot.hardware=rg34xxsp
EOF
fi

sync
umount /tmp/sdcard-boot

# --- 8. verify what we just built ----------------------------------------
say "verifying"
sfdisk --dump "$OUT" | sed -n '1,7p'
sfdisk --dump "$OUT" | grep -E '\.img[0-9]+ *:'
NPARTS=$(sfdisk --dump "$OUT" | grep -cE '\.img[0-9]+ *:')
[ "$NPARTS" -eq 5 ] || die "expected 5 partitions, GPT has $NPARTS"
echo
python3 - "$OUT" "$BOOT_START" "$SUPER_START" <<'PY'
import sys
path, boot_start, super_start = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
h = open(path, 'rb')
h.seek(boot_start * 512)
bs = h.read(512)
assert bs[510:512] == b'\x55\xaa', 'boot partition has no FAT boot sector'
# FAT32 keeps the volume label at 0x47 (71); 0x2B is the FAT12/16 slot.
label = bs[71:82].decode('ascii', 'replace').strip()
fstype = bs[82:90].decode('ascii', 'replace').strip()
oem = bs[3:11].decode('ascii', 'replace').strip()
print('    boot: oem=%s label=%s fstype=%s' % (oem, label, fstype))
# LP metadata geometry sits at LP_PARTITION_RESERVED_BYTES (4096), not at 0.
# Magic is LP_METADATA_GEOMETRY_MAGIC = 0x616c4467 -> on disk 67 44 6c 61.
h.seek(super_start * 512 + 4096)
magic = h.read(4)
assert magic == bytes.fromhex('67446c61'), 'no LP metadata geometry in super: %s' % magic.hex()
print('    super: LP metadata geometry magic ok (67446c61)')
PY

# The FAT boot partition must actually contain what U-Boot's sysboot looks for.
say "boot partition contents"
LOOP2=$(losetup -f --show -P -o $((BOOT_START * 512)) --sizelimit $((BOOT_SIZE * 512)) "$OUT")
mkdir -p /tmp/sdcard-check
mount -o ro -t vfat "$LOOP2" /tmp/sdcard-check
find /tmp/sdcard-check -type f | sed "s#/tmp/sdcard-check#    #" | sort
umount /tmp/sdcard-check
losetup -d "$LOOP2"

say "done: $OUT ($(stat -c%s "$OUT") B)"
cat <<EOF

Next step (Windows):
  Rufus -> 选择你的 TF 卡 -> 选 "$OUT" -> 写盘方式选 "DD 镜像" -> 开始
  （不要选 ISO 模式，它是扇区级 raw 写；写完整张卡会被清空）

首次开机：插卡上电。屏幕亮但黑屏 = 面板时序没走通；完全没反应 = SPL 没被 BROM 选中。
串口 115200 8N1（UART0 = PH0 TX / PH1 RX，别接 VCC）能看到 U-Boot 和内核日志。
EOF