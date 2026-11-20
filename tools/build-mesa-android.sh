#!/bin/bash
set -e
NDK=/root/ndk/android-ndk-r25c
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64
API=30
CROSS=/root/ndk-aarch64.cross
cat > $CROSS <<EOF
[binaries]
c = '$TC/bin/aarch64-linux-android$API-clang'
cpp = '$TC/bin/aarch64-linux-android$API-clang++'
ar = '$TC/bin/llvm-ar'
strip = '$TC/bin/llvm-strip'
ranlib = '$TC/bin/llvm-ranlib'
pkg-config = '/usr/bin/pkg-config'
[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'
[properties]
needs_exe_wrapper = true
EOF
echo "=== STAGE 1: libdrm 2.4.120 (android) ==="
cd /root/libdrm-src/libdrm-2.4.120
rm -rf /tmp/libdrm-build /tmp/libdrm-install
meson setup /tmp/libdrm-build --cross-file $CROSS --prefix=/tmp/libdrm-install \
  -Dintel=disabled -Dradeon=disabled -Damdgpu=disabled -Dnouveau=disabled -Dvmwgfx=disabled \
  -Domap=disabled -Dexynos=disabled -Dfreedreno=disabled -Dtegra=disabled -Dvc4=disabled -Detnaviv=disabled \
  -Dcairo-tests=disabled -Dman-pages=disabled -Dvalgrind=disabled \
  -Dfreedreno-kgsl=false -Dinstall-test-programs=false -Dudev=false -Dtests=false 2>&1 | tail -16
ninja -C /tmp/libdrm-build 2>&1 | tail -6
ninja -C /tmp/libdrm-build install 2>&1 | tail -4
echo "=== libdrm installed ==="; find /tmp/libdrm-install -name 'libdrm.so*' -o -name '*.pc' 2>/dev/null | head
echo "=== STAGE 2: mesa (android-stub) ==="
SYS=$TC/sysroot
LZDIR=$(dirname "$(find "$SYS" -name libz.so 2>/dev/null | head -1)")
echo "target libz dir = $LZDIR"
mkdir -p /tmp/target-pc
cat > /tmp/target-pc/zlib.pc <<EOF
prefix=$SYS/usr
libdir=$LZDIR
includedir=$SYS/usr/include
Name: zlib
Version: 1.2.11
Libs: -L\${libdir} -lz
Cflags: -I\${includedir}
EOF
export PKG_CONFIG_PATH=/tmp/target-pc:/tmp/libdrm-install/lib/pkgconfig:/tmp/libdrm-install/lib64/pkgconfig
cd /root/mesa-src/mesa-23.3.6
rm -rf /tmp/mb-ndk
meson setup /tmp/mb-ndk --cross-file $CROSS --prefix=/tmp/mesa-install \
  -Dplatforms=android -Dplatform-sdk-version=$API -Dandroid-stub=true \
  -Dgallium-drivers=panfrost -Dvulkan-drivers= -Dglx=disabled \
  -Dgles1=disabled -Dgles2=enabled -Degl=enabled \
  -Dbuild-tests=false -Dtools= -Dlibdir=lib64 -Dandroid-libbacktrace=disabled 2>&1 | tail -30
ninja -C /tmp/mb-ndk 2>&1 | tail -25
echo "=== mesa artifacts ==="; find /tmp/mb-ndk -name 'gallium_dri.so' -o -name 'libEGL*.so' -o -name 'libGLESv2*.so' 2>/dev/null | while read f; do ls -l "$f"; done
