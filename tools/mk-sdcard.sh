#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# mk-sdcard.sh -- build a bootable Android SD-card *image* for the
#                Anbernic RG34XXSP (Allwinner H700), no root-on-hardware needed.
#
#   ./mk-sdcard.sh                                   # -> rg34xxsp-sd.img, 8 GiB
#   ./mk-sdcard.sh --out /tmp/card.img --total-gib 30
#   ./mk-sdcard.sh --embed-initramfs --kernel /path/Image
#
# --embed-initramfs builds a card for a kernel Image that already contains the
# initramfs (CONFIG_INITRAMFS_SOURCE=<dir> at build time). Such a card has no
# `initrd` line in extlinux.conf on purpose: an external initrd handed to booti
# would *replace* the built-in one, putting us right back on the uncertain path
# this mode exists to avoid. Requires a separate build of the kernel -- see
# docs/BUILD.md.
#
# Then write it to the TF card with Rufus (dd mode) or balenaEtcher on Windows.
#
# IMPORTANT: the partition table is a hybrid MBR, and that is not cosmetic.
# This U-Boot image (661504 bytes, U-Boot 2026.01, byte-identical to the one
# ROCKNIX ships) only registers the DOS/MBR partition parser: part_efi's
# strings are absent from the binary while part_dos's are present. On a pure
# GPT disk `part list mmc 0 -bootable` therefore returns nothing, `fstype
# mmc 0:1` fails against the 0xEE protective entry, and scan_dev_for_boot is
# never reached -- neither extlinux nor boot.scr is looked at, and the board
# shows a lit power LED with a black screen.
#
# A pure MBR is not an option either: msdos.c clears volname, so the kernel
# would expose no PARTNAME and /dev/block/by-name/* would disappear, which is
# exactly what fstab.rg34xxsp needs. Hence both: entry 0 is 0x0C + bootable
# pointing at the FAT boot partition (satisfies U-Boot's dos parser), entry 1
# is 0xEE at LBA 1 (satisfies the kernel's is_pmbr_valid(), which classifies
# this as GPT_MBR_HYBRID and then parses the real GPT). The 0x0C entry must
# come first so U-Boot numbers the boot partition as 1.
#
# extlinux.conf entries say `kernel /Image` rather than `linux /Image`. Both
# spellings are accepted by U-Boot (pxe_utils.c maps T_KERNEL and T_LINUX to
# the same parse_label_kernel), so this is a style choice, not a workaround --
# `kernel` is what the syslinux spec uses and what ROCKNIX writes.
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
#
# Why the image also carries a *hybrid* MBR (sector 0), on top of the GPT:
#
#   U-Boot on this board walks distro_bootcmd -> scan_dev_for_boot_part, which
#   runs `part list ${devtype} ${devnum} -bootable devplist` and then
#   `fstype ${devtype} ${devnum}:${part}` before it will look at extlinux.conf
#   or boot.scr at all. A pure protective MBR (single 0xEE entry covering the
#   whole disk) gives that code path nothing it can fstype, so *nothing* on the
#   card is ever scanned: the board sits at a black screen with the power LED
#   lit and leaves no trace whatsoever. ROCKNIX ships `label: dos` and boots
#   with the byte-identical U-Boot binary, which is what pointed here.
#
#   Android, on the other hand, needs the GPT: partition *names* are what
#   become /dev/block/by-name/<name>, and fstab.rg34xxsp is written against
#   those names (msdos tables carry no volume name -- block/partitions/msdos.c
#   clears info->volname, so a pure MBR card yields no PARTNAME uevent and the
#   by-name symlinks never appear).
#
#   A hybrid MBR satisfies both: sector 0 gets a normal 0x0C bootable entry for
#   the FAT boot partition (what U-Boot's dos driver needs) plus the protective
#   0xEE entry at LBA 1 (what the kernel's efi_partition demands). The kernel
#   classifies that as GPT_MBR_HYBRID -- see block/partitions/efi.c
#   is_pmbr_valid() -- which is accepted, so it still parses the GPT and the
#   partition names survive.
#
# The 0x0C entry must come first so U-Boot numbers the boot partition as
# partition 1 (`devplist=1`), matching what a plain DOS-labelled card looks
# like.

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
EMBED_INITRAMFS=0
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
        --embed-initramfs) EMBED_INITRAMFS=1; shift ;;
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

