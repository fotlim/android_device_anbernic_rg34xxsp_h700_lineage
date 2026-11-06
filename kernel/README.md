# 内核侧：在 Android 上跑 H700

Allwinner 从来没有给 H700 出过 Android BSP，所以 Android 用户态只能配一个
**Linux 7.1.x 内核 + ROCKNIX 的 H700 补丁集**。这个目录就是做这件事的最小集合：
一份板级 DTS、一份 config 片段、和"怎么把 DTS 塞进内核树"的步骤。

## 1. 内核基线（已实测）

ROCKNIX 在 `packages/linux/package.mk` 里把主内核 pin 在 **Linux 7.1.2**：

```
PKG_VERSION="03e2778d1f11de9260543f969e9e888a1c2bf830" # 7.1.2
PKG_URL="https://github.com/torvalds/linux/archive/${PKG_VERSION}.tar.gz"
```

用这个 commit，不要用 mainline 的 HEAD：ROCKNIX 的补丁是照着它 rebase 的，换个版本
就会出现大量 `patch does not apply`。

```sh
git init linux && cd linux
git remote add origin https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
git fetch --depth 1 origin 03e2778d1f11de9260543f969e9e888a1c2bf830
git checkout FETCH_HEAD
```

## 2. 必须打的补丁

**mainline 的 `sun50i-h616.dtsi` 里没有显示引擎、PWM、HDMI 节点**，而面板、
背光、手柄驱动也都不在 mainline。全部依赖来自
`ROCKNIX/distribution` → `projects/ROCKNIX/devices/H700/patches/linux/`（共 31 个）：

| 补丁 | 作用 | 对本机是否关键 |
|---|---|---|
| `0002-rg35xx-enable-HDMI-LCD.patch` | 给 `sun50i-h616.dtsi` 加 DE / TCON_TOP / TCON_LCD0 / HDMI 节点、`lcd0_rgb888_pins`，并改 drm/sun4i | **关键**，没有它就没有显示 |
| `0003-Update-sun8i_tcon_top.c.patch` | TCON_TOP 驱动适配 | 关键 |
| `0007-rg35xx-add-GPU-opp.patch` | Mali-G31 OPP（420–600 MHz，超频点 648） | 建议 |
| `0008-...h616_pwm_controller.patch` | H616 PWM 控制器（`&pwm`、pwm0=PD28 / pwm2=PH2） | **关键**，背光和震动都靠它 |
| `0010-rg35xx-enable-pwm-backlight.patch` | 背光从 gpio-backlight 换成 `pwm-backlight`（PD28，周期 40000 ns） | **关键** |
| `0110-...generic_mipi_panel_driver.patch` | 新增 `drivers/gpu/drm/panel/panel-mipi.c`（`panel-mipi-dpi-spi`） | **关键** |
| `0111-rg35xx-2024-use-panel-mipi-dpi-spi-driver.patch` | 板级改用上面这个面板驱动 | 关键 |
| `0126-...rg35xx_add_additional_hardware_support.patch` | MMC2（第二卡槽 PE22 检测 / PE4 供电）、mmc aliases 重排 | **关键** |
| `0127-enable-mmc1-for-RG35XX-2024.patch` | SDIO Wi-Fi 的 mmc1 | **关键** |
| `0140-rg35xx-2024-use-rocknix-joypad-driver.patch` | 用 `rocknix-singleadc-joypad` 取代 gpio-keys | **关键** |
| `0150-add-forcefeedback.patch` | 震动（PWM2） | 关键 |
| `0153-enable-rgb-leds.patch`、`0901-...fix-led-1-color.patch` | PI12/PI11 两颗 LED | 次要 |
| `0155-sun4i-set-rgb-connector-as-DSI.patch` | RGB 连接器按 DSI 处理（面板时序/模式设置） | 关键 |
| `0156-rg35xx-plus-add-pcf8563-rtc.patch` | R_I2C 上的 PCF8563（0x51） | 建议 |
| `0159-...keep-the-second-sd-card-powered.patch` | 第二卡槽常供电 | 建议 |
| `0161`–`0163` | 显示管线 / 面板 / codec 的 suspend | 休眠才需要 |
| `0007`/`0200`/`0210` | CPU/GPU OPP 表 | 建议 |
| 其余（`0040` `0124` `0151` `0152` `0154` `0157` `0158` `0160` `0204`） | musb/AXP 电池名/OTG host/rocknix-dt-id/PEK/SD data0/HDMI 音频 | 按需 |

