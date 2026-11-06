# Anbernic RG34XXSP — Android 设备树

> 给 Allwinner H700 的翻盖掌机 RG34XXSP 做的 **LineageOS 22.2 / Android 15** 设备树与内核 DTS。
> 没有官方 BSP，所以这份东西是从 mainline Linux + ROCKNIX 的 H700 支持反推出来的。

## ⚠️ 先读这一段

- **没有在真机上验证过。** 作者手上没有这台机器。所有硬件参数都有出处（见 `docs/HARDWARE.md`
  的 ✅🟢🟡 标记），但"能不能亮屏"只有你插上卡才知道。
- 内核/设备树部分是可靠的（等于把 Linux 上已经跑得通的配置翻译成 Android 需要的形态）；
  **HAL 部分是 0%**（显示/音频/Wi-Fi 全要自己接），这是 Allwinner 掌机的宿命，不是这个仓库的锅。
- 想少走弯路：先按 `docs/BUILD.md` 走到能 `adb shell`（M0），再谈画面。

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
│   ├── dts/sun50i-h700-anbernic-rg34xxsp.dts   # ← 核心：完整板级 DTS
│   ├── fragment.config             # 内核 Android + 本机硬件 config 片段
│   └── README.md                   # 怎么把 DTS/config 塞进内核树
├── docs/
│   ├── HARDWARE.md                 # 引脚表、面板、输入、无线，全部标来源
│   ├── BUILD.md                    # 从 repo init 到插卡上电
│   └── PORTING.md                  # 还缺哪些 HAL、验收标准、里程碑
└── tools/
    ├── build.sh                    # Lineage 构建（WSL/Linux）
    └── mk-sdcard.sh                # GPT + U-Boot + boot 分区 + super.img
```

## 这台机器（摘要）

| 项 | 值 |
|---|---|
| SoC | Allwinner H700（4×A53 @1.416 GHz，Mali-G31 MP2） |
| 屏幕 | 720×480 横屏 3:2 翻盖，MIPI-DPI-over-SPI，PWM0/PD28 背光（满量程 2499） |
| 输入 | "H700 Gamepad" `0019:484b:14df`，17 键 + 双摇杆（ADC 复用）+ PWM2 震动 + PE7 盖子 |
| 存储 | TF1 = mmc0 启动，TF2 = mmc2 第二卡槽 |
| 无线 | RTL8821CS（SDIO + UART1）— **批次可能不同，需实物确认** |
| 电源 | AXP717（R_I2C 0x34）+ PCF8563 RTC（0x51） |
| 调试 | UART0（PH），115200 8N1 |

## 快速开始

```sh
# 1) 内核（见 kernel/README.md）：基线 ROCKNIX H700 内核 + kernel/fragment.config
#    产物 Image 放到 device/anbernic/rg34xxsp-kernel/Image

# 2) Android
cd ~/android/lineage && source build/envsetup.sh
breakfast lineage_rg34xxsp-userdebug
mka bacon

# 3) 做卡（注意：先确认你的板子是 DDR3 还是 DDR4 的 U-Boot）
sudo ./tools/mk-sdcard.sh /dev/sdX --force --uboot H700_DDR4_u-boot-sunxi-with-spl.bin \
     --image-dir out/target/product/rg34xxsp --dtb <path>/sun50i-h700-anbernic-rg34xxsp.dtb
```

## 设计上的几个决定（和原因）

1. **目标选 LineageOS 22.2**：这台机器的"同类用户"都在跑 ROCKNIX（Linux），
   而 Android 侧唯一有参考价值的社区产物是 Lineage 设备树；用户现有构建脚本也是 Lineage。
2. **不用 GKI / vendor_boot**：sunxi 的 U-Boot 对经典 Android boot image（header v0）
   支持最好，DTS 走 FAT 分区的 `extlinux.conf` 加载，省掉 boot header 魔改和 AVB 签名。
3. **GPT 而不是 MBR**：GPT 有分区名，Android 的 ueventd 会自动生成
   `/dev/block/by-name/*`，fstab 比写死 `mmcblk0p15` 稳。
4. **动态分区 super = 4 GiB**：SD 卡上留得下，system+vendor+product+system_ext 够用。
5. **内核单体（不拆 vendor_dlkm）**：少一层依赖，先让它能开机。
6. **依赖 ROCKNIX 的 H700 内核树**：mainline 的 `sun50i-h616.dtsi` 里没有 DE/TCON/HDMI/PWM 节点，
   这些（以及面板、手柄驱动）都还只在 ROCKNIX 的补丁集里，见 `kernel/README.md` §2。
6. **SELinux 先 permissive**：HAL 还没写，enforcing 只会让你在 M0 卡三天。
7. **FBE 不启用**：H700 没有 TEE/keymaster，硬件加密做不了；`/data` 先明文，
   软件 keymaster 是后话。

## 来源与致谢

这份设备树不是凭空造的，硬件事实来自这些项目：

- [mainline Linux — `sun50i-h700-anbernic-rg35xx-*.dts`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/arch/arm64/boot/dts/allwinner)
  （Ryan Walklin、Chris Morgan）：SoC、按键、音量、盖子、PMIC、存储、Wi-Fi/BT 接线
- [ROCKNIX/distribution](https://github.com/ROCKNIX/distribution) `projects/ROCKNIX/devices/H700`：
  面板（`panel-mipi.c`、720×480 时序）、PWM 背光、`rocknix-singleadc-joypad`、MMC2
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