# With --embed-initramfs the kernel Image already carries the initramfs (built
# with CONFIG_INITRAMFS_SOURCE pointing at a directory that has our debug-init
# as /init). In that mode we must NOT emit an `initrd` line: U-Boot's booti
# hands any external initrd to the kernel, which *replaces* the built-in one,
# and we would be back to depending on the exact thing we are trying to avoid.
REQUIRED=("$UBOOT" "$DTB" "$KERNEL" "$SUPER")
[ "$EMBED_INITRAMFS" -eq 1 ] || REQUIRED+=("$RAMDISK")
for f in "${REQUIRED[@]}"; do
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

# --- 2b. hybrid MBR (see the long note at the top of this file) ------------
say "writing hybrid MBR (0x0C boot entry + 0xEE protective)"
python3 - "$OUT" "$BOOT_START" "$BOOT_SIZE" "$TOTAL_SECT" <<'PY'
import struct, sys
path, boot_start, boot_size, total = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
f = open(path, 'r+b')
f.seek(0)
mbr = bytearray(f.read(512))
assert mbr[510:512] == b'\x55\xaa', 'no MBR signature to build on: %r' % mbr[510:512]

def chs(lba, heads=255, sectors=63):
    # Classic INT13 geometry. The values only matter to legacy BIOSes and to
    # code that sanity-checks them, so a plain LBA->CHS conversion is enough.
    c = lba // (heads * sectors)
    if c > 1023:
        return b'\xfe\xff\xff'
    h = (lba // sectors) % heads
    s = (lba % sectors) + 1
    return bytes([h, (s & 0x3f) | ((c >> 2) & 0xc0), c & 0xff])

def entry(bootable, ptype, start, size):
    return bytes([0x80 if bootable else 0x00]) + chs(start) + bytes([ptype]) + \
           chs(start + size - 1) + struct.pack('<II', start, size)

# Entry 1: the FAT boot partition as a normal 0x0C (FAT32 LBA) partition. This
# is what U-Boot's dos partition driver sees, and it must be first so that it
# becomes partition number 1 -- scan_dev_for_boot_part falls back to devplist=1.
# Entry 2: the protective 0xEE record the kernel's efi_partition insists on
# (is_pmbr_valid requires os_type 0xEE with starting_lba == 1).
mbr[446:462] = entry(True, 0x0C, boot_start, boot_size)
mbr[462:478] = entry(False, 0xEE, 1, min(total - 1, 0xFFFFFFFF))
mbr[478:510] = bytes(32)
f.seek(0)
f.write(mbr)
f.close()

# Read it back and prove both halves are present.
f = open(path, 'rb')
hdr = f.read(512)
f.close()
e = [hdr[446 + 16 * i: 462 + 16 * i] for i in range(4)]
assert e[0][4] == 0x0C and e[0][0] == 0x80, 'boot entry wrong: %r' % e[0]
assert struct.unpack('<I', e[0][8:12])[0] == boot_start, 'boot entry LBA wrong'
assert struct.unpack('<I', e[0][12:16])[0] == boot_size, 'boot entry size wrong'
assert e[1][4] == 0xEE, 'no protective entry: %r' % e[1]
assert struct.unpack('<I', e[1][8:12])[0] == 1, 'protective entry must start at LBA 1'
print('    hybrid MBR ok: [1] 0x0C bootable @%d +%d, [2] 0xEE @1' % (boot_start, boot_size))
PY

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
[ "$EMBED_INITRAMFS" -eq 1 ] || cp "$RAMDISK" /tmp/sdcard-boot/initrd.img
cp "$DTB"     /tmp/sdcard-boot/sun50i-h700-anbernic-rg34xx-sp.dtb
[ -f "$DTB_V2" ] && cp "$DTB_V2" /tmp/sdcard-boot/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb

# Optional "instrumented" initramfs: the real Android ramdisk with a small
# static logger prepended as /init. The logger takes a snapshot of /proc and
# /sys onto the FAT boot partition, forks a child that streams /dev/kmsg into
# DEBUG/dmesg.txt, then exec's the real Android init (renamed /init.android) so
# the system boots normally -- logging all the way. This is the only way to see
# a failing boot on this board: no UART is exposed, USB never enumerates when
# the kernel dies early, and pstore is not compiled in. Build the logger with
# tools/debug-init/build.sh.
DEBUG_LABEL=""
if [ -n "$DEBUG_INIT" ]; then
    [ -f "$DEBUG_INIT" ] || die "missing --debug-init file: $DEBUG_INIT"
    say "packing instrumented initramfs from $DEBUG_INIT"
    rm -rf /tmp/debug-initrd
    mkdir -p /tmp/debug-initrd
    # Unpack the stock Android ramdisk, then swap in our /init.
    ( cd /tmp/debug-initrd && gzip -dc "$RAMDISK" | cpio -i -d --quiet )
    [ -f /tmp/debug-initrd/init ] || die "stock ramdisk has no /init to wrap"
    mv /tmp/debug-initrd/init /tmp/debug-initrd/init.android
    cp "$DEBUG_INIT" /tmp/debug-initrd/init
    chmod 0755 /tmp/debug-initrd/init /tmp/debug-initrd/init.android
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
#
# When --debug-init is given the default label uses initrd-debug.img, which is
# the stock ramdisk with a logger prepended: it records to DEBUG/dmesg.txt and
# then exec's the real Android init, so this is still a normal boot.
#
# With --embed-initramfs there is no initrd line at all (see the note at the top
# of the input checks): the initramfs travels inside Image.
#
# The initrd line is carried as a *suffix* on the devicetree line rather than
# its own template line, because an empty expansion there would leave a blank
# line inside the label block -- and a blank line is the one thing we cannot
# afford to guess about in a config we only get one shot at testing.
INITRD_SUFFIX=""
if [ "$EMBED_INITRAMFS" -eq 0 ]; then
    if [ -n "$DEBUG_LABEL" ]; then
        BOOT_INITRD=/initrd-debug.img
    else
        BOOT_INITRD=/initrd.img
    fi
    INITRD_SUFFIX="
    initrd $BOOT_INITRD"
fi

cat > /tmp/sdcard-boot/extlinux/extlinux.conf <<EOF
default android
menu title Anbernic RG34XXSP
prompt 0
timeout 0

label android
    menu label Android 11 (LineageOS 18.1)
    kernel /Image
    devicetree /sun50i-h700-anbernic-rg34xx-sp.dtb$INITRD_SUFFIX
    append console=ttyS0,115200 console=tty0 earlycon no_console_suspend ignore_loglevel loglevel=7 coherent_pool=1M androidboot.hardware=rg34xxsp androidboot.selinux=permissive androidboot.boot_devices=4020000.mmc androidboot.super_partition=super drm.debug=0x1f

# Same Android image but with the v2 panel firmware. The two DTB files differ
# only in rocknix-dt-id and the panel node's first compatible string, which is
# what panel-mipi uses to pick panels/<compatible>.panel -- so this label is
# how we find out which panel an individual unit was built with.
label android-v2
    menu label Android 11 (v2 panel)
    kernel /Image
    devicetree /sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb$INITRD_SUFFIX
    append console=ttyS0,115200 console=tty0 earlycon no_console_suspend ignore_loglevel loglevel=7 coherent_pool=1M androidboot.hardware=rg34xxsp androidboot.selinux=permissive androidboot.boot_devices=4020000.mmc androidboot.super_partition=super drm.debug=0x1f

# Same kernel, but no ramdisk and no init: the kernel panics after
# init_mount_root() because there is nothing to exec. Kept as an escape hatch
# for when the panel never lights up.
label shell
    menu label U-Boot shell
    kernel /Image
    append console=ttyS0,115200 console=tty0
EOF

if [ -n "$DEBUG_LABEL" ]; then
    cat >> /tmp/sdcard-boot/extlinux/extlinux.conf <<'EOF'

# Escape hatch: the untouched stock ramdisk, no logger in the way. If the
# instrumented boot behaves differently from this one, the logger is the
# suspect rather than the image.
label plain
    menu label Android 11 (stock ramdisk)
    kernel /Image
    devicetree /sun50i-h700-anbernic-rg34xx-sp.dtb
    initrd /initrd.img
    append console=ttyS0,115200 console=tty0 earlycon no_console_suspend ignore_loglevel loglevel=7 coherent_pool=1M androidboot.hardware=rg34xxsp androidboot.selinux=permissive androidboot.boot_devices=4020000.mmc androidboot.super_partition=super drm.debug=0x1f
EOF
fi

# --- 7b. breadcrumb boot.scr (fallback evidence, never the primary path) ---
#
# scan_dev_for_boot runs, for each prefix:
#     run scan_dev_for_extlinux      <- our extlinux.conf, should win
#     run scan_dev_for_scripts       <- boot.scr, only reached if the above failed
#
# If extlinux works, booti never returns and boot.scr is dead weight. If it
# fails, boot.scr runs and leaves small files on the card saying how far it got
# -- which is the only telemetry this board offers, since it has no exposed UART
# and USB never enumerates when the kernel dies early.
#
# The marker files are written with `fatwrite`, which this U-Boot build has.
# Their *content* is incidental -- existence is the signal. To keep them
# readable anyway they are copied out of the script buffer itself (U-Boot loads
# boot.scr at scriptaddr=0x4FC00000 and executes it in place, so 0x4FC00040 is
# the script text right after mkimage's 64-byte header).
#
# Conditionals use `&&` rather than `test $? -eq 0`: U-Boot's hush does not set
# $? the way a POSIX shell does, but it does implement && / ||.
if command -v mkimage >/dev/null 2>&1; then
    say "building breadcrumb boot.scr"
    cat > /tmp/sdcard-boot.cmd <<'EOF'
fatwrite mmc 0:1 0x4FC00040 BREAD.TXT 32
fatload mmc 0:1 0x4FA00000 /sun50i-h700-anbernic-rg34xx-sp.dtb && fatwrite mmc 0:1 0x4FA00000 STEP1DTB.TXT 16
fatload mmc 0:1 0x40080000 /Image && fatwrite mmc 0:1 0x40080000 STEP2IMG.TXT 16
setenv bootargs console=ttyS0,115200 console=tty0 earlycon no_console_suspend ignore_loglevel loglevel=7 coherent_pool=1M androidboot.hardware=rg34xxsp androidboot.selinux=permissive androidboot.boot_devices=4020000.mmc androidboot.super_partition=super drm.debug=0x1f
booti 0x40080000 - 0x4FA00000
fatwrite mmc 0:1 0x4FC00040 STEP3FAIL.TXT 32
EOF
    mkimage -A arm64 -O linux -T script -C none -n rg34xxsp-breadcrumb \
        -d /tmp/sdcard-boot.cmd /tmp/sdcard-boot/boot.scr >/dev/null
    ls -l /tmp/sdcard-boot/boot.scr
else
    say "mkimage not found -- skipping breadcrumb boot.scr"
fi

sync
umount /tmp/sdcard-boot

# --- 8. verify what we just built ----------------------------------------
say "verifying"
# sfdisk picks a table type by looking at sector 0, and our hybrid MBR makes it
# report the image as `dos` even though a full GPT is present. So the GPT is
# read back directly instead of through sfdisk, and the MBR is checked on its
# own terms.
python3 - "$OUT" "$BOOT_START" "$BOOT_SIZE" "$SUPER_START" <<'PY'
import struct, sys
path, boot_start, boot_size, super_start = (sys.argv[1], int(sys.argv[2]),
                                            int(sys.argv[3]), int(sys.argv[4]))
h = open(path, 'rb')

# ---- sector 0: the hybrid MBR U-Boot's dos driver reads ------------------
h.seek(0); mbr = h.read(512)
assert mbr[510:512] == b'\x55\xaa', 'MBR signature missing'
ents = [mbr[446 + 16 * i: 462 + 16 * i] for i in range(4)]
types = [e[4] for e in ents if e[4] != 0]
assert types[:2] == [0x0C, 0xEE], 'hybrid MBR entries wrong: %r' % types
assert ents[0][0] == 0x80, 'boot entry not marked bootable'
assert struct.unpack('<I', ents[0][8:12])[0] == boot_start, 'boot entry LBA wrong'
assert struct.unpack('<I', ents[0][12:16])[0] == boot_size, 'boot entry size wrong'
assert struct.unpack('<I', ents[1][8:12])[0] == 1, 'protective entry must start at LBA 1'
print('    MBR: [1] 0x0C bootable @%d +%d, [2] 0xEE @1  (U-Boot dos driver)' %
      (boot_start, boot_size))

# ---- sector 1: the GPT the kernel and Android read -----------------------
h.seek(512); gpt = h.read(512)
assert gpt[:8] == b'EFI PART', 'no GPT header at LBA 1: %r' % gpt[:8]
my_lba, alt_lba, first_usable, last_usable = struct.unpack('<QQQQ', gpt[24:56])
entries_lba, num_entries, entry_size = struct.unpack('<QII', gpt[72:88])
assert my_lba == 1, 'GPT my_lba is %d, expected 1' % my_lba
assert entries_lba == 2, 'GPT entry array is at %d, expected 2' % entries_lba
print('    GPT: my_lba=%d alt_lba=%d entries_lba=%d n=%d' %
      (my_lba, alt_lba, entries_lba, num_entries))

# ---- the entry array must not have clobbered the SPL at sector 16 --------
# 8 entries * 128 B = 1024 B = sectors 2..3, which leaves 4..15 free before the
# sunxi SPL at 16. Anything larger eats the bootloader.
assert num_entries == 8 and entry_size == 128, \
    'GPT has %d entries of %d B; must be 8x128 to keep sector 16 free' % (num_entries, entry_size)
assert entries_lba + (num_entries * entry_size + 511) // 512 <= 16, \
    'GPT entry array runs into the SPL at sector 16'

h.seek(entries_lba * 512)
raw = h.read(num_entries * entry_size)
names = []
for i in range(num_entries):
    e = raw[i * entry_size:(i + 1) * entry_size]
    if e[:16] == bytes(16):
        continue
    first, last = struct.unpack('<QQ', e[32:48])
    name = e[56:128].decode('utf-16-le').rstrip('\x00')
    names.append((i + 1, name, first, last - first + 1))
for num, name, first, size in names:
    print('    GPT part %d: %-9s start=%-8d size=%d' % (num, name, first, size))
assert len(names) == 5, 'expected 5 GPT partitions, found %d' % len(names)
assert names[0][1] == 'boot' and names[0][2] == boot_start, 'GPT boot partition wrong'
assert any(n[1] == 'super' and n[2] == super_start for n in names), 'GPT super partition wrong'

# ---- filesystems ---------------------------------------------------------
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
h.close()
PY
echo

# The FAT boot partition must actually contain what U-Boot's sysboot looks for.
say "boot partition contents"
LOOP2=$(losetup -f --show -P -o $((BOOT_START * 512)) --sizelimit $((BOOT_SIZE * 512)) "$OUT")
mkdir -p /tmp/sdcard-check
mount -o ro -t vfat "$LOOP2" /tmp/sdcard-check
find /tmp/sdcard-check -type f | sed "s#/tmp/sdcard-check#    #" | sort

# Guard against the extlinux entry losing its kernel line entirely. The
# keyword itself is not special -- U-Boot maps both T_KERNEL and T_LINUX to
# parse_label_kernel() -- but a label with no kernel line is silently skipped
# and the board then sits at a black screen with the power LED on, which looks
# exactly like a dead kernel.
if grep -qE '^[[:space:]]*(linux|kernel)[[:space:]]+/Image' /tmp/sdcard-check/extlinux/extlinux.conf; then
    :
else
    die "extlinux.conf has no kernel line pointing at /Image"
fi
say "extlinux kernel line present"

# Assert the two things that a bad card silently gets wrong.
if [ "$EMBED_INITRAMFS" -eq 1 ]; then
    if grep -qE '^[[:space:]]*initrd[[:space:]]' /tmp/sdcard-check/extlinux/extlinux.conf; then
        die "embed mode wrote an initrd line -- it would override the built-in initramfs"
    fi
    # The built-in cpio is uncompressed inside Image, so the logger's banner
    # string is greppable. If it is missing, the wrong kernel got copied in.
    grep -qa 'debug-init reached main' /tmp/sdcard-check/Image || \
        die "Image on the card has no embedded debug-init (wrong kernel?)"
    say "embed mode ok: no initrd line, Image carries debug-init"
else
    grep -qE '^[[:space:]]*initrd[[:space:]]' /tmp/sdcard-check/extlinux/extlinux.conf || \
        die "no initrd line but --embed-initramfs was not given"
fi
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