#!/bin/bash
# Build the static first-stage init (the ramdisk /init) carrying the publishing
# patches, and drop it in place as the initramfs' /init.android.
#
# The patches live in the repository and are applied with patches/apply.sh, so
# this script only re-applies them (idempotent) and rebuilds the single module.
set -e

SRC=/root/android/lineage
REPO=/mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/03_WSA与安�?anbernic-rg34xxsp
TMP=/mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/99_临时与截�?_tmp
# Do not call this variable OUT: `lunch` exports OUT=<product dir> and would
# clobber it, which is how an earlier run ended up copying the whole product
# directory instead of the binary.
BIN=$SRC/out/target/product/rg34xxsp/obj/EXECUTABLES/init_first_stage_intermediates/init
DST=/root/initramfs-embed/init.android
JOBS=${JOBS:-4}

echo "=== applying patches (idempotent) ==="
bash "$REPO/patches/apply.sh" "$SRC"
echo "=== diff summary ==="
git -C "$SRC/system/core" diff --stat -- init/first_stage_init.cpp

echo "=== marker strings expected in the new binary ==="
for m in "ctx: probe %s: access=" "ctx: overlay /vendor/etc/selinux ok" \
         "platform lines" "publish: "; do
  printf '  %-46s %s\n' "$m" "$(grep -Fc "$m" "$SRC/system/core/init/first_stage_init.cpp")"
done

echo "=== building init_first_stage (jobs=$JOBS) ==="
cd "$SRC"
source build/envsetup.sh >/dev/null 2>&1 || true
lunch lineage_rg34xxsp-userdebug >/dev/null 2>&1 || true
m init_first_stage -j"$JOBS" 2>&1 | tail -25

echo "=== result ==="
ls -l "$BIN"
file "$BIN"
echo "--- markers in the binary (must all be >= 1) ---"
for m in "ctx: probe %s: access=" "ctx: overlay /vendor/etc/selinux ok"; do
  printf '  %-46s %s\n' "$m" "$(strings -a "$BIN" | grep -Fc -- "$m")"
done
echo "--- install ---"
cp -f "$BIN" "$DST"
chmod 755 "$DST"
ls -l "$DST"
sha256sum "$DST"
