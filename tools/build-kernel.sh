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
#     INITRAMFS_SOURCE 清空（Android 自己有 ramdisk）；
#     EXTRA_FIRMWARE 改成内建本仓库 kernel/firmware/panels/*.panel ——
#     面板驱动 probe 时按 compatible 读 `panels/<compatible>.panel`，内建才不受挂载时序影响。
#   * ROCKNIX 的 0110 补丁在 7.1.2 上只能落下 panel-mipi.c，
#     Kconfig/Makefile 两处 hunk 会被拒 —— 本脚本会幂等地补回，否则驱动根本不会编进内核。
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
# 还有一组「所有设备通用」的 mainline 补丁，放在 packages/linux/patches/mainline。
# 其中 0004 定义了 joypad_input_g —— 树外手柄驱动 extern 引用的就是它，缺了编不过。
ML_BASE="https://raw.githubusercontent.com/ROCKNIX/distribution/next/projects/ROCKNIX/packages/linux/patches/mainline"
JOYPAD_REV="005033d1ddd8746c771f2c81f1174f2d892541c9"
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

# 通用补丁（packages/linux/patches/mainline），必须在 H700 那组之前打。
ml_patches=(
  0003-pwm-add-pwm_set_period.patch
  0004-input-adc-keys-redirect-keycode-316-to-rocknix-joypa.patch
  0005-Bluetooth-btrtl-Add-the-support-for-RTL8733BU.patch
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

say "2/6 ROCKNIX 补丁（3 个通用 + ${#patches[@]} 个 H700）"
for p in "${ml_patches[@]}"; do
  out="$PDIR/ml-$p"
  [ -s "$out" ] && [ "$(head -c 12 "$out" 2>/dev/null)" != "404: Not Fou" ] && continue
  for _ in 1 2 3 4; do curl -fsS -m 60 -o "$out" "$ML_BASE/$p" && break; sleep 2; done
done
for p in "${patches[@]}"; do
  out="$PDIR/$p"
  [ -s "$out" ] && [ "$(head -c 12 "$out" 2>/dev/null)" != "404: Not Fou" ] && continue
  for _ in 1 2 3 4; do curl -fsS -m 60 -o "$out" "$PATCH_BASE/$p" && break; sleep 2; done
done

say "3/6 打补丁（--reject：上游上下文漂移，被拒的部分由脚手架补齐）"
cd "$SRC"
git checkout -q -- arch/arm64/boot/dts/allwinner drivers/input 2>/dev/null || true
for p in "${ml_patches[@]}"; do
  msg=$(git apply --reject --whitespace=nowarn "$PDIR/ml-$p" 2>&1 || true)
  echo "$msg" | grep -q 'patch failed' && echo "  部分被拒: $p"
done
for p in "${patches[@]}"; do
  msg=$(git apply --reject --whitespace=nowarn "$PDIR/$p" 2>&1 || true)
  echo "$msg" | grep -q 'patch failed' && echo "  部分被拒: $(basename "$p")"
done
find "$SRC" -name '*.rej' -delete

# 0002 里同一段 sun50i_h616_tv_quirks 被写了两遍，git apply 会把两个 hunk 都塞进去
# （上下文还匹配得上），编译时直接 redefinition 报错。这里按「完全相同的结构体块」
# 去掉重复的那份；顺便断言它确实只剩一份。
say "3a/6 去重 sun4i_tcon.c 里被重复写入的 quirk 块"
python3 - "$SRC" <<'PY'
import io, re, sys
p = sys.argv[1] + '/drivers/gpu/drm/sun4i/sun4i_tcon.c'
s = io.open(p, encoding='utf-8').read()
pat = re.compile(r'static const struct sun4i_tcon_quirks (\w+) = \{.*?\n\};\n', re.S)
seen, out, pos, dropped = {}, [], 0, []
for m in pat.finditer(s):
    name, body = m.group(1), m.group(0)
    out.append(s[pos:m.start()])
    pos = m.end()
    if name in seen and seen[name] == body:
        dropped.append(name)
    else:
        seen[name] = body
        out.append(body)
out.append(s[pos:])
if dropped:
    io.open(p, 'w', encoding='utf-8', newline='\n').write(''.join(out))
    print('  去重: %s' % ', '.join(dropped))
else:
    print('  没有重复块')
PY
dups=$(grep -c '^static const struct sun4i_tcon_quirks sun50i_h616_tv_quirks' "$SRC/drivers/gpu/drm/sun4i/sun4i_tcon.c")
if [ "$dups" != "1" ]; then
  echo "错误：sun50i_h616_tv_quirks 出现 $dups 次（应恰好 1 次），内核编译会失败"; exit 1
fi

# 0110 补丁的 Kconfig / Makefile 两处 hunk 在 7.1.2 上会被拒，
# 结果就是 panel-mipi.c 躺在树里但永远不参与编译（真机表现：背光亮、屏黑）。
# 这里按锚点幂等补回，并在最后断言生效。
say "3b/6 补回 panel-mipi 的 Kconfig / Makefile 条目"
if [ ! -f "$SRC/drivers/gpu/drm/panel/panel-mipi.c" ]; then
  echo "  警告：panel-mipi.c 不存在（0110 补丁没落下），后面会编不出屏幕驱动"
fi
python3 - "$SRC" <<'PY'
import io, sys, os
src = sys.argv[1]
k = os.path.join(src, 'drivers/gpu/drm/panel/Kconfig')
s = io.open(k, encoding='utf-8').read()
if 'config DRM_PANEL_MIPI' in s:
    print('  Kconfig: 已有 DRM_PANEL_MIPI，跳过')
else:
    anchor = 'config DRM_PANEL_NEC_NL8048HL11'
    entry = (
        'config DRM_PANEL_MIPI\n'
        '\ttristate "Generic MIPI-DSI/DPI(+SPI) panel"\n'
        '\tdepends on OF\n'
        '\tdepends on SPI || DRM_MIPI_DSI\n'
        '\tselect DRM_MIPI_DBI if SPI\n'
        '\tdepends on BACKLIGHT_CLASS_DEVICE\n'
        '\thelp\n'
        '\t  Say Y here if you want to enable support for Generic MIPI-DSI /\n'
        '\t  MIPI-DPI(+SPI) panels.\n'
        '\n'
    )
    if anchor in s:
        s = s.replace(anchor, entry + anchor, 1)
    else:
        s = s + '\n' + entry
    io.open(k, 'w', encoding='utf-8', newline='\n').write(s)
    print('  Kconfig: 已插入 DRM_PANEL_MIPI')

m = os.path.join(src, 'drivers/gpu/drm/panel/Makefile')
s = io.open(m, encoding='utf-8').read()
if 'panel-mipi.o' in s:
    print('  Makefile: 已有 panel-mipi.o，跳过')
else:
    line = 'obj-$(CONFIG_DRM_PANEL_MIPI) += panel-mipi.o\n'
    anchor = 'obj-$(CONFIG_DRM_PANEL_MOTOROLA_MOT)'
    s = s.replace(anchor, line + anchor, 1) if anchor in s else s + line
    io.open(m, 'w', encoding='utf-8', newline='\n').write(s)
    print('  Makefile: 已插入 panel-mipi.o')
PY

# 手柄同样不在内核树里：ROCKNIX 把它当树外模块（hackbar/rocknix-joypad）维护，
# 所以 DTS 里那个 compatible 对应的 .c 在 ROCKNIX 仓库里搜不到。
# 本仓库把源码副本放在 kernel/joypad/，这里塞进 drivers/input/joystick/ 当内建编。
# 为什么内建而不做 .ko：Android 上模块要操心 /vendor/lib/modules 挂载时序和加载顺序，
# 手柄又必须在开机动画前就可用（recovery 里也要能按），内建省掉这一整类问题。
say "3c/6 放入 rocknix-singleadc-joypad 驱动（内建）"
JOY_SRC="$REPO/kernel/joypad"
if [ ! -f "$JOY_SRC/rocknix-singleadc-joypad.c" ]; then
  echo "  错误：$JOY_SRC/rocknix-singleadc-joypad.c 不存在"; exit 1
fi
cp -f "$JOY_SRC/rocknix-singleadc-joypad.c" "$JOY_SRC/rocknix-joypad.h" \
      "$SRC/drivers/input/joystick/"
python3 - "$SRC" <<'PY'
import io, sys, os
src = sys.argv[1]

k = os.path.join(src, 'drivers/input/joystick/Kconfig')
s = io.open(k, encoding='utf-8').read()
if 'config INPUT_ROCKNIX_SINGLEADC_JOYPAD' in s:
    print('  Kconfig: 已有 INPUT_ROCKNIX_SINGLEADC_JOYPAD，跳过')
else:
    entry = (
        'config INPUT_ROCKNIX_SINGLEADC_JOYPAD\n'
        '\ttristate "ROCKNIX single-ADC joypad (Anbernic H700 handhelds)"\n'
        '\tdepends on INPUT\n'
        '\tdepends on IIO\n'
        '\tselect INPUT_FF_MEMLESS\n'
        '\thelp\n'
        '\t  Out-of-tree driver from ROCKNIX (hackbar/rocknix-joypad).\n'
        '\t  Reads the analogue sticks through a single ADC channel with an\n'
        '\t  analogue multiplexer, the buttons through GPIOs, and drives the\n'
        '\t  rumble motor through a PWM. Also exports joypad_input_g, which the\n'
        '\t  adc-keys patch uses to forward BTN_MODE.\n'
        '\n'
    )
    # joystick/Kconfig 的结构是：L5 menuconfig INPUT_JOYSTICK → L17 if INPUT_JOYSTICK
    # → ... → 末尾唯一一个 endif。必须插在那个 endif 之前，否则手柄不在
    # INPUT_JOYSTICK 的 if 里，菜单里看不到也没法选中。
    import re
    ms = list(re.finditer(r'(?m)^endif[ \t]*$', s))
    idx = ms[-1].start() if ms else len(s)
    s = s[:idx] + entry + s[idx:]
    io.open(k, 'w', encoding='utf-8', newline='\n').write(s)
    print('  Kconfig: 已插入 INPUT_ROCKNIX_SINGLEADC_JOYPAD')

m = os.path.join(src, 'drivers/input/joystick/Makefile')
s = io.open(m, encoding='utf-8').read()
if 'rocknix-singleadc-joypad.o' in s:
    print('  Makefile: 已有 rocknix-singleadc-joypad.o，跳过')
else:
    line = 'obj-$(CONFIG_INPUT_ROCKNIX_SINGLEADC_JOYPAD)\t+= rocknix-singleadc-joypad.o\n'
    io.open(m, 'w', encoding='utf-8', newline='\n').write(s + line)
    print('  Makefile: 已追加 rocknix-singleadc-joypad.o')
PY
# 0004 补丁没打上的话，joypad_input_g 就没有定义，内建时会 undefined symbol。
if ! grep -q 'joypad_input_g' "$SRC/drivers/input/keyboard/adc-keys.c"; then
  echo "  错误：adc-keys.c 里没有 joypad_input_g —— 通用补丁 0004 没打上"; exit 1
fi
echo "  adc-keys.c 已有 joypad_input_g（0004 补丁生效）"

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

# 0002 系列里的 02/03/04 三个子补丁在 7.1.2 上会被拒（上下文漂移），结果是：
#   sun50i_planes.c/.h 落在树里（--reject 的新建文件 hunk 成功），
#   但 Kconfig / Makefile 条目、ccu-sun8i-de2.c 的 regmap 导出、
#   sun8i_mixer.c 切到新绑定（sun50i_engine_ops）全都没落地。
# 后果非常隐蔽：mixer 照样 probe（compatible 在 of_table 里），屏幕也能亮，
# 但 DE33 的 plane→通道映射寄存器（0x24/0x28）从未被写过 —— 真机表现就是
# 「满屏横向亮线」。ROCKNIX 官方内核里这三个补丁是生效的，所以它能正常显示。
say "3d/6 启用 DE33 planes 驱动（0002 系列的子补丁 02/03/04）"
SERIES="$PDIR/0002-rg35xx-enable-HDMI-LCD.patch"
if [ ! -s "$SERIES" ]; then
  echo "  错误：$SERIES 不存在（0002 没下载成功？）"; exit 1
fi
python3 - "$SERIES" <<'PY'
import re, sys
src = open(sys.argv[1], encoding='utf-8', errors='replace').read()
parts = re.split(r'(?m)^From [0-9a-f]{40} ', src)
for p in parts[1:]:
    m = re.search(r'Subject: \[PATCH (\d+)/21\]', p)
    if not m:
        continue
    n = int(m.group(1))
    if n in (1, 2, 3, 4, 5, 6, 7, 16):
        open('/tmp/sub-%02d.patch' % n, 'w', encoding='utf-8').write(
            'From ' + '0' * 40 + ' ' + p)
        print('  拆出子补丁 %02d' % n)
PY

if grep -q 'sun50i_engine_ops' "$SRC/drivers/gpu/drm/sun4i/sun8i_mixer.c"; then
  echo "  sun8i_mixer.c 已切到新绑定，跳过"
else
  # 02: DE2 CCU 导出 plane mapping 寄存器 regmap（allwinner,plane-mapping 靠它解析）
  if grep -q 'of_syscon_register_regmap' "$SRC/drivers/clk/sunxi-ng/ccu-sun8i-de2.c"; then
    echo "  ccu-sun8i-de2.c 已有 regmap 导出，跳过"
  else
    git apply /tmp/sub-02.patch && echo "  02 clk: regmap 导出已应用"
  fi
  # 03: Kconfig / Makefile 两处 hunk 会被拒，按锚点幂等补回
  python3 - "$SRC" <<'PY'
import io, os, sys
src = sys.argv[1]
k = os.path.join(src, 'drivers/gpu/drm/sun4i/Kconfig')
s = io.open(k, encoding='utf-8').read()
if 'config DRM_SUN50I_PLANES' in s:
    print('  Kconfig: 已有 DRM_SUN50I_PLANES，跳过')
else:
    entry = (
        'config DRM_SUN50I_PLANES\n'
        '\ttristate\n'
        '\tdefault DRM_SUN4I if DRM_SUN8I_MIXER!=n\n'
        '\thelp\n'
        '\t  Chose this option if you have an Allwinner Soc with the\n'
        '\t  Display Engine 3.3 or newer. Planes are shared resource\n'
        '\t  between multiple mixers.\n'
        '\n'
    )
    i = s.rstrip().rfind('endif')
    s = s[:i] + entry + s[i:]
    io.open(k, 'w', encoding='utf-8', newline='\n').write(s)
    print('  Kconfig: 已插入 DRM_SUN50I_PLANES')
m = os.path.join(src, 'drivers/gpu/drm/sun4i/Makefile')
s = io.open(m, encoding='utf-8').read()
if 'sun50i_planes.o' in s:
    print('  Makefile: 已有 sun50i_planes.o，跳过')
else:
    s = s.rstrip('\n') + '\nobj-$(CONFIG_DRM_SUN50I_PLANES)\t+= sun50i_planes.o\n'
    io.open(m, 'w', encoding='utf-8', newline='\n').write(s)
    print('  Makefile: 已追加 sun50i_planes.o')
PY
  # 04: 把 DE33 切到新绑定（engine->ops 换成 sun50i_engine_ops，取 top_regs 用 index 1）
  patch -p1 -F3 --no-backup-if-mismatch -d "$SRC" < /tmp/sub-04.patch >/dev/null \
    && echo "  04 mixer: DE33 已切到新绑定"
fi

# 01/05/06/07/16 同样是「补丁主体被拒、结果整条链路缺一块」的受害者，各自幂等补上。
# 每一块缺了都会让屏幕停在「亮但没有正确画面」：
#   01 DE33 VI plane 去掉 YUV 格式（YUV 需要 scaler，DE33 scaler 还没实现）
#   05 H616 TCON TV quirks（hdmi_pad，否则 HDMI 通路打不开）
#   06 H616 HDMI PHY 配置表
#   07 H616 display-engine compatible（否则整个 DRM 驱动根本不 bind！）
#   16 R40 TCON LCD quirks（H616/H700 的 LCD TCON 复用 R40 的，DTS 就写着 sun8i-r40-tcon-lcd）
say "3e/6 补齐 0002 系列其余显示子补丁（01/05/06/07/16）"
apply_if() { # $1=子补丁号 $2=目标文件 $3=判据 $4=说明
  if grep -q "$3" "$SRC/$2"; then
    echo "  $4 已在，跳过"
  else
    patch -p1 -F3 --no-backup-if-mismatch -d "$SRC" < "/tmp/sub-$1.patch" >/dev/null \
      && echo "  $4 已应用"
  fi
}
apply_if 01 drivers/gpu/drm/sun4i/sun8i_vi_layer.c 'sun8i_vi_layer_de33_formats' '01 DE33 VI plane 格式表'
apply_if 05 drivers/gpu/drm/sun4i/sun4i_tcon.h          'hdmi_pad'                      '05 H616 TCON TV quirks'
apply_if 06 drivers/gpu/drm/sun4i/sun8i_hdmi_phy.c      'sun50i-h616-hdmi-phy'          '06 H616 HDMI PHY'
apply_if 07 drivers/gpu/drm/sun4i/sun4i_drv.c           'sun50i-h616-display-engine'    '07 H616 display-engine'
apply_if 16 drivers/gpu/drm/sun4i/sun4i_tcon.c          'sun8i_r40_lcd_quirks'          '16 R40 TCON LCD quirks'

# 0003 是 ROCKNIX 的「首行花屏」hack：tcon-top 的 PORT_SEL 复位值不清零而是写 0x20。
if grep -q 'writel(0x20, regs + TCON_TOP_PORT_SEL_REG)' "$SRC/drivers/gpu/drm/sun4i/sun8i_tcon_top.c"; then
  echo "  0003 tcon-top 首行修正 已在，跳过"
else
  patch -p1 -F3 --no-backup-if-mismatch -d "$SRC" \
    < "$PDIR/0003-Update-sun8i_tcon_top.c.patch" >/dev/null \
    && echo "  0003 tcon-top 首行修正 已应用"
fi

# 这些地方缺任何一处，屏幕就是「亮但满屏横线/黑屏」，所以全部断言。
for chk in \
  "$SRC/drivers/gpu/drm/sun4i/sun50i_planes.c" \
  "$SRC/drivers/gpu/drm/sun4i/sun50i_planes.h" ; do
  [ -f "$chk" ] || { echo "  错误：$chk 不存在（子补丁 03 没落下）"; exit 1; }
done
grep -q 'config DRM_SUN50I_PLANES' "$SRC/drivers/gpu/drm/sun4i/Kconfig" \
  || { echo "  错误：Kconfig 里没有 DRM_SUN50I_PLANES"; exit 1; }
grep -q 'sun50i_planes.o' "$SRC/drivers/gpu/drm/sun4i/Makefile" \
  || { echo "  错误：Makefile 里没有 sun50i_planes.o"; exit 1; }
grep -q 'of_syscon_register_regmap' "$SRC/drivers/clk/sunxi-ng/ccu-sun8i-de2.c" \
  || { echo "  错误：ccu-sun8i-de2.c 没导出 regmap，allwinner,plane-mapping 会解析失败"; exit 1; }
grep -q 'sun50i_engine_ops' "$SRC/drivers/gpu/drm/sun4i/sun8i_mixer.c" \
  || { echo "  错误：sun8i_mixer.c 没切到 sun50i_engine_ops"; exit 1; }
grep -q 'sun8i_vi_layer_de33_formats' "$SRC/drivers/gpu/drm/sun4i/sun8i_vi_layer.c" \
  || { echo "  错误：sun8i_vi_layer.c 没有 DE33 格式表（子补丁 01）"; exit 1; }
grep -q 'allwinner,sun50i-h616-display-engine' "$SRC/drivers/gpu/drm/sun4i/sun4i_drv.c" \
  || { echo "  错误：sun4i_drv.c 没有 h616 display-engine（整个 DRM 不会 bind）"; exit 1; }
grep -q 'allwinner,sun50i-h616-tcon-tv' "$SRC/drivers/gpu/drm/sun4i/sun4i_tcon.c" \
  || { echo "  错误：sun4i_tcon.c 没有 h616-tcon-tv（子补丁 05）"; exit 1; }
grep -q 'allwinner,sun8i-r40-tcon-lcd' "$SRC/drivers/gpu/drm/sun4i/sun4i_tcon.c" \
  || { echo "  错误：sun4i_tcon.c 没有 r40-tcon-lcd（子补丁 16，DTS 用的就是它）"; exit 1; }
grep -q 'sun50i-h616-hdmi-phy' "$SRC/drivers/gpu/drm/sun4i/sun8i_hdmi_phy.c" \
  || { echo "  错误：sun8i_hdmi_phy.c 没有 h616 PHY（子补丁 06）"; exit 1; }
grep -q 'writel(0x20, regs + TCON_TOP_PORT_SEL_REG)' "$SRC/drivers/gpu/drm/sun4i/sun8i_tcon_top.c" \
  || { echo "  错误：sun8i_tcon_top.c 没有 0003 的首行修正"; exit 1; }
echo "  DE33 显示链路就位（planes + tcon + tcon-top + hdmi-phy + display-engine）"

# 3f/6 和 sun50i_planes.c 一模一样的坑：0008 补丁新建了 drivers/pwm/pwm-sun8i.c，
# 但同一补丁里改 drivers/pwm/Kconfig 与 drivers/pwm/Makefile 的两个 hunk 被 --reject
# 拒掉了 —— 结果是源码躺在树里、却从来没有被编译过。
# 后果不是「背光暗一点」而是整条背光链断掉：
#   pwm@300a000 (allwinner,sun50i-h616-pwm) 没有 provider
#     → backlight (pwm-backlight) 的 pwm_get() 返回 -EPROBE_DEFER
#       → backlight 永远 probe 不了
#         → panel-mipi 拿不到 backlight 设备 → 屏幕黑（但系统照常跑）
say "3f/6 补回 H616 PWM 驱动的 Kconfig / Makefile 条目（0008 的 hunk 被拒）"
if [ ! -f "$SRC/drivers/pwm/pwm-sun8i.c" ]; then
  echo "  错误：drivers/pwm/pwm-sun8i.c 不存在（0008 没打上？）"; exit 1
fi
python3 - "$SRC" <<'PY'
import io, os, sys
src = sys.argv[1]
k = os.path.join(src, 'drivers/pwm/Kconfig')
s = io.open(k, encoding='utf-8').read()
if 'config PWM_SUN8I' in s:
    print('  Kconfig: 已有 PWM_SUN8I，跳过')
else:
    entry = (
        'config PWM_SUN8I\n'
        '\ttristate "Allwinner sun8i/sun50i PWM support"\n'
        '\tdepends on ARCH_SUNXI || COMPILE_TEST\n'
        '\tdepends on HAS_IOMEM && COMMON_CLK\n'
        '\thelp\n'
        '\t  Generic PWM framework driver for Allwinner sun8i/sun50i SoCs.\n'
        '\t  It supports the PWM controller found on H616 and newer SoCs,\n'
        '\t  which is different from the one handled by PWM_SUN4I.\n'
        '\n'
        '\t  To compile this driver as a module, choose M here: the module\n'
        '\t  will be called pwm-sun8i.\n'
        '\n'
    )
    anchor = 'config PWM_SUN4I'
    i = s.find(anchor)
    if i < 0:
        i = s.rstrip().rfind('endif')
    else:
        # 插到 PWM_SUN4I 整块之后：找下一个顶层 'config ' 或 'endif'
        j = s.find('\nconfig ', i + len(anchor))
        e = s.find('\nendif', i + len(anchor))
        cands = [x for x in (j, e) if x > 0]
        i = min(cands) + 1 if cands else len(s)
    s = s[:i] + entry + s[i:]
    io.open(k, 'w', encoding='utf-8', newline='\n').write(s)
    print('  Kconfig: 已插入 PWM_SUN8I')

m = os.path.join(src, 'drivers/pwm/Makefile')
s = io.open(m, encoding='utf-8').read()
if 'pwm-sun8i.o' in s:
    print('  Makefile: 已有 pwm-sun8i.o，跳过')
else:
    line = 'obj-$(CONFIG_PWM_SUN8I)\t\t+= pwm-sun8i.o\n'
    anchor = 'obj-$(CONFIG_PWM_SUN4I)\t\t+= pwm-sun4i.o\n'
    if anchor in s:
        s = s.replace(anchor, anchor + line, 1)
    else:
        s = s.rstrip('\n') + '\n' + line
    io.open(m, 'w', encoding='utf-8', newline='\n').write(s)
    print('  Makefile: 已加入 pwm-sun8i.o')
PY
grep -q 'config PWM_SUN8I' "$SRC/drivers/pwm/Kconfig" \
  || { echo "  错误：Kconfig 里没有 PWM_SUN8I"; exit 1; }
grep -q 'pwm-sun8i.o' "$SRC/drivers/pwm/Makefile" \
  || { echo "  错误：Makefile 里没有 pwm-sun8i.o"; exit 1; }
echo "  H616 PWM 驱动就位（背光链 pwm@300a000 → pwm-backlight → panel）"

say "4/6 配置：ROCKNIX H700 全量 config + 本仓库 fragment"
for _ in 1 2 3 4; do curl -fsS -m 90 -o "$WORK/base.config" "$CONF_URL" && break; sleep 2; done
[ -s "$WORK/base.config" ] || { echo "拉不到 linux.aarch64.conf"; exit 1; }
cp "$WORK/base.config" .config
# INITRAMFS_SOURCE 属于 ROCKNIX 的 buildroot 树，脱离它必炸；Android 自己有 ramdisk。
./scripts/config --file .config --set-str INITRAMFS_SOURCE ''
# 固件内建：不只是面板，还有 WiFi/BT/regdb —— 这几样都有「加载太早」的问题：
#   panels/*.panel   panel-mipi 在驱动 probe 时就读，早于任何文件系统挂载
#   regulatory.db    cfg80211 在 subsys_initcall 就 load，同样早于 /vendor
#   rtw88/*.bin      rtw88 在 netdev bring-up 时才要，但内建省得纠结路径
#   rtl_bt/*.bin     btrtl 在 HCI open 时要
# 全部塞进 Image 就彻底摆脱「/vendor 挂得上挂不上」这个变量。
rm -rf "$SRC/firmware-extra"
mkdir -p "$SRC/firmware-extra"
( cd "$REPO/kernel/firmware" && find . -type f ! -name 'README*' -print0 |
  while IFS= read -r -d '' f; do
    rel="${f#./}"
    mkdir -p "$SRC/firmware-extra/$(dirname "$rel")"
    cp -f "$f" "$SRC/firmware-extra/$rel"
  done )
FW_LIST=$( cd "$SRC/firmware-extra" && find . -type f | sed 's|^\./||' | sort | tr '\n' ' ' )
FW_LIST="${FW_LIST% }"
if [ -n "$FW_LIST" ]; then
  ./scripts/config --file .config --set-str EXTRA_FIRMWARE "$FW_LIST"
  ./scripts/config --file .config --set-str EXTRA_FIRMWARE_DIR 'firmware-extra'
  ./scripts/config --file .config --enable FW_LOADER
  echo "  内建固件 $(echo $FW_LIST | wc -w) 个：$FW_LIST"
else
  ./scripts/config --file .config --set-str EXTRA_FIRMWARE ''
  echo "  警告：kernel/firmware/ 是空的，屏幕/WiFi 固件都没内建"
fi
cp "$REPO/kernel/fragment.config" "$WORK/fragment.config"
ARCH=arm64 ./scripts/kconfig/merge_config.sh -m -O . .config "$WORK/fragment.config" >/dev/null
make ARCH=arm64 CROSS_COMPILE="$CROSS" olddefconfig >/dev/null
grep -q '^CONFIG_DRM_PANEL_MIPI=y' .config \
  || { echo "错误：CONFIG_DRM_PANEL_MIPI 没生效（Kconfig 条目没补上？）"; exit 1; }
grep -q '^CONFIG_EXTRA_FIRMWARE_DIR="firmware-extra"' .config \
  || { echo "错误：EXTRA_FIRMWARE_DIR 没设成 firmware-extra"; exit 1; }
grep -q '^CONFIG_INPUT_ROCKNIX_SINGLEADC_JOYPAD=y' .config \
  || { echo "错误：CONFIG_INPUT_ROCKNIX_SINGLEADC_JOYPAD 没生效（Kconfig 条目没补上？）"; exit 1; }
# DE33 planes：没有它 mixer 能 probe、屏幕会亮，但 plane 通道映射没配 → 满屏横线
grep -q '^CONFIG_DRM_SUN50I_PLANES=y' .config \
  || { echo "错误：CONFIG_DRM_SUN50I_PLANES 没生效（sun4i/Kconfig 条目没补上？）"; exit 1; }
# H616 PWM：没有它 pwm-backlight 永远 -EPROBE_DEFER → 背光不亮 → 屏幕黑
grep -q '^CONFIG_PWM_SUN8I=y' .config \
  || { echo "错误：CONFIG_PWM_SUN8I 没生效（pwm/Kconfig 条目没补上？）"; exit 1; }

# Android 起不来的那几件事，逐个断言。merge_config 遇到未知符号只 warn 不报错，
# olddefconfig 又会静默丢掉依赖不满足的符号 —— 不断言就只能等开机黑屏再回来查。
bad=0
for want in \
  'CONFIG_SECURITY_SELINUX=y' \
  'CONFIG_SECURITY_NETWORK=y' \
  'CONFIG_SECURITY_SELINUX_DEVELOP=y' \
  'CONFIG_LSM=".*selinux.*"' \
  'CONFIG_AUDIT=y' \
  'CONFIG_ANDROID_BINDER_IPC=y' \
  'CONFIG_ANDROID_BINDERFS=y' \
  'CONFIG_MEMFD_CREATE=y' \
  'CONFIG_DMABUF_HEAPS=y' \
  'CONFIG_USB_CONFIGFS=y' \
  'CONFIG_USB_CONFIGFS_F_FS=y' \
  'CONFIG_BT_HCIUART_H4=y' \
  'CONFIG_BT_RFCOMM=y' \
  'CONFIG_BT_BNEP=y' \
  'CONFIG_NETFILTER_XTABLES_LEGACY=y' \
  'CONFIG_IP_NF_IPTABLES_LEGACY=y' \
  'CONFIG_IP_NF_FILTER=y' \
  'CONFIG_IP_NF_NAT=y' \
  'CONFIG_NETFILTER_XT_TARGET_MASQUERADE=y' ; do
  grep -qE "^$want" .config || { echo "  缺少 $want（Android 需要它，检查 fragment.config 与该符号的依赖）"; bad=1; }
done
[ "$bad" = 0 ] || { echo "错误：配置没收敛，停下。"; exit 1; }
for want in 'panels/anbernic,rg34xx-sp-panel.panel' 'rtw88/rtw8821c_fw.bin' 'rtl_bt/rtl8821cs_fw.bin' 'regulatory.db'; do
  grep -q "^CONFIG_EXTRA_FIRMWARE=.*$want" .config \
    || { echo "错误：EXTRA_FIRMWARE 里缺 $want"; exit 1; }
done
echo "  当前配置：$(grep -c '=y' .config) 个 =y / $(grep -c '=m' .config) 个 =m"
echo "  屏幕驱动：$(grep '^CONFIG_DRM_PANEL_MIPI=' .config) / $(grep '^CONFIG_EXTRA_FIRMWARE=' .config)"
echo "  手柄驱动：$(grep '^CONFIG_INPUT_ROCKNIX_SINGLEADC_JOYPAD=' .config)"

say "5/6 编 Image（-j$JOBS）"
make ARCH=arm64 CROSS_COMPILE="$CROSS" -j"$JOBS" Image
cp -f "$SRC/arch/arm64/boot/Image" "$OUT/Image"

# 符号在 vmlinux 里，Image 里只剩数据 —— 所以查 compatible 字符串才靠得住。
# 显示链路上每一环都要在 Image 里出现，缺一环屏幕就停在「亮但没画面」。
for compat in \
  'allwinner,sun50i-h616-de33-planes' \
  'allwinner,plane-mapping' \
  'allwinner,sun50i-h616-de33-mixer-0' \
  'allwinner,sun50i-h616-display-engine' \
  'allwinner,sun50i-h616-tcon-tv' \
  'allwinner,sun8i-r40-tcon-lcd' \
  'allwinner,sun50i-h616-hdmi-phy' \
  'allwinner,sun50i-h616-pwm' ; do
  grep -aq "$compat" "$OUT/Image" \
    || { echo "错误：Image 里没有 $compat，显示/背光链路缺了这一环"; exit 1; }
done
echo "  Image 内含完整显示链路 compatible（planes/mixer/engine/tcon/tcon-tv/hdmi-phy/pwm）"

# 注意：这里不编 DTB，直接装仓库里那份 ROCKNIX 验证过的二进制。
# 原因见 kernel/dtb/README.md：repo 里的 kernel/dts/*.dts 是 include 式覆盖，
# 依赖 sun50i-h700-anbernic-rg35xx-2024.dts 里存在 panel/joypad 两个 label，
# 而那两段是 ROCKNIX 的 0111/0140 补丁加进去的 —— 没打补丁的树编不过
# （Error: Label or path panel not found）。
say "6/6 安装 DTB"
cp -f "$REPO"/kernel/dtb/sun50i-h700-anbernic-rg34xx-sp.dtb "$OUT/"
cp -f "$REPO"/kernel/dtb/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb "$OUT/"

say "把 Image 装进设备树（TARGET_PREBUILT_KERNEL 就指这个文件）"
DEST="$REPO/device/anbernic/rg34xxsp/kernel/Image"
cp -f "$OUT/Image" "$DEST"
echo "  $DEST"
echo "  $(stat -c%s "$DEST") B  sha256 $(sha256sum "$DEST" | cut -d' ' -f1)"

say "完成"
ls -l "$OUT"
echo
echo "DTB 已就位，交给 boot 分区（extlinux.conf）或 tools/mk-sdcard.sh。"
