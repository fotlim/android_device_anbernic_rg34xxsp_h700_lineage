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

mkdir -p "$OV"

echo "=== copy (sizes before) ==="
ls -l "$OUT/egl/libGLES_mesa.so" "$OUT/dri/gallium_dri.so" "$OUT/libglapi.so"

# sd97c: FLAT source names on purpose. New subdirectories in the initramfs
# (vendor-overlay/lib64/egl, .../dri) came back ENOTDIR(20) at open() while the
# sibling vendor-overlay/lib64/libglapi.so copied fine, so the ramdisk layout is
# doing something we do not control. Flat files under an existing directory
# avoid the whole question; the first stage installs them under their real names
# in /vendor/lib64/egl and /vendor/lib64/dri.
rm -rf "$OV/lib64/egl" "$OV/lib64/dri"
cp -f "$OUT/egl/libGLES_mesa.so" "$OV/mesa_egl.so"
cp -f "$OUT/egl/libGLES_mesa.so" "$OV/mesa_glesv2.so"
cp -f "$OUT/dri/gallium_dri.so"  "$OV/mesa_panfrost_dri.so"
cp -f "$OUT/libglapi.so"         "$OV/libglapi.so"

chmod 644 "$OV/mesa_egl.so" "$OV/mesa_glesv2.so" "$OV/mesa_panfrost_dri.so" "$OV/libglapi.so"

echo "=== staged into the initrd overlay (flat) ==="
ls -l "$OV"
echo "=== gzip estimate ==="
echo "panfrost_dri.so: $(stat -c%s "$OV/mesa_panfrost_dri.so") B raw, $(gzip -c "$OV/mesa_panfrost_dri.so" | wc -c) B gzip"
sha256sum "$OV/mesa_egl.so" "$OV/mesa_glesv2.so" "$OV/mesa_panfrost_dri.so" "$OV/libglapi.so"
