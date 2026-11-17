#!/bin/bash
# Record or verify the sha256 of every artefact this project puts inside WSL.
#
# Why: the Android tree, the initramfs staging dir and /root/artifacts all live
# in WSL, which other conversations also use. A quiet change there costs a boot
# cycle to notice, so keep a baseline and compare against it.
#
#   tools/verify-artifacts.sh            # compare with tools/artifact-hashes.txt
#   tools/verify-artifacts.sh --write    # (re)write the baseline
#
# Read-only unless --write is given.
set -u

HERE=$(cd "$(dirname "$0")/.." && pwd)
BASE="$HERE/tools/artifact-hashes.txt"

LIST=(
	/root/initramfs-embed/init
	/root/initramfs-embed/init.android
	/root/initramfs-embed/vendor-overlay/lib64/hw/gralloc.minigbm.so
	/root/initramfs-embed/vendor-overlay/lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm.so
	/root/initramfs-embed/vendor-overlay/lib64/hw/android.hardware.health@2.0-impl-2.1.so
	/root/initramfs-embed/vendor-overlay/bin/hw/android.hardware.graphics.allocator@4.0-service.minigbm
	/root/initramfs-embed/vendor-overlay/bin/hw/android.hardware.graphics.composer@2.1-service
	/root/initramfs-embed/vendor-overlay/bin/hw/dsh-hwcprobe
	/root/initramfs-embed/vendor-overlay/bin/hw/dsh-wifi-stub
	/root/initramfs-embed/vendor-overlay/lib64/hw/memtrack.default.so
	/root/initramfs-embed/vendor-overlay/lib64/hw/android.hardware.memtrack@1.0-impl.so
	/mnt/f/rg34xxsp/initrd.img
	/root/artifacts/Image.sd70
	/root/android/lineage/out/target/product/rg34xxsp/system.img
	/root/android/lineage/out/target/product/rg34xxsp/super.img
	/root/android/lineage/out/target/product/rg34xxsp/vendor.img
)

current() {
	for f in "${LIST[@]}"; do
		if [ -f "$f" ]; then
			printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f"
		else
			printf '%-64s  %s\n' MISSING "$f"
		fi
	done
}

if [ "${1:-}" = "--write" ]; then
	current > "$BASE"
	echo "wrote baseline: $BASE ($(grep -c . "$BASE") entries)"
	exit 0
fi

if [ ! -f "$BASE" ]; then
	echo "no baseline yet; run: $0 --write" >&2
	exit 2
fi

cur=$(mktemp)
current > "$cur"
if diff -u "$BASE" "$cur" > /tmp/artifact-diff.txt; then
	echo "OK: every artefact matches the baseline"
	rm -f "$cur"
	exit 0
fi

echo "!! differences against the baseline:" >&2
cat /tmp/artifact-diff.txt >&2
rm -f "$cur"
exit 1