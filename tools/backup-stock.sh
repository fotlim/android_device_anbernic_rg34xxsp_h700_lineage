#!/bin/bash
# ============================================================================
# backup-stock.sh -- snapshot the stock partitions of an ANBERNIC RG34XX SP
#                    so the unit can always be put back exactly as it shipped.
#
# RUN THIS ON THE DEVICE (it is streamed over ssh), not on the host:
#     cmd /c "ssh root@192.168.137.30 bash -s < tools/backup-stock.sh"
# On Windows, stream it with `cmd /c ... < file`; piping through
# `Get-Content | ssh` transcodes the bytes and breaks the script.
#
# This file is deliberately pure ASCII: it travels through ssh, and the
# Chinese explanation lives in docs/BUILD.md instead.
#
# Layout of the stock eMMC (mmcblk0), read from the unit itself:
#     p1  boot-resource   64 MiB
#     p2  boot-resource   32 MiB   (Allwinner partition metadata, "Volumn/FAT16")
#     p3  env             16 MiB   (u-boot env; empty on a fresh unit)
#     p4  boot            64 MiB   <-- standard Android boot.img, this is what
#                                    our LineageOS image replaces
#     p5  rootfs        6.9 GiB    (stock Ubuntu rootfs)
#     p6  ...           3.9 GiB    (stock /mnt/vendor)
#     p7  512 MiB
#     p8  ...           ~46 GiB    (user data / ROMs)
#
#   usage: backup-stock.sh [min|full]
#          min  -- GPT + first 2 MiB (bootloader) + p1 p2 p3 p4 p7   ~12 MB
#          full -- the above plus p5 p6 (the whole stock OS)        ~1.2 GB
#
# The first 2 MiB matters most: at offset 8 KiB sits the sunxi boot chain
# (0xea0004be + "eGON.BT0" at +4), i.e. the SPL/U-Boot that the BROM loads.
# ============================================================================
set -u

STAMP=$(date +%Y%m%d-%H%M%S)
DEST="/mnt/mmc/backup-stock-$STAMP"
MODE="${1:-min}"

DEV=/dev/mmcblk0
mkdir -p "$DEST" || { echo "cannot create $DEST (is the SD card mounted at /mnt/mmc?)"; exit 1; }

say() { printf '\n=== %s ===\n' "$*"; }

say "0/3 partition table snapshot"
sfdisk -d "$DEV" > "$DEST/partitions.sfdisk" 2>/dev/null || \
  dd if="$DEV" bs=512 count=34 status=none > "$DEST/gpt-head.bin"
lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,PARTUUID > "$DEST/lsblk.txt" 2>/dev/null
cat /proc/cmdline        > "$DEST/proc_cmdline.txt"
cp /proc/device-tree/compatible "$DEST/dt_compatible" 2>/dev/null
dmesg                    > "$DEST/dmesg.txt" 2>/dev/null
echo "  ok"

say "1/3 bootloader area (GPT + sunxi SPL/U-Boot at 8 KiB), first 2 MiB"
dd if="$DEV" bs=1M count=2 status=none | zstd -q -3 -T0 -o "$DEST/bootloader-first2M.img.zst" -f
echo "  $(du -h "$DEST/bootloader-first2M.img.zst" | cut -f1)  sha=$(sha256sum "$DEST/bootloader-first2M.img.zst" | cut -c1-16)"

say "2/3 critical partitions, geometry read from sysfs"
for n in 1 2 3 4 7; do
  start=$(cat "/sys/class/block/mmcblk0p$n/start" 2>/dev/null)
  size=$(cat  "/sys/class/block/mmcblk0p$n/size"  2>/dev/null)
  if [ -z "$start" ] || [ -z "$size" ]; then echo "  p$n: no geometry, skipped"; continue; fi
  off=$((start * 512))
  mb=$((size * 512 / 1024 / 1024))
  out="$DEST/p$n.img.zst"
  printf '  p%s off=%s size=%sMiB ... ' "$n" "$off" "$mb"
  dd if="$DEV" bs=1M skip=$((off / 1024 / 1024)) count=$mb status=none \
     | zstd -q -3 -T0 -o "$out" -f
  echo "$(du -h "$out" | cut -f1)  sha=$(sha256sum "$out" | cut -c1-16)"
done

if [ "$MODE" = "full" ]; then
  say "3/3 large partitions (whole stock OS), optional"
  for n in 5 6; do
    start=$(cat "/sys/class/block/mmcblk0p$n/start")
    size=$(cat  "/sys/class/block/mmcblk0p$n/size")
    off=$((start * 512))
    mb=$((size * 512 / 1024 / 1024))
    printf '  p%s %sMiB ... ' "$n" "$mb"
    dd if="$DEV" bs=4M skip=$((off / 1024 / 1024 / 4)) count=$((mb / 4)) status=none \
       | zstd -q -3 -T0 -o "$DEST/p$n.img.zst" -f
    echo "$(du -h "$DEST/p$n.img.zst" | cut -f1)"
  done
else
  say "3/3 large partitions skipped (mode=$MODE; pass 'full' for p5/p6)"
fi

say "done"
( cd "$DEST" && sha256sum *.zst *.bin *.sfdisk > SHA256SUMS 2>/dev/null && cat SHA256SUMS )
echo
echo "DEST=$DEST"
echo
echo "Pull it to the host, then verify:"
echo '  scp -r root@<device>:'"$DEST"' F:\dsh-backup\'
echo '  (Windows has no zstd; use 7-Zip:  & "C:\Program Files\7-Zip\7z.exe" e p4.img.zst -o<dir>)'