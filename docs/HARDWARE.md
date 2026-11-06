# 硬件事实表（RG34XX-SP）

这一份**不是从网上抄的**：全部来自本机真机（Anbernic 原厂固件）上直接读出来的值。
方法见文末"怎么自己再验一遍"。

## 0. 这台机器现在跑的是什么

| 项 | 值 | 出处 |
|---|---|---|
| 系统 | Anbernic 官方 **Ubuntu 22.04**（不是 ROCKNIX、不是 mainline） | `/etc/os-release` |
| 内核 | `4.9.170 #9 SMP PREEMPT Tue May 26 15:49:58 CST 2026` | `uname -a` |
| U-Boot | `2018.05 (05/26/2026 - 15:48:53)` | `/proc/cmdline` + 串口 |
| 设备树 | `allwinner,h616` + `arm,sun50iw9p1`（全志 BSP 内核） | `/proc/device-tree/compatible` |
| hostname | `ANBERNIC` | `hostname` |
| 登录 | `root` / `root` | 实测 |

⚠️ 关键影响：**原厂内核是全志 BSP 4.9.170，不是 mainline**。所以
`/sys/firmware/fdt` 里的节点命名（`lcd0@01c0c000`、`sdc0@04020000`、`twi@0x07081400`…）
和 mainline 的 `sun50i-h616.dtsi` 完全不同，不能直接拿来做 Android 设备树，
但**引脚和器件型号是对得上的**，这正是能反推出 DTS 的原因。

## 1. SoC / 电源

| 项 | 值 |
|---|---|
| SoC | Allwinner **H700**（4×Cortex-A53，Mali-G31 MP2） |
| CPU OPP | 480 MHz – 1.416 GHz（mainline `sun50i-h616-cpu-opp.dtsi` + ROCKNIX `0200`） |
| GPU OPP | 420 / 480 / 600 MHz，超频点 648（ROCKNIX `0007`） |
| PMIC | **`x-powers,axp2202`**，挂 **R_TWI（`0x07081400`）地址 `0x34`** |
| PMIC 中断 | `r_intc`，`interrupts = <0 0x08>` |
| 实测电压 | dcdc1 1.16 V / dcdc2 0.94 V / dcdc3 1.20 V / dcdc4 1.00 V / aldo+aldo4+bldo 1.8 V / cldo1–4 3.3 V / cpusldo 0.90 V |
| 电池 | 3.3 Ah，满电 ~4.08 V，`simple-battery` 参数沿用上游（1024 mA / 4.2 V） |
| RTC | **PCF8563 @ R_TWI `0x51`**（`nxp,pcf8563`），同时给 Wi-Fi 提供 32 kHz |

> mainline 的 `axp20x` 驱动里**没有 `axp2202`**，只有 `axp717`。两个器件的 rail 名称
> 与结构一致（dcdc1..4 / aldo1..4 / bldo1..4 / cldo1..4 / cpusldo / vmid / drivevbus），
> ROCKNIX 就是用 `axp717` 节点跑这张板的。若遇到电压异常，先怀疑这里。

## 2. 显示

| 项 | 值 |
|---|---|
| 面板 | 3.5" **720×480 横屏 3:2**，stock 驱动名 `rg34xxsp_v1`，初始化函数 `lcd_panel_fog_fj035fhd05_v1_init` |
| 接口 | RGB（DPI）`lcd_if = 0`，不是 MIPI-DSI |
| dclk | **26 MHz** |
| 行时序 | `hspw 2 / hbp 46 / ht 820` → **hfp 52** |
| 场时序 | `vspw 5 / vbp 20 / vt 536` → **vfp 31** |
| 其它 | `lcd_hv_clk_phase 1`、`lcd_hv_sync_polarity 3`、`lcd_frm 1` |
| 面板初始化 | 3 线 SPI 位操作（stock 里没有 `spi_master`，全志 BSP 用 GPIO 抠） |
| GPIO | **PI9 = SCK、PI10 = MOSI、PI8 = CS、PI14 = RESET、PI15 = POWER**（都高有效） |
| 背光 | **PWM0 / PD28**，50 kHz，`lcd_pwm_max_limit = 0xff`（这个值不是 2499） |
| framebuffer | `/dev/fb0`，stride 2880，XRGB8888（在 mainline 下则由 DRM 接管） |
| HDMI | `hdmi@06000000`，`hdmi_used = 1`，stock 启动时 fb0 被 HDMI 占用（1280×1024） |

