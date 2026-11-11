#!/bin/bash
# build-image.sh - 把 initramfs-embed 打进内核 Image，产出一版可直接写卡的文件。
#
# 为什么需要它：sd31 之后每一版 Image 都是「内核 + 内建 initramfs」，而
# tools/build-kernel.sh 的设计是「内核 + DTB 从头编、initramfs 交给别处」
# （它第 509 行会 --set-str INITRAMFS_SOURCE ''）。照原样跑 build-kernel.sh
# 会得到一个没有 initramfs 的 Image，卡上电就是黑的。
#
# 这个脚本只做「重打 initramfs + 重编 Image + 断言 + 落地」这一段，内核树与
# .config 必须是已经配好的（quota 四项、INITRAMFS_SOURCE 指向 initramfs-embed）。
#
# 用法：
#   tools/build-image.sh                 # 版本号 sdNN 从 --version 推不出时用日期
#   VERSION=sd50 tools/build-image.sh
#   KERNEL=/root/rg34xxsp-kernel/linux INITRAMFS=/root/initramfs-embed tools/build-image.sh
#
# 产物：/root/artifacts/Image.<VERSION>，并拷一份到 Windows 的 Downloads。
set -e
WS=${WS:-/mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace}
KERNEL=${KERNEL:-/root/rg34xxsp-kernel/linux}
INITRAMFS=${INITRAMFS:-/root/initramfs-embed}
ART=${ART:-/root/artifacts}
DL=${DL:-/mnt/c/Users/LinOcean/Downloads}
JOBS=${JOBS:-4}
VERSION=${VERSION:-sd$(date +%m%d%H%M)}
CROSS=${CROSS:-aarch64-linux-gnu-}

die() { echo "错误：$*" >&2; exit 1; }

[ -d "$KERNEL" ]      || die "内核树不在：$KERNEL"
[ -f "$KERNEL/Makefile" ] || die "$KERNEL 里没有 Makefile"
[ -f "$INITRAMFS/init" ]         || die "$INITRAMFS/init 不在（debug-init）"
[ -f "$INITRAMFS/init.android" ] || die "$INITRAMFS/init.android 不在（真 init）"

echo "== 1/5 检查 .config =="
cd "$KERNEL"
# olddefconfig 会静默丢掉依赖不满足的符号，所以每次都断言，不要靠记忆。
bad=0
for want in \
	'CONFIG_INITRAMFS_SOURCE="/root/initramfs-embed"' \
	'CONFIG_QFMT_V2=y' \
	'CONFIG_QUOTA_TREE=y' \
	'CONFIG_QUOTACTL=y' \
	'CONFIG_QUOTA=y' \
	'CONFIG_DRM_PANFROST=y' \
	'CONFIG_DRM_SUN4I=y' \
	'CONFIG_SECURITY_SELINUX=y' \
	'CONFIG_ANDROID_BINDERFS=y'; do
	if grep -qF -- "$want" .config; then
		echo "  ok   $want"
	else
		echo "  MISSING $want"; bad=1
	fi
done
[ "$bad" = 0 ] || die ".config 没收敛：先按 docs/HANDOVER.md §6.1 重建配置"

echo "== 2/5 丢掉旧的 initramfs 产物，强制重打 =="
rm -f usr/initramfs_data.cpio usr/initramfs_data.o usr/built-in.a arch/arm64/boot/Image

echo "== 3/5 编 Image（-j$JOBS）=="
make ARCH=arm64 CROSS_COMPILE="$CROSS" -j"$JOBS" Image

IMG=arch/arm64/boot/Image
[ -f "$IMG" ] || die "没有产出 $IMG"
SIZE=$(stat -c%s "$IMG")
echo "  $IMG = $SIZE B"
# 只有 32 MB 左右说明 initramfs 没进去（sd46 及以前带 initramfs 是 35,158,024 B，
# 带 vendor-overlay 图形模块后约 35.5 MB）。
if [ "$SIZE" -lt 34000000 ]; then
	die "Image 只有 $SIZE B，initramfs 没打进去（检查 CONFIG_INITRAMFS_SOURCE）"
fi

echo "== 4/5 断言关键标记串都在 =="
# initramfs 里的东西（debug-init + init.android）与内核里的东西各查一遍。
for s in \
	'stage 9: about to execl' \
	'wdt: wrote 0x16aa0000' \
	'console: framebuffer console unbound' \
	'pmap.txt' \
	'publish: ' \
	'sun4i-drm' ; do
	if grep -qaF -- "$s" "$IMG"; then
		echo "  ok   $s"
	else
		echo "  MISSING $s"; bad=1
	fi
done
[ "$bad" = 0 ] || die "Image 里缺标记串：initramfs 与内核可能不是同一版"

echo "== 5/5 落地 =="
mkdir -p "$ART"
cp -f "$IMG" "$ART/Image.$VERSION"
ls -l "$ART/Image.$VERSION"
sha256sum "$ART/Image.$VERSION"
if [ -d "$DL" ]; then
	cp -f "$ART/Image.$VERSION" "$DL/Image.$VERSION"
	echo "  已拷到 $DL/Image.$VERSION（写卡请从 Windows 侧 Copy-Item + Write-VolumeCache）"
fi
echo BUILD_OK