> `0203-sound-soc-Add-sunxi_v2-for-h616-ahub.patch.disabled` 在 ROCKNIX 里本来就是关掉的，
> 本机的音频走 `sunxi-snd-codec`，不需要它。

最省事的做法不是手工 `git apply`，而是直接用 ROCKNIX 的构建流程（它会在编译前自动
把这 31 个补丁按顺序打上），然后把本仓库的 DTS 一起丢进去。

## 3. 本目录的 DTS（include 式，不是从零手写）

上游 mainline 已经有 `sun50i-h700-anbernic-rg35xx-2024.dts` / `-plus` / `-sp` 三层继承，
ROCKNIX 又在这套继承上加了面板和手柄节点。所以本机只需要 30 行覆盖：

| 文件 | 内容 |
|---|---|
| `dts/sun50i-h700-anbernic-rg34xx-sp.dts` | include `sun50i-h700-anbernic-rg35xx-sp.dts`；`model/compatible` 换成 `anbernic,rg34xx-sp`；`&panel` 换 `anbernic,rg34xx-sp-panel`；`&joypad` 加 `invert-absrx/invert-absry`、`amux-count = <4>` |
| `dts/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dts` | 面板批次二选一用的变体（compatible 换 `-v2-panel`） |

继承链：

```
sun50i-h700-anbernic-rg34xx-sp.dts
└── sun50i-h700-anbernic-rg35xx-sp.dts      (mainline)
    └── sun50i-h700-anbernic-rg35xx-plus.dts (mainline)
        └── sun50i-h700-anbernic-rg35xx-2024.dts (mainline + ROCKNIX 补丁)
            ├── sun50i-h616.dtsi
            └── sun50i-h616-cpu-opp.dtsi
```

换成 include 式的原因：`rg35xx-sp.dts` 已经把 PMIC、codec、Wi-Fi/BT、盖子开关、
按键、LED、MMC 全部理顺了，而且和本机实测硬件一一对得上（见 `docs/HARDWARE.md`）；
手写一份 15 KB 的板级 DTS 只会多出一堆抄错的引脚。

## 4. 已验证

在本机用 **Linux 7.1.2 + 上述补丁** 的树上编译通过：

```sh
cpp -nostdinc -undef -P -x assembler-with-cpp \
    -I arch/arm64/boot/dts/allwinner -I include \
    dts/sun50i-h700-anbernic-rg34xx-sp.dts -o /tmp/pre.dts
dtc -I dts -O dtb -o /tmp/rg34xx-sp.dtb /tmp/pre.dts
```

- `rc=0`，产物 **35662 字节**（v2 面板版 35678 字节）
- 反编译回来确认：`compatible = "anbernic,rg34xx-sp\0allwinner,sun50i-h700"`、
  `panel` 的 `compatible = "anbernic,rg34xx-sp-panel\0panel-mipi-dpi-spi"`、
  `spi-max-frequency = <3125000>`、`spi-3wire`、`pwms = <&pwm 0 40000 0>`、
  `rocknix-singleadc-joypad` 带 `invert-absrx/invert-absry/amux-count = <4>`、
  `rtc@51` 是 `nxp,pcf8563`
- 只有 11 条 dtc 风格警告（`simple_bus_reg`、`unit_address_vs_reg` 之类），全部来自上游
  `h616.dtsi`，不是本文件引入的

复现脚本：`tools/verify-dts.sh`（会在临时目录里拉 7.1.2 的 DTS 树、打补丁、
再 cpp+dtc 编译）。

⚠️ 一个诚实的提醒：直接把补丁 `git apply` 到 7.1.2 上时，有 9 个 hunk 会因为上下文漂移
被拒（`0126` 的 `reg_vcc3v3_mmc2`、`0152` 的 `reg_usb0_vbus`、`0140` 的 joypad 节点等）。
`tools/verify-dts.sh` 会用脚手架节点把它们补上以完成编译验证；**真正构建时请用 ROCKNIX
的构建系统打补丁**（它 pin 的版本和补丁顺序是吻合的），不要照抄这个脚手架。

## 5. 放进内核树

