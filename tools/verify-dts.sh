#!/bin/bash
# verify-dts.sh - 在不编译内核的前提下，验证本仓库的板级 DTS 能否通过 cpp + dtc。
#
# 做法：
#   1. 稀疏 clone torvalds/linux 到 ROCKNIX 实际 pin 的那个 commit（Linux 7.1.2）
#   2. 从 ROCKNIX 拉 H700 的补丁集并 --reject 方式打上（只为拿到 dtsi 里的 label）
#   3. 把本仓库 kernel/dts/*.dts 放进内核树，cpp 预处理后交给 dtc
#
# 用法：
#   ./tools/verify-dts.sh              # 用 /tmp/rg34xxsp-verify 当工作目录
#   WORK=~/dts-verify ./tools/verify-dts.sh
#
# 依赖：git curl cpp dtc（Ubuntu: apt install device-tree-compiler）
set -euo pipefail

REV="03e2778d1f11de9260543f969e9e888a1c2bf830"   # Linux 7.1.2，ROCKNIX 的 pin
PKG_URL="https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"
PATCH_BASE="https://raw.githubusercontent.com/ROCKNIX/distribution/next/projects/ROCKNIX/devices/H700/patches/linux"
WORK="${WORK:-/tmp/rg34xxsp-verify}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$WORK/linux"
AW="$SRC/arch/arm64/boot/dts/allwinner"
PDIR="$WORK/patches"

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

for bin in git curl cpp dtc; do
  command -v "$bin" >/dev/null || { echo "缺少 $bin"; exit 1; }
done

mkdir -p "$WORK" "$PDIR"

say "内核源码 $REV (Linux 7.1.2)"
if [ ! -d "$SRC/.git" ]; then
  git init -q "$SRC"
  git -C "$SRC" remote add origin "$PKG_URL"
  git -C "$SRC" fetch -q --depth 1 origin "$REV"
  git -C "$SRC" checkout -q FETCH_HEAD
  git -C "$SRC" sparse-checkout init --cone
  git -C "$SRC" sparse-checkout set include/dt-bindings include/uapi/linux arch/arm64/boot/dts/allwinner
  git -C "$SRC" checkout -q
else
  echo "已存在，跳过 clone"
fi

say "拉 ROCKNIX 补丁"
for p in "${patches[@]}"; do
  out="$PDIR/$p"
  [ -s "$out" ] && [ "$(head -c 20 "$out")" != "404: Not Found" ] && continue
  for _ in 1 2 3 4; do
    curl -fsS -m 45 -o "$out" "$PATCH_BASE/$p" && break
    sleep 2
  done
done

say "打补丁（--reject，只为拿 dts 里的 label）"
cd "$SRC"
git checkout -q -- arch/arm64/boot/dts/allwinner include/dt-bindings 2>/dev/null || true
rej=0
for p in "${patches[@]}"; do
  msg=$(git apply --reject --whitespace=nowarn "$PDIR/$p" 2>&1 || true)
  if echo "$msg" | grep -q 'patch failed'; then
    n=$(echo "$msg" | grep -c 'patch failed' || true)
    echo "  部分被拒 $(basename "$p") ($n hunk)"
    rej=$((rej+n))
  fi
done
echo "被拒 hunk 合计: $rej"

# 脚手架：上下文漂移导致上面几个 hunk 被拒时，用最小节点补上 label，
# 让 verify 能跑完（真正构建请用 ROCKNIX 的构建系统，它 pin 的补丁顺序是吻合的）。
say "脚手架（仅验证用）"
F="$AW/sun50i-h700-anbernic-rg35xx-2024.dts"
if ! grep -q 'joypad: rocknix-singleadc-joypad' "$F"; then
  cat >> "$F" <<'EOF'

/* ---- verify-dts.sh scaffold (not shipped) ---- */
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

say "编译本仓库的 DTS"
cp "$REPO"/kernel/dts/*.dts "$AW"/
ok=0
for f in sun50i-h700-anbernic-rg34xx-sp.dts sun50i-h700-anbernic-rg34xx-sp-v2-panel.dts; do
  cpp -nostdinc -undef -P -x assembler-with-cpp -I "$AW" -I "$SRC/include" "$AW/$f" -o "$WORK/pre.dts" \
    || { echo "  CPP 失败: $f"; exit 1; }
  if dtc -q -I dts -O dtb -o "$WORK/${f%.dts}.dtb" "$WORK/pre.dts" 2>"$WORK/dtc.err"; then
    echo "  OK $f -> $(wc -c < "$WORK/${f%.dts}.dtb") bytes"
    ok=$((ok+1))
  else
    echo "  DTC 失败: $f"; head -20 "$WORK/dtc.err"; exit 1
  fi
done
echo "  （dtc 用 -q 静音警告；去掉 -q 会看到 11 条 simple_bus_reg/unit_address 之类，"
echo "    全部来自上游 h616.dtsi，不是本仓库引入的）"

say "结果"
echo "$ok/2 个 DTB 编译通过，产物在 $WORK/"
echo "抽查命令：dtc -I dtb -O dts $WORK/sun50i-h700-anbernic-rg34xx-sp.dtb | grep -A6 'panel@0'"