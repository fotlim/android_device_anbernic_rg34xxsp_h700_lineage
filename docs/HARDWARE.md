# 硬件档案：Anbernic RG34XXSP（Allwinner H700）

本文件是所有 DTS / 内核 / 设备树配置的事实来源。每条都标注了出处和可信度。

## 可信度标记

| 标记 | 含义 |
|---|---|
| ✅ | 来自**官方主线内核 DTS**，即文件里逐字存在 |
| 🟢 | 来自 ROCKNIX 的 H700 设备插件（这套硬件在 Linux 上实际在跑的配置） |
| 🟡 | 来自 birdOS 的 `bird-device-contract.tsv`（面向本机的用户态读硬件合同，数值是实测） |
| ❓ | 推断/待实物确认，**上手前请先核对** |

---

## 1. SoC

| 项 | 值 | 来源 |
|---|---|---|
| SoC | Allwinner H700（`allwinner,sun50i-h700`），与 H616 同 die | ✅ |
| CPU | 4× Cortex-A53 | ✅ |
| 频率 | 480 MHz – 1.416 GHz（OPP 表 `sun50i-h616-cpu-opp.dtsi`） | ✅ |
| GPU | Mali-G31 MP2，devfreq `1800000.gpu`，420–600 MHz（可超 648） | 🟡🟢 |
| GPU governor | 只用 `simple_ondemand` 与 `userspace`，无 performance | 🟡 |
| DRAM | LPDDR4，电压由 AXP717 DCDC3 = 1.1 V 供电 | ✅ |
| 电源 | AXP717（R_I2C 0x34），含 ADC、电池、USB 供电单元 | ✅ |

> ⚠️ H700 有 **DDR3 / DDR4 两种 U-Boot 版本**（区别就是 `vdd-dram`：DDR3 = 1.20 V，
> DDR4 = 1.10 V）。刷错 U-Boot 直接不开机，且不会报错。刷之前先确认板上颗粒丝印。

## 2. 显示

| 项 | 值 | 来源 |
|---|---|---|
| 面板 | 720×480，横屏，3:2，24-bit RGB，IPS | 🟡🟢 |
| 面板控制器 | MIPI-DPI-over-SPI（`panel-mipi-dpi-spi`），驱动 `panel-mipi.c` | 🟢 |
| compatible | `anbernic,rg34xx-sp-panel` | 🟢 |
| 控制总线 | bit-banged SPI：SCK **PI9**、MOSI **PI10**、CS **PI8**，3-wire，3.125 MHz | 🟢 |
| 复位 | **PI14**，低有效 | 🟢 |
| 面板电源 | **PI15**，高有效（`vdd-lcd` 3.3 V） | 🟢 |
| 数据通路 | DE → TCON_LCD0 → RGB888，pinctrl `lcd0_rgb888_pins`（PD 组） | ✅🟢 |
| 背光 | PWM0 / **PD28**，pwm-backlight，周期 40000 ns（25 kHz） | 🟢 |
| 背光满量程 | raw 2499（`maximum_raw`），冷启动默认 5% | 🟡 |
| 亮度节点 | `/sys/class/backlight/backlight/brightness` | 🟡 |
| 旧 framebuffer | `/dev/fb0`，stride 2880，XRGB8888（ROCKNIX 的 fbdev 路径，Android 不用） | 🟡 |
| 视频输出 | mini-HDMI（`hdmi-connector` type d） | 🟢 |

Android 侧要点：**没有触摸屏**，没有加速度计（所以 overlay 里关掉自动旋转）。
720×480 → 240 dpi 是最合适的密度。

> ⚠️ **显示节点不在 mainline 里。** `&de`、`&tcon_lcd0`、`&tcon_lcd0_out_lcd`、`&hdmi`、
> `&hdmi_out`、`&pwm`、`lcd0_rgb888_pins` 全部由 ROCKNIX 的补丁 `0002`（DE/TCON/HDMI，
> +308 行到 `sun50i-h616.dtsi`）和 `0008`（H616 PWM 控制器）提供。
> 拿纯 mainline 内核编本仓库的 DTS 会直接失败，这是预期的——见 `kernel/README.md` §2。

## 3. 输入

设备名 **"H700 Gamepad"**，bus `0x19`，vendor `0x484b`，product `0x14df`，version `0x0100`，
驱动 `rocknix-singleadc-joypad`。事件能力 `0x20000b`，绝对轴 `0x1b`，力反馈 106/107030000。

### 按键矩阵（GPIO，均为低有效）

| 功能 | 引脚 | Linux code | 来源 |
|---|---|---|---|
| DPAD-UP | PA6 | `BTN_DPAD_UP` | 🟢 |
| DPAD-DOWN | PE0 | `BTN_DPAD_DOWN` | 🟢 |
| DPAD-LEFT | PA8 | `BTN_DPAD_LEFT` | 🟢 |
| DPAD-RIGHT | PA9 | `BTN_DPAD_RIGHT` | 🟢 |
| A | PA0 | `BTN_EAST` | 🟢 |
| B | PA1 | `BTN_SOUTH` | 🟢 |
| X | PA3 | `BTN_NORTH` | 🟢 |
| Y | PA2 | `BTN_WEST` | 🟢 |
| SELECT | PA5 | `BTN_SELECT` | 🟢 |
| START | PA4 | `BTN_START` | 🟢 |
| MENU（丝印 F） | PE3 | `BTN_MODE` | 🟢 |
| L1 | PA10 | `BTN_TL` | 🟢 |
| L2 | PA11 | `BTN_TL2` | 🟢 |
| R1 | PA12 | `BTN_TR` | 🟢 |
| R2 | PA7 | `BTN_TR2` | 🟢 |
| L3 | PE8 | `BTN_THUMBL` | 🟢 |
| R3 | PE9 | `BTN_THUMBR` | 🟢 |

