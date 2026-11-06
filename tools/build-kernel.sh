#!/bin/bash
# build-kernel.sh - 在 WSL/Linux 上给 RG34XX-SP 编一个 Android 能用的 Image + DTB。
#
# 为什么这么做：
#   * ROCKNIX 的 `projects/ROCKNIX/devices/H700/linux/linux.aarch64.conf` 是这块板
#     （H700 + LPDDR3 + AXP2202 + panel-mipi）唯一经过真机验证的内核配置，
#     拿它当 base 比自己从 defconfig 拼要靠谱得多。
#   * 但那份配置里有两项是 ROCKNIX 构建系统的产物，直接拿来编会失败：
#       CONFIG_INITRAMFS_SOURCE  -> 指向 buildroot 里的目录（usr/initramfs_data.cpio Error 1）
#       CONFIG_EXTRA_FIRMWARE    -> 引用 external-firmware/rtl_bt/*.bin（No rule to make target）
#     本脚本会把这两项清空：Android 自己有 ramdisk，固件走 /vendor/firmware。
#   * 本仓库 kernel/fragment.config 只做增量（binder / fb / ALSA / 触屏无关之类）。
#
# 用法：
#   ./tools/build-kernel.sh                 # 默认 $HOME/rg34xxsp-kernel 当工作区
#   WORK=~/k ./tools/build-kernel.sh
#   JOBS=16 ./tools/build-kernel.sh
#
# 产物：$WORK/out/Image、$WORK/out/sun50i-h700-anbernic-rg34xx-sp*.dtb
# 依赖：git curl make bison flex bc libssl-dev gcc-aarch64-linux-gnu
#   sudo apt install -y build-essential bison flex bc libssl-dev gcc-aarch64-linux-gnu
set -euo pipefail

REV="${REV:-03e2778d1f11de9260543f969e9e888a1c2bf830}"   # Linux 7.1.2 = ROCKNIX 的 pin
PKG_URL="https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"
RK_BASE="https://raw.githubusercontent.com/ROCKNIX/distribution/next/projects/ROCKNIX/devices/H700"
CONF_URL="$RK_BASE/linux/linux.aarch64.conf"
PATCH_BASE="$RK_BASE/patches/linux"
WORK="${WORK:-$HOME/rg34xxsp-kernel}"
JOBS="${JOBS:-$(nproc)}"
CROSS="${CROSS:-aarch64-linux-gnu-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$WORK/linux"
AW="$SRC/arch/arm64/boot/dts/allwinner"
PDIR="$WORK/patches"
OUT="$WORK/out"

patches=(
  0002-rg35xx-enable-HDMI-LCD.patch
  0003-Update-sun8i_tcon_top.c.patch
  0007-rg35xx-add-GPU-opp.patch
  0008-v8_20260804_james_hilliard1_introduce_allwinner_h616_pwm_controller.patch
  0010-rg35xx-enable-pwm-backlight.patch
  0040-Revert-usb-musb-Fix-hardware-lockup-on-first-Rx-endp.patch
  0110-v2_20250226_kikuchan98_drm_panel_add_generic_mipi_panel_driver.patch
  0111-rg35xx-2024-use-panel-mipi-dpi-spi-driver.patch
  0124-battery-name.patch
  0126-20241018_macroalpha82_rg35xx_add_additional_hardware_support.patch
  0127-enable-mmc1-for-RG35XX-2024.patch
  0140-rg35xx-2024-use-rocknix-joypad-driver.patch
  0144-Update-sun50i-h700-anbernic-rg35xx-h.dts.patch
  0150-add-forcefeedback.patch
  0151-phy-fix-OTG-host-mode.patch
  0152-rg35xx-2024-enable-usb-otg.patch
  0153-enable-rgb-leds.patch
  0154-rocknix-dt-id.patch
  0155-sun4i-set-rgb-connector-as-DSI.patch
  0156-rg35xx-plus-add-pcf8563-rtc.patch
  0157-input-axp20x-pek-do-not-report-the-key-press-that-woke-the-system.patch
  0158-mmc-sunxi-mmc-mask-data0-when-updating-the-clock-on-h616-emmc.patch
  0159-arm64-dts-rg35xx-2024-keep-the-second-sd-card-powered.patch
  0160-phy-sun4i-usb-disable-the-clock-that-was-enabled.patch
  0161-drm-sun4i-gate-the-display-pipeline-across-system-suspend.patch
  0162-pinctrl-sunxi-release-the-lcd0-pads-while-the-panel-is-off.patch
  0163-asoc-sun4i-codec-suspend-the-codec-and-switch-the-amplifier-supply.patch
  0200-h700-update-opps.patch
  0204-dts-Enable-hdmi-sound.patch
  0210-h616-cpu-opp-mark-480MHz-as-suspend-opp.patch
  0901-dts-rg35xx-2024-fix-led-1-color.patch
)

say() { printf '\n=== %s ===\n' "$*"; }