```sh
cp kernel/dts/*.dts <kernel>/arch/arm64/boot/dts/allwinner/

# arch/arm64/boot/dts/allwinner/Makefile
dtb-$(CONFIG_ARCH_SUNXI) += sun50i-h700-anbernic-rg34xx-sp.dtb
dtb-$(CONFIG_ARCH_SUNXI) += sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb
```

## 6. 叠加 Android config

```sh
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- defconfig
scripts/kconfig/merge_config.sh -m .config <this-repo>/kernel/fragment.config
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) Image dtbs
```

产物：

- `arch/arm64/boot/Image` → `device/anbernic/rg34xxsp-kernel/Image`
- `arch/arm64/boot/dts/allwinner/sun50i-h700-anbernic-rg34xx-sp.dtb` → boot 分区

`fragment.config` 里的符号名会随内核版本变化（AXP717、MUSB、RTW88 的 Kconfig 最善变），
`merge_config.sh` 会打印哪些符号没被识别——以 `menuconfig` 里实际存在的为准。

## 7. ⚠️ 面板固件（点不亮的头号原因）

`panel-mipi.c` **不是硬编码时序的驱动**：它在 probe 时读 DTS 的**第一个** compatible，
拼成文件名去 `request_firmware()`：

```
panels/<compatible>.panel      →  本机是 panels/anbernic,rg34xx-sp-panel.panel
```

读不到就直接 `dev_err_probe(... "Failed to load firmware")` 并返回错误，面板根本不会 probe
（表现为：背光亮、屏幕黑、`dmesg | grep panel-mipi` 有 Failed to load firmware）。

这个 `.panel` 文件同时装着 **初始化命令序列 + 显示时序 + 面板物理尺寸**，格式是
`struct panel_firmware_header`（15 字节 magic + version=1）/ `panel_firmware_config`
（width_mm、height_mm、bus_flags、num_timings）/ `panel_firmware_panel_timing[]`（be16：
hactive、hfp、hsync、hbp、vactive、vfp、vsync、vbp）+ 命令块。
可以用 [panel-firmware-generator](https://kikuchan.github.io/panel-firmware-generator/) 生成，
参数就用 `docs/HARDWARE.md` §2 里实测的值（720×480、26 MHz、hspw 2 / hbp 46 / ht 820、
vspw 5 / vbp 20 / vt 536）。

放到 Android 侧的话，文件要在 `/vendor/firmware/panels/`（本设备树的 `device.mk` 里
`PRODUCT_COPY_FILES` 的 firmware 目录），并在 BoardConfig 里保持 `TARGET_COPY_TO_SYSTEM` 之外
的 firmware 路径不被 sepolicy 挡掉。

> 出处已经定位清楚了：ROCKNIX 的 H700 设备配置
> `projects/ROCKNIX/devices/H700/config/kernel-firmware.dat` 里明确列着
> `panels/anbernic,rg34xx-sp-panel.panel`（以及 `panels/anbernic,rg34xx-sp-v2-panel.panel`），
> 说明这些文件是跟着 ROCKNIX 的 linux-firmware 包发出来的，**不在 git 树里能直接 grep 到的地方**。
> 所以在拿到二进制之前，最稳的路子是：从一份 ROCKNIX H700 镜像的
> `/usr/lib/firmware/panels/` 里把它拷出来；其次才是拿 `docs/HARDWARE.md` §2 的时序
> 自己用 panel-firmware-generator 生成（这样就还得自己搞定初始化命令序列）。

## 8. 已知要自己确认的东西

- **PMIC 型号**：本机实测是 `x-powers,axp2202`（R_I2C `0x34`），而 mainline 驱动只认
  `x-powers,axp717`。两者寄存器/rail 布局一致，ROCKNIX 用 axp717 节点跑这张板且能工作，
  但如果你遇到电压相关的怪问题，先查这里。
- **面板批次**：stock 固件 dmesg 里是 `lcd_panel_fog_fj035fhd05_v1_init`，
  对应 `anbernic,rg34xx-sp-panel`；若黑屏无输出，试 v2 面板变体。
- **`panel-mipi.c` 的时序**：不要自己编，直接搬 ROCKNIX 的实现（驱动里已经带 720×480 时序）。
- 内核必须是**非 GKI** 的单体镜像（设备树用 boot header v0），所以 `CONFIG_MODULES`
  可以关掉，省掉 vendor_dlkm 那一整套。