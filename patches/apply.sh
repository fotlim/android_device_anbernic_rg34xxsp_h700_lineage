#!/bin/sh
# Apply the device patches to a Lineage tree.
#   apply.sh /root/android/lineage
#
# Two patches, in this order:
#   1. system/core/init  -- first stage init publishes the ramdisk fstab, the
#      mount_all rc, the framework log rc, the graphics device rc, the public
#      library list, and (sd48) the three rebuilt minigbm modules, onto the
#      read-only /vendor before the ramdisk is freed.
#   2. external/minigbm  -- a sun4i-drm backend, the card-node fallback in
#      cros_gralloc_driver::init(), and gralloc.minigbm installed into hw/.
set -e
TREE=${1:-/root/android/lineage}
HERE=$(cd "$(dirname "$0")" && pwd)

cd "$TREE/system/core"
if grep -q 'PublishVendorGraphics' init/first_stage_init.cpp; then
	echo "system/core/init: already applied"
else
	git apply "$HERE/init-first-stage-publish-ramdisk-fstab.patch"
	echo "system/core/init: base applied"
fi

# The per-capability scripts layer on top of the base patch.  Each one is
# idempotent: it checks for its own marker first, so re-running apply.sh is safe
# and a tree that already has (say) the graphics block only gets the new one.
for p in patch-mount-data-rc.py patch-framework-logs-rc.py \
         patch-graphics-device-rc.py patch-logcat-to-file.py \
         patch-public-libraries.py patch-public-libs-filter.py \
         patch-vendor-graphics.py patch-selinux-contexts.py \
         patch-devprobe.py patch-service-rc.py patch-composer-rc.py \
         patch-disable-rescue.py patch-hwc-drm-device.py patch-wifi-stub-rc.py \
         patch-wifi-feature.py patch-wpa-supplicant-stub.py \
         patch-system-app-overlay.py patch-skip-bootanim.py \
         patch-privapp-log.py patch-live-snapshot.py; do
	if [ -f "$HERE/$p" ]; then
		python3 "$HERE/$p" || { echo "$p: FAILED"; exit 1; }
	fi
done

cd "$TREE/external/minigbm"
if grep -q 'backend_sun4i' drv.c; then
	echo "external/minigbm: already applied"
else
	git apply "$HERE/minigbm/minigbm-graphics.patch"
	echo "external/minigbm: applied"
fi

echo "done -- rebuild with:"
echo "  m init_first_stage gralloc.minigbm \\"
echo "    android.hardware.graphics.mapper@4.0-impl.minigbm \\"
echo "    android.hardware.graphics.allocator@4.0-service.minigbm"
