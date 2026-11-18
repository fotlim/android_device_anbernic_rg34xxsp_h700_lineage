#!/bin/bash
# sd97: stage mesa+panfrost artefacts into the initrd vendor overlay.
#
# The initrd overlay is the same mechanism that already ships gralloc.minigbm,
# the composer 2.1 service and the memtrack stub into /vendor without touching
# the (stale) vendor image. Mesa is just bigger.
#
# Naming: the EGL loader picks its driver from ro.hardware.egl (currently
# "swiftshader") and dlopens /vendor/lib64/egl/libEGL_<tag>.so plus
# libGLESv2_<tag>.so. Overriding the *files* instead of the property avoids a
# fight with /vendor/build.prop (ro.* properties are write-once, and the vendor
# value would win). So mesa's libGLES_mesa.so is shipped under the swiftshader
# names -- and it also overwrites the 16 MB SwiftShader, which is fine: we are
# replacing that backend on purpose.
set -e
OUT=/root/android/lineage/out/target/product/rg34xxsp/vendor/lib64
OV=/root/initramfs-embed/vendor-overlay

mkdir -p "$OV/lib64/egl" "$OV/lib64/dri"

echo "=== copy (sizes before) ==="
ls -l "$OUT/egl/libGLES_mesa.so" "$OUT/dri/gallium_dri.so" "$OUT/libglapi.so"

cp -f "$OUT/egl/libGLES_mesa.so" "$OV/lib64/egl/libEGL_swiftshader.so"
cp -f "$OUT/egl/libGLES_mesa.so" "$OV/lib64/egl/libGLESv2_swiftshader.so"
cp -f "$OUT/dri/gallium_dri.so"  "$OV/lib64/dri/panfrost_dri.so"
cp -f "$OUT/libglapi.so"         "$OV/lib64/libglapi.so"

chmod 644 "$OV/lib64/egl/"*.so "$OV/lib64/dri/"*.so "$OV/lib64/libglapi.so"

echo "=== staged into the initrd overlay ==="
ls -l "$OV/lib64/egl" "$OV/lib64/dri" "$OV/lib64/libglapi.so"
echo "=== gzip estimate for the new files ==="
before=$(gzip -c "$OV/lib64/dri/panfrost_dri.so" | wc -c)
echo "panfrost_dri.so: $(stat -c%s "$OV/lib64/dri/panfrost_dri.so") B raw, $before B gzip"
sha256sum "$OV/lib64/egl/"*.so "$OV/lib64/dri/"*.so "$OV/lib64/libglapi.so"