在 mainline/ROCKNIX 下，这些对应关系是：`panel-mipi-dpi-spi` 驱动（`0110`）负责 SPI
初始化序列和时序，`tcon_lcd0` 输出 RGB，`pwm-backlight`（`0010`）负责 PD28 背光。
**时序在驱动里，不在 DTS 里**，所以 DTS 只需给对 compatible。

## 3. 输入

### 3.1 stock 固件里的按键（`gpio-keys-polled`，节点 label `ANBERNIC-keys`，poll-interval 20）

| 功能 | 引脚 | stock 用的 code |
|---|---|---|
| UP | PA6 | `EV_ABS 0x11`（ABS_HAT0Y） |
| DOWN | PE0 | `EV_ABS 0x11` |
| LEFT | PA8 | `EV_ABS 0x10`（ABS_HAT0X） |
| RIGHT | PA9 | `EV_ABS 0x10` |
| A | PA0 | `0x130` BTN_SOUTH |
| B | PA1 | `0x131` BTN_EAST |
| X | PA3 | `0x132` BTN_C |
| Y | PA2 | `0x133` BTN_NORTH |
| L1 | PA10 | `0x134` BTN_TL |
| R1 | PA12 | `0x135` BTN_TR |
| SELECT | PA5 | `0x136` BTN_SELECT |
| START | PA4 | `0x137` BTN_START |
| MENU/MODE | PE3 | `0x138` BTN_MODE |
| L3 | PE8 | `0x139` BTN_THUMBL |
| L2 | PA11 | `0x13a` BTN_TL2 |
| R2 | PA7 | `0x13b` BTN_TR2 |
| R3 | PE9 | `0x13c` BTN_THUMBR |
| VOL+ | PE1 | `0x73` KEY_VOLUMEUP |
| VOL− | PE2 | `0x72` KEY_VOLUMEDOWN |
| 盖子（hall） | **PE7** | `SW_LID`（debugfs 里叫 `hall switch`） |

> **A/B 与 X/Y 的"谁是谁"，stock 和 mainline 是反的。**
> stock 的 `gpio-keys-polled` 用 PA0 = BTN_SOUTH、PA1 = BTN_EAST；
> 而 ROCKNIX 的 `rocknix-singleadc-joypad` 节点（patch `0140`）明确写的是
> `sw5 PA0 = BTN_EAST`（label `GPIO BTN-A`）、`sw6 PA1 = BTN_SOUTH`（`BTN-B`）、
> `sw7 PA3 = BTN_NORTH`（`BTN-X`）、`sw8 PA2 = BTN_WEST`（`BTN-Y`），D-pad 用
> `BTN_DPAD_*`（不是 ABS_HAT0X/Y）。
> 本仓库用的是后者，所以 `keylayout/Vendor_484b_Product_14df.kl` 按 mainline 的 code 映射：
> `305 BTN_EAST → BUTTON_A`、`304 BTN_SOUTH → BUTTON_B`、`307 → BUTTON_X`、`308 → BUTTON_Y`。
> 如果你按 stock 的 dmesg 去对，会觉得 A/B 反了——以能跑起来的这套内核为准。

### 3.2 模拟摇杆（ADC 复用）

| 项 | 值 |
|---|---|
| 使能 | `amux-en-gpios` = **PI0** |
| 选择 | `A0_gpio` = **PI1**、`A1_gpio` = **PI2** |
| ADC | `gpadc` channel 0（`io-channels = <&gpadc 0>`） |
| 供电使能 | `adc-en-gpios` = **PE11** |
| 通道映射 | `<0 2 1 3>` = ABS_RY、ABS_RX、ABS_Y、ABS_X（`amux-count = 4`，即双摇杆） |
| 反相 | `invert-absx/invert-absy` + RG34XX-SP 额外要 `invert-absrx/invert-absry` |

### 3.3 震动

PWM 通道 2，引脚 **PH2**；mainline 下由 `rocknix-singleadc-joypad` 的
`pwms = <&pwm 2 1000000 0>` 驱动（`0150` 补丁加 forcefeedback）。

## 4. 存储

| 项 | stock 节点 | mainline 别名 | 说明 |
|---|---|---|---|
| **eMMC** | `sdc0@04020000` | `mmc0` | non-removable、bus-width 4，**58.24 GiB** |
| TF 卡槽 | `sdc2@04022000` | `mmc2`（Android 下是 `mmcblk1`） | `cd-gpios = PE22`、供电 `PE4`（debugfs: `sdc2 power enable`） |
| SDIO Wi-Fi | `sdc1@04021000` | `mmc1` | `no-sd`/`no-mmc`，max 150 MHz |

