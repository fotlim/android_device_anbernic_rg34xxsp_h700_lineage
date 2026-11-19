# Anbernic RG34XX-SP — Android 设备树

> 给 Allwinner H700 翻盖掌机 **RG34XX-SP** 做的 **LineageOS 18.1 / Android 11** 设备树 + 内核 DTS。
> 官方没有 Android BSP，所以这份东西是从 mainline Linux + ROCKNIX 的 H700 支持反推、
> 再用真机（Anbernic 原厂 Ubuntu 固件）逐项校验出来的。

## 现在到哪一步了

| 部分 | 状态 | 说明 |
|---|---|---|
| 硬件事实 | ✅ 真机实测 | 引脚、面板时序、PMIC、存储、无线全部从原厂固件读出来，见 `docs/HARDWARE.md` |
| 内核 DTS | ✅ 编译通过 | include 式覆盖，在 Linux 7.1.2 + ROCKNIX 补丁树上 `dtc` rc=0 |
| Android 设备树 | ✅ 完整 | 分区、boot header v0、super、fstab、init、keylayout、overlay、sepolicy |
| 真机开机 | ✅ 到桌面 | init→zygote→system_server→SurfaceFlinger→SystemUI→Launcher 全通，user 0 可解锁、`sys.boot_completed=1` |
| 图形 | ✅ 出画面 | Mesa 20.3.4 + Panfrost（Mali-G31），SurfaceFlinger 正常合成；DRM master 之争已解决 |
| 桌面 | 🟠 修补待验证 | Launcher3 曾因缺 `appwidget` 系统服务而 NPE 崩溃循环，已打空保护并重编 `TrebuchetQuickStep` |
| HAL | ❌ 大部分未做 | 显示/图形已通；**音频、WiFi、电源管理（深睡）** 仍未实现；`com.android.settings` 未编译 |
| 输入 | 🟠 按键可用 | 无触摸屏；方向键/按键能导航，**摇杆当鼠标**在做（`patches/joymouse`） |

> 完整踩坑复盘见 **`docs/JOURNEY.md`（开发历程）**，逐条问题见 `docs/ISSUES.md`。
> 想少走弯路：先按 `docs/BUILD.md` 走到能开机（M2），再谈交互与外设。

## 目录结构

```
anbernic-rg34xxsp/
├── device/anbernic/rg34xxsp/        # Android 设备树（直接放进源码树）
│   ├── BoardConfig.mk              # 分区、boot header、super、sepolicy、无线
│   ├── device.mk                   # 拷贝项、属性、产品形态
│   ├── lineage_rg34xxsp.mk         # lunch 目标
│   ├── AndroidProducts.mk
│   ├── fstab.rg34xxsp              # super 动态分区 + by-name + zram
│   ├── init/                       # init / ueventd（背光、游戏手柄、rfkill）
│   ├── keylayout/                  # H700 Gamepad → Android 键码 + 摇杆轴
│   ├── overlay/                    # 无触摸、不旋转、720x480@240dpi
│   └── sepolicy/vendor/            # 背光/LED/rfkill 的最小策略
├── kernel/
│   ├── dts/sun50i-h700-anbernic-rg34xx-sp.dts            # ← 核心：板级覆盖（30 行）
│   ├── dts/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dts   # 面板批次二选一
│   ├── fragment.config             # 内核 Android + 本机硬件 config 片段
│   └── README.md                   # 内核基线、31 个补丁、怎么塞进内核树
├── docs/
│   ├── HARDWARE.md                 # 真机实测引脚/面板/PMIC/存储/无线
│   ├── BUILD.md                    # 从 repo init 到插卡上电
│   └── PORTING.md                  # 还缺哪些 HAL、验收标准、里程碑
└── tools/
    ├── build.sh                    # Lineage 构建（WSL/Linux）
    ├── mk-sdcard.sh                # GPT + U-Boot + boot 分区 + super.img
    ├── collect-facts.sh            # 真机只读体检（重采 docs/HARDWARE.md 的全部值）
    ├── verify-dts.sh               # 拉内核树 + 打补丁 + cpp/dtc 验证 DTS
    └── watch-serial.ps1            # Windows 侧串口日志
```

## 这台机器（摘要）

| 项 | 值 |
|---|---|
| SoC | Allwinner H700（4×A53 @1.416 GHz，Mali-G31 MP2） |
| 屏幕 | 720×480 横屏 3:2，**RGB/DPI** 直连（不是 MIPI-DSI），3 线 SPI 初始化；dclk 26 MHz，hfp 52 / vfp 31 |
| 面板 | `FOG FJ035FHD05`（stock dmesg 实证），compatible `anbernic,rg34xx-sp-panel` |
| 背光 | PWM0 / PD28，50 kHz，`pwm-backlight` |
| 输入 | "H700 Gamepad" `0019:484b:14df`，17 键 + 双摇杆（ADC 复用 PI1/PI2）+ PWM2(PH2) 震动 + PE7 盖子 |
| 存储 | **eMMC 58.24 GiB = mmc0（系统在这里）**；TF 卡槽 = mmc2（PE22 检测 / PE4 供电） |
| 无线 | RTL8821CS（SDIO `mmc1` + UART1 BT，PG15 IRQ / PG16/17/19 BT 控制） |
| 电源 | **AXP2202**（R_I2C 0x34，mainline 用 axp717 节点兼容）+ PCF8563 RTC（0x51） |
| 音频 | `sunxi-snd-codec` + AHUB，功放 cldo1 供电、使能 PI5、耳机检测 PI3 |
| 调试 | UART0（PH），115200 8N1 |

