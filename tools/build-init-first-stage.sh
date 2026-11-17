#!/bin/bash
# Build the static first-stage init (the ramdisk /init) carrying the publishing
# patches, and drop it in place as the initramfs' /init.android.
#
# The patches live in the repository and are applied with patches/apply.sh, so
# this script only re-applies them (idempotent) and rebuilds the single module.
#
# REPO defaults to /root/repo, a symlink to the Windows-side checkout.  This
# file is deliberately pure ASCII: the checkout directory name contains
# non-ASCII characters, and passing or embedding that path has repeatedly
# produced "No such file or directory" from a mangled byte sequence.  Create
# the symlink from a shell that can spell the path, then run this script:
#
#     ln -sfn '<checkout>' /root/repo
#     bash /root/repo/tools/build-init-first-stage.sh
set -e
# `m ... | tail -25` hides a failed build behind tail's exit status (boot b30feba9
# shipped the previous init.android because of it). Same fix as the other builders.
set -o pipefail

SRC=/root/android/lineage
REPO=${REPO:-/root/repo}
# Do not call this variable OUT: `lunch` exports OUT=<product dir> and would
# clobber it, which is how an earlier run ended up copying the whole product
# directory instead of the binary.
BIN=$SRC/out/target/product/rg34xxsp/obj/EXECUTABLES/init_first_stage_intermediates/init
DST=/root/initramfs-embed/init.android
JOBS=${JOBS:-4}

if [ ! -f "$REPO/patches/apply.sh" ]; then
	echo "!! $REPO does not look like the repository (no patches/apply.sh)." >&2
	echo "   create the symlink first: ln -sfn '<checkout>' /root/repo" >&2
	exit 1
fi

echo "=== applying patches (idempotent) ==="
bash "$REPO/patches/apply.sh" "$SRC"

# sd82: first_stage_init.cpp includes dsh-fused-location-apk.h, which is generated
# from the built APK. Regenerate it here so a fresh replay always compiles.
APK=$SRC/out/target/product/rg34xxsp/system/priv-app/FusedLocation/FusedLocation.apk
if [ -f "$APK" ]; then
	python3 "$REPO/tools/embed-fused-location-apk.py"
else
	echo "!! $APK missing; run tools/build-fused-location.sh first" >&2
	exit 1
fi
echo "=== diff summary ==="
git -C "$SRC/system/core" diff --stat -- init/first_stage_init.cpp

echo "=== marker strings expected in the new binary ==="
for m in "ctx: probe %s: access=" "ctx: overlay /vendor/etc/selinux ok" \
         "platform lines" "devprobe"; do
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
for m in "ctx: probe %s: access=" "devprobe"; do
	printf '  %-46s %s\n' "$m" "$(strings -a "$BIN" | grep -Fc -- "$m")"
done
echo "--- install ---"
cp -f "$BIN" "$DST"
chmod 755 "$DST"
ls -l "$DST"
sha256sum "$DST"
