#!/bin/bash
# Build the static first-stage init (the ramdisk /init) with the fstab-publishing
# patch, and drop it in place as the initramfs' /init.android.
set -e

SRC=/root/android/lineage
TMP=/mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/99_临时与截图/_tmp
OUT=$SRC/out/target/product/rg34xxsp/obj/EXECUTABLES/init_first_stage_intermediates/init
DST=/root/initramfs-embed/init.android

echo "=== applying patch ==="
python3 "$TMP/patch-init-stage.py"
echo "=== diff summary ==="
cd $SRC
git -C system/core diff --stat -- init/first_stage_init.cpp

echo "=== building init_first_stage ==="
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m init_first_stage -j12 2>&1 | tail -25

echo "=== result ==="
ls -l "$OUT"
file "$OUT"
echo "--- marker strings ---"
grep -c "Published" "$OUT" || true
strings -a "$OUT" | grep -c "for the second stage" || true
echo "--- install ---"
cp -f "$OUT" "$DST"
chmod 755 "$DST"
ls -l "$DST"
sha256sum "$DST"