细节和出处都在 `docs/HARDWARE.md`。

## 快速开始

```sh
# 1) 内核（见 kernel/README.md）：Linux 7.1.2 + ROCKNIX H700 补丁 + kernel/fragment.config
#    产物 Image 放到 device/anbernic/rg34xxsp-kernel/Image

# 2) 验证 DTS（可选，但建议先跑一遍）
./tools/verify-dts.sh

# 3) Android
cd ~/android/lineage && source build/envsetup.sh
breakfast lineage_rg34xxsp-userdebug
mka bacon

# 4) 做卡 / 刷机（本机已确认为 LPDDR3 → 用 DDR3 那份 U-Boot）
sudo ./tools/mk-sdcard.sh /dev/sdX --force --uboot H700_DDR3_u-boot-sunxi-with-spl.bin \
     --image-dir out/target/product/rg34xxsp \
     --dtb <path>/sun50i-h700-anbernic-rg34xx-sp.dtb
```

## 设计上的决定（和原因）

1. **目标选 LineageOS 18.1（Android 11），不是 22.2**：这台机器只有 **1 GiB LPDDR3**。
   Android 13/15 在 1 GiB 上光是冷启动之后系统就只剩个位数 MiB 空闲，桌面动画都会掉帧；
   Android 11 + `ro.config.low_ram=true`（本仓库已开）是在这个内存档位上还「能用」的上限。
   想要更轻就往 17.1（Android 10）退，device 树只差 product 继承和 API level。
：这台机器的"同类用户"都在跑 ROCKNIX（Linux），
   而 Android 侧唯一有参考价值的社区产物是 Lineage 设备树；用户现有构建脚本也是 Lineage。
2. **DTS 走 include，不手写整份板级文件**：上游 mainline 已经有
   `rg35xx-2024 → plus → sp` 三层继承，ROCKNIX 又在其上加了面板/手柄节点，
   而本机实测硬件与之**逐项对得上**（见 `docs/HARDWARE.md`）。手写 15 KB 只会抄错引脚。
   我们只写 30 行覆盖：`model/compatible`、`&panel`、`&joypad`。
3. **不用 GKI / vendor_boot**：sunxi 的 U-Boot 对经典 Android boot image（header v0）
   支持最好，DTS 走 FAT 分区的 `extlinux.conf` 加载，省掉 boot header 魔改和 AVB 签名。
4. **GPT 而不是 MBR**：GPT 有分区名，Android 的 ueventd 会自动生成
   `/dev/block/by-name/*`，fstab 比写死 `mmcblk0p15` 稳。
5. **`androidboot.boot_devices=mmcblk0`**：本机系统在 **eMMC**（`mmc0`）上。
   stock 固件就是 `root=/dev/mmcblk0p5`，所以 Android 也从 mmcblk0 起。
6. **动态分区 super = 4 GiB**：留得下，system+vendor+product+system_ext 够用。
7. **内核单体（不拆 vendor_dlkm）**：少一层依赖，先让它能开机。
8. **依赖 ROCKNIX 的 H700 补丁集**：mainline 的 `sun50i-h616.dtsi` 里没有 DE/TCON/HDMI/PWM，
   面板与手柄驱动也都不在上游，见 `kernel/README.md` §2。
9. **SELinux 先 permissive**：HAL 还没写，enforcing 只会让你在 M0 卡三天。
10. **FBE 不启用**：H700 没有 TEE/keymaster，硬件加密做不了；`/data` 先明文。

## 来源与致谢

- [mainline Linux — `sun50i-h700-anbernic-rg35xx-*.dts`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/arch/arm64/boot/dts/allwinner)
  （Ryan Walklin、Chris Morgan）：SoC、按键、音量、盖子、PMIC、存储、Wi-Fi/BT 接线
- [ROCKNIX/distribution](https://github.com/ROCKNIX/distribution) `projects/ROCKNIX/devices/H700`：
  官方已有 `sun50i-h700-anbernic-rg34xx-sp.dts`、面板驱动 `panel-mipi.c`、
  `rocknix-singleadc-joypad`、PWM 背光、31 个内核补丁
- [danielbuva/birdOS](https://github.com/danielbuva/birdOS)：`bird-device-contract.tsv`，
  输入设备 ID、背光满量程、面板分辨率等实测值
- [macromorgan/device_anbernic_rgxx3](https://github.com/macromorgan/device_anbernic_rgxx3)：
  AOSP 设备树的组织方式（super / sepolicy / keylayout 结构）

## 许可

- `device/`、`tools/`、`docs/`：Apache-2.0
- `kernel/dts/*.dts`：`GPL-2.0-only OR BSD-2-Clause`（与 mainline 一致）

## 免责

刷机有风险。做卡前确认 `/dev/sdX` 是你要抹掉的那张卡；U-Boot 的 DDR3/DDR4 选错不会开机，
但不会烧硬件（只会让你怀疑人生）。
