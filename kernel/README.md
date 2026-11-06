# 内核侧：把 H700 跑起来（Android 用同一个内核）

Android 并没有给 Allwinner H700 的 BSP。现实的路线是**同一个 mainline 内核**（Linux 6.x，
带 ROCKNIX 的 H700 补丁集）跑 Android 用户态，只需要额外打开 Android 相关的 config。

## 1. 内核来源（二选一）

### A. ROCKNIX 内核（推荐，最省事）

ROCKNIX 的 H700 设备树/补丁就是适配这套硬件的"事实标准"，`rg34xx-sp` 的 DTS 已经在里面：

- `projects/ROCKNIX/devices/H700/linux/dts/allwinner/sun50i-h700-anbernic-rg34xx-sp.dts`
- `projects/ROCKNIX/devices/H700/patches/linux/`
- 它们的 `linux.aarch64.conf` 就是可用的 defconfig 基线

### B. mainline + 补丁

- H700 在 mainline 里是 `arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg35xx-*.dts`，
  但**没有** RG34XX-SP 的板级文件，也没有面板驱动。
- 需要自己带上：
  - `drivers/gpu/drm/panel/panel-mipi.c`（`panel-mipi-dpi-spi`，
    见 ROCKNIX patch `0110-...generic_mipi_panel_driver.patch`）
  - `drivers/input/joystick/rocknix-singleadc-joypad.c`（ROCKNIX 独有）
  - PWM 背光、MMC2、AXP717 等补丁（ROCKNIX `patches/linux/` 里都有）

## 2. 必须的补丁（重要）

**`sun50i-h616.dtsi` 在 mainline 里没有显示引擎、PWM、HDMI 节点。** 也就是说
`&de` / `&tcon_lcd0` / `&tcon_lcd0_out_lcd` / `&hdmi` / `&hdmi_out` / `&pwm` /
`lcd0_rgb888_pins` 这些引用，只有在**打了 ROCKNIX 补丁的内核树**里才存在。
所以要用的补丁（`projects/ROCKNIX/devices/H700/patches/linux/`）：

| 补丁 | 作用 |
|---|---|
| `0002-rg35xx-enable-HDMI-LCD.patch` | 给 `sun50i-h616.dtsi` 加 +308 行：DE/TCON_TOP/TCON_LCD0/HDMI 节点、`lcd0_rgb888_pins`，以及 drm/sun4i 的 H616 支持 |
| `0008-...introduce_allwinner_h616_pwm_controller.patch` | H616 PWM 控制器（`&pwm`、`pwm0`/`pwm2` pinctrl） |
| `0010-rg35xx-enable-pwm-backlight.patch` | PWM 背光接线（PD28） |
| `0110` + `0111` | `panel-mipi.c` 通用 MIPI-DPI-over-SPI 面板驱动 + 板级改用它 |
| `0126` | MMC2（TF2 第二个卡槽） |
| `0140` + `0150` | `rocknix-singleadc-joypad` + 力反馈（震动） |
| `0153` | RGB LED（`function`/`color` 属性） |
| `0155` | 把 RGB 连接器当 DSI 用（面板时序相关） |
| `0159` | 保持第二张 SD 卡供电 |
| `0161`–`0163` | 显示管线/面板/codec 的 suspend 相关（休眠要用） |

直接 `git clone ROCKNIX/distribution` 然后按其 `packages/` 的构建方式打补丁最省事，
省得你手工 rebase 这十几个补丁。

## 3. 本目录提供的文件

| 文件 | 用途 |
|---|---|
| `dts/sun50i-h700-anbernic-rg34xxsp.dts` | 独立的板级 DTS（已把 mainline + ROCKNIX 的板级差异合并成一份） |
| `fragment.config` | 需要叠加到内核 defconfig 上的 Android / 本机硬件选项 |

## 4. 把 DTS 放进内核树

```sh
cp dts/sun50i-h700-anbernic-rg34xxsp.dts \
   <kernel>/arch/arm64/boot/dts/allwinner/

# arch/arm64/boot/dts/allwinner/Makefile 里加一行
dtb-$(CONFIG_ARCH_SUNXI) += sun50i-h700-anbernic-rg34xxsp.dtb
```

## 5. 叠加 Android config

```sh
cd <kernel>
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- defconfig
scripts/kconfig/merge_config.sh -m .config <this-repo>/kernel/fragment.config
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) Image dtbs
```

产物：

- `arch/arm64/boot/Image` → 复制为 `device/anbernic/rg34xxsp-kernel/Image`
- `arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg34xxsp.dtb` → 放到 SD 卡 boot 分区

**注意**：`fragment.config` 里的符号名随内核版本会变（尤其是 AXP717、MUSB、RTW88 的 Kconfig），
`merge_config.sh` 会提示哪些符号没有被识别——以 `menuconfig` 里实际存在的为准，不要照抄。

## 6. 已知必须自己补的东西

- `panel-mipi.c` 的 `anbernic,rg34xx-sp-panel` 条目里是 720x480 的时序，**不要自己编时序**，
  直接搬 ROCKNIX 的实现；如果面板型号不同（批次差异），先用 stock 系统的 `dmesg` 确认。
- `rocknix-singleadc-joypad` 是 out-of-tree 驱动，Android 的输入子系统不需要改动它。
- 内核必须是**非 GKI**的单体镜像（本 device tree 用 boot header v0），因此
  `CONFIG_MODULES` 可以关掉，省掉 vendor_dlkm 那一整套。