⚠️ 纠正一个常见误解：**这台机器的系统在 eMMC 上，不是"启动 TF 卡"**。
原厂 GPT 的 8 个分区全在 `mmcblk0`（eMMC）上：

| 分区 | 名字 | 大小 | stock 用途 |
|---|---|---|---|
| p1 | `special` | 64 MiB | 全志特殊分区 |
| p2 | `boot-resource` | 32 MiB | 全志 boot 资源 |
| p3 | `env` | 16 MiB | U-Boot env |
| p4 | `boot` | 64 MiB | **内核 + bootargs**（`boot_type=1`） |
| p5 | `rootfs` | 7 GiB | Ubuntu |
| p6 | `appfs` | 4 GiB | 应用 |
| p7 | `UDISK` | 512 MiB | 杂项 |
| p8 | `primary` | 46 GiB | 数据 |

stock U-Boot 的 bootargs 里带 `androidboot.mode=normal`、`androidboot.hardware=sun50iw9p1`、
`boot_type=1`、`gpt=1` —— 这是**全志 Android 系 U-Boot**，理论上可能直接吃经典 Android
boot.img。这条捷径尚未验证（见 `docs/PORTING.md` M0）。

## 5. 无线

| 项 | 值 |
|---|---|
| Wi-Fi | **RTL8821CS**，SDIO（`mmc1`），驱动 `8821cs`（RTW），`wlan_power = axp2202-cldo4` |
| Wi-Fi 复位 | **没有 reset GPIO**（stock 的 `wlan` 节点里没有） |
| MAC | `68:8f:c9:ca:29:40`（wlan0）／`6a:8f:c9:ca:29:40`（wlan1） |
| 蓝牙 | UART1，`/dev/ttyS1`；`bt_rst_n = PG19`、`bt_wake = PG17`、`bt_hostwake = PG16`（中断） |
| 备注 | mainline 的 `rg35xx-plus.dts` 用 **PG18** 当 wifi_pwrseq reset、**PG15** 当 SDIO 中断；实测 PG18 在 stock 里**没被任何驱动占用**，PG15 是 Wi-Fi IRQ —— 与上游一致 |

## 6. 音频 / 其它

| 项 | 值 |
|---|---|
| 音频 | `codec@0x05096000`（`allwinner,sunxi-snd-codec`）+ AHUB |
| 功放供电 | `allen_amp_power = axp2202-cldo1`，功放使能 **PI5**，耳机检测 **PI3**（debugfs: `Headphone detection`） |
| USB | `udc = 5100000.udc-controller`；`usb_id_det` = **PI4** |
| LED | **PI12** = `work_led_ctl`（电源灯）、**PI11** = `low_pwr_led_ctl`（状态灯） |
| 未知器件 | `i2c-3`（`twi3`, `0x5002c00`）上有 **`3-003c`**，型号未确认（不影响开机） |
| 调试串口 | UART0（PH 引脚），115200 8N1 |

## 7. 还没确认的三件事

1. ~~**DDR3 还是 DDR4**~~ **已确认：LPDDR3。** 依据 ROCKNIX `bootloader/update.sh` 的判据
   （`vdd-dram` 1200 mV = DDR3 板、1100 mV = DDR4 板），本机 `axp2202-dcdc3 = 1200000 µV`
   → U-Boot 用 `anbernic_rg35xx_h700_lpddr3_defconfig`。内存 1 GiB（`MemTotal` 996480 kB）。
2. **`3-003c` 是什么器件**：可能是某个传感器/触控（本机无触摸屏），也可能是 PMIC 的
   附属芯片。不影响 M0。
3. **原厂 U-Boot 能否直接引导 Android boot.img**：见上文 §4。

## 8. 怎么自己再验一遍

本仓库的 `tools/collect-facts.sh` 会把上面所有值重新采一遍（只读，不改系统）：

```sh
scp tools/collect-facts.sh root@<设备IP>:/tmp/
ssh root@<设备IP> "sh /tmp/collect-facts.sh" > facts.txt
ssh root@<设备IP> "cat /sys/firmware/fdt" > rg34xxsp-stock.dtb   # 然后 dtc -I dtb -O dts
```

> 拉 DTB 的小坑：`ssh ... "cat /sys/firmware/fdt" | head` 会因为 SIGPIPE 截断文件，
> 反编译时报 "Blob has incorrect magic number"。**先落盘再反编译**。