for bin in git curl make "${CROSS}gcc"; do
  command -v "$bin" >/dev/null || { echo "缺少 $bin（apt install gcc-aarch64-linux-gnu）"; exit 1; }
done

mkdir -p "$WORK" "$PDIR" "$OUT"

say "1/6 内核源码 $REV"
if [ ! -d "$SRC/.git" ]; then
  git init -q "$SRC"
  git -C "$SRC" remote add origin "$PKG_URL"
  git -C "$SRC" fetch -q --depth 1 origin "$REV"
  git -C "$SRC" checkout -q FETCH_HEAD
else
  echo "已存在，跳过 clone"
fi

say "2/6 ROCKNIX H700 补丁"
for p in "${patches[@]}"; do
  out="$PDIR/$p"
  [ -s "$out" ] && [ "$(head -c 12 "$out" 2>/dev/null)" != "404: Not Fou" ] && continue
  for _ in 1 2 3 4; do curl -fsS -m 60 -o "$out" "$PATCH_BASE/$p" && break; sleep 2; done
done

say "3/6 打补丁（--reject：上游上下文漂移，被拒的部分由脚手架补齐）"
cd "$SRC"
git checkout -q -- arch/arm64/boot/dts/allwinner 2>/dev/null || true
for p in "${patches[@]}"; do
  msg=$(git apply --reject --whitespace=nowarn "$PDIR/$p" 2>&1 || true)
  echo "$msg" | grep -q 'patch failed' && echo "  部分被拒: $(basename "$p")"
done
find "$SRC" -name '*.rej' -delete

# 脚手架节点：与 tools/verify-dts.sh 相同的三处（joypad / mmc2 3V3 / usb vbus）。
# 这几处 hunk 在 7.1.2 上被拒，但本仓库 DTS 需要这些 label 才能编译。
F="$AW/sun50i-h700-anbernic-rg35xx-2024.dts"
if ! grep -q 'joypad: rocknix-singleadc-joypad' "$F"; then
  cat >> "$F" <<'EOF'

/* ---- build-kernel.sh scaffold (not shipped in the device tree) ---- */
/ {
	joypad: rocknix-singleadc-joypad {
		compatible = "rocknix-singleadc-joypad";
		hw_info = <0x01>;
		status = "okay";
	};

	reg_vcc3v3_mmc2: regulator-vcc3v3-mmc2 {
		compatible = "regulator-fixed";
		enable-active-high;
		gpio = <&pio 4 4 GPIO_ACTIVE_HIGH>;
		regulator-min-microvolt = <3300000>;
		regulator-max-microvolt = <3300000>;
		regulator-name = "vcc3v3-mmc2";
	};

	reg_usb0_vbus: regulator-usb0-vbus {
		compatible = "regulator-fixed";
		enable-active-high;
		gpio = <&pio 8 16 GPIO_ACTIVE_HIGH>;
		regulator-min-microvolt = <5000000>;
		regulator-max-microvolt = <5000000>;
		regulator-name = "usb0-vbus";
	};
};
EOF
  echo "  已注入脚手架节点"
fi

say "4/6 配置：ROCKNIX H700 全量 config + 本仓库 fragment"
for _ in 1 2 3 4; do curl -fsS -m 90 -o "$WORK/base.config" "$CONF_URL" && break; sleep 2; done
[ -s "$WORK/base.config" ] || { echo "拉不到 linux.aarch64.conf"; exit 1; }
cp "$WORK/base.config" .config
# 这两项属于 ROCKNIX 的 buildroot 树，脱离它必炸；Android 自己带 ramdisk 和固件。
./scripts/config --file .config --set-str INITRAMFS_SOURCE '' --set-str EXTRA_FIRMWARE ''
cp "$REPO/kernel/fragment.config" "$WORK/fragment.config"
ARCH=arm64 ./scripts/kconfig/merge_config.sh -m -O . .config "$WORK/fragment.config" >/dev/null
make ARCH=arm64 CROSS_COMPILE="$CROSS" olddefconfig >/dev/null
echo "  当前配置：$(grep -c '=y' .config) 个 =y / $(grep -c '=m' .config) 个 =m"

say "5/6 编 Image（-j$JOBS）"
cp "$REPO"/kernel/dts/*.dts "$AW"/
make ARCH=arm64 CROSS_COMPILE="$CROSS" -j"$JOBS" Image
cp -f "$SRC/arch/arm64/boot/Image" "$OUT/Image"

say "6/6 编 DTB"
for f in sun50i-h700-anbernic-rg34xx-sp.dtb sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb; do
  make ARCH=arm64 CROSS_COMPILE="$CROSS" "allwinner/$f"
  cp -f "$AW/$f" "$OUT/$f"
done

say "完成"
ls -l "$OUT"
echo
echo "把 Image 拷到设备树里，供 mka 打进 boot.img："
echo "  cp $OUT/Image $REPO/device/anbernic/rg34xxsp/kernel/Image"
echo "DTB 交给 boot 分区（extlinux.conf）或 tools/mk-sdcard.sh。"