### 摇杆

两个摇杆共用 **一个 ADC 通道**（`gpadc` channel 0），靠 2-bit GPIO 模拟开关切换：

- 使能/输出：**PI0**（`joy_mux_pin`，gpio_out）
- 选择位：**PI1** = A、**PI2** = B
- `amux-channel-mapping = <0 2 1 3>` → ABS_RY / ABS_RX / ABS_Y / ABS_X
- 右摇杆在这块板子上是**反向**的（`invert-absx` / `invert-absy` 对右摇杆生效）

### 其他输入

| 功能 | 引脚 | 说明 |
|---|---|---|
| 音量+ | PE1 | `gpio-keys`，`KEY_VOLUMEUP` ✅ |
| 音量− | PE2 | `KEY_VOLUMEDOWN` ✅ |
| 翻盖（上盖闭合检测） | PE7 | `EV_SW`/`SW_LID`，唤醒源，低有效 ✅ |
| 震动马达 | PWM2 / **PH2** | 1 ms 周期，`rumble_enable` 节点在 |
| LED 电源 | PI12 | 绿色 |
| LED 状态 | PI11 | 绿色 |

## 4. 音频

- 编解码器：H616/H700 内置 codec，卡名 **"audiocodec"** 🟡
- 功放使能：**PI5**（`allwinner,pa-gpios`），高有效
- 耳机检测：**PI3**（`hp-det-gpios`）
- 扬声器与耳机经 74HC4052D 模拟开关切换 ✅
- ⚠️ H700 的 codec 在主线里只实现了播放路径；**录音不可用**（没有麦克风，也没必要）

## 5. 存储

| 口 | 控制器 | 卡检测 | 电源 | 用途 |
|---|---|---|---|---|
| TF1 | `mmc0` | PF6（低有效） | `reg_cldo3` 3.3 V | 启动卡 / OS |
| TF2 | `mmc2` | PE22（低有效） | 外部 3.3 V 由 **PE4** 使能 | 第二个卡槽（ROM 卡） |

- 都没有 1.8 V 切换（`no-1-8-v`），都禁用写保护（`disable-wp`）。
- 卡上分区（保存下来的原厂布局，仅供参考；本项目自己的布局见 `tools/mk-sdcard.sh`）：
  p1 vfat boot、p5 ext4 root、p6 exfat content 🟡

## 6. 无线

继承自 RG35XX Plus 的接线（RG34XX 系列共用主板设计）：

| 项 | 值 | 来源 |
|---|---|---|
| Wi-Fi 模块 | RTL8821CS（SDIO，`mmc1`，非可插拔） | ✅🟢 |
| SDIO IRQ | **PG15**（低有效） | ✅ |
| 模块电源 | `reg_cldo4` 3.3 V / `reg_aldo4` 1.8 V | ✅ |
| BT | 同一模块的 BT 半，UART1 + RTS/CTS | ✅ |
| BT enable | **PG19** | ✅ |
| BT host-wake | **PG16** | ✅ |
| BT device-wake | **PG17** | ✅ |

❓ **需要实物确认**：部分 RG34XX / RG34XX-SP 批次换过 Wi-Fi 模块（有 AIC8800 / RTL8723DS 的传闻）。
上手第一件事：用原厂系统 `dmesg | grep -i -E "mmc1|sdio|wifi"` 和
`ls /sys/bus/sdio/devices/*/` 确认 vendor/device id，再决定要不要保留 `&mmc1` 节点。

## 7. 其他外设

| 项 | 值 |
|---|---|
| USB-C | `usbotg`（MUSB），`dr_mode = "peripheral"` 用于 adb/fastboot；OTG 供电由 AXP717 USB 供电单元管 |
| USB 2.0 host（EHCI/OHCI0） | ✅ 在 DTS 里，H 系列才有独立 USB-A 口；本机为 OTG 复用 |
| 调试串口 | **UART0**（PH 组），115200 8N1 — 砖机时唯一的救命通道 |
| 外部 RTC | PCF8563，R_I2C 0x51（换电池时保时） |
| 电源键 | AXP717 PEK（`INPUT_AXP20X_PEK`） |

## 8. 已知的边界

- 主线内核**没有** RG34XX 的板级文件，本仓库的 DTS 是把 `rg35xx-2024` / `rg35xx-sp` /
  `rg35xx-plus` 三者 + ROCKNIX 板级差异合并出来的。
- 面板 720×480 的**时序参数在驱动里**（`panel-mipi.c` 的 `anbernic,rg34xx-sp-panel` 条目），
  不要自己编；不同批次面板可能不同，先用原厂系统 dump 出来。
- 本机没有摄像头、没有 GPS、没有蜂窝网络。