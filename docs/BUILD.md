# 构建与刷机流程

目标：**LineageOS 22.2（Android 15）**，设备名 `rg34xxsp`，全部在 WSL / Linux 里做。
下面的命令都在 WSL Ubuntu（22.04+）里跑，Windows 侧只用来插卡。

## 0. 准备

```sh
sudo apt install -y git git-lfs ccache rsync gdisk dosfstools mtools \
                    python3 python3-pip openjdk-17-jdk bc bison build-essential \
                    flex g++-multilib gcc-multilib lib32z1-dev libncurses5 \
                    libsdl1.2-dev libssl-dev libxml2 libxml2-utils lzop pngcrush \
                    schedtool squashfs-tools xsltproc zip zlib1g-dev device-tree-compiler
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/bin/repo && chmod +x ~/bin/repo
```

磁盘：源码 ~100 GB，产物 ~60 GB，ccache 建议 50 GB。8 GB 内存建议加 swap。

## 1. 拉源码

```sh
mkdir -p ~/android/lineage && cd ~/android/lineage
repo init -u https://github.com/LineageOS/android.git -b lineage-22.2 --git-lfs
repo sync -c -j8 --force-sync --no-clone-bundle --no-tags
```

把本仓库放到设备树位置（软链或复制都行）：

```sh
rsync -a --exclude .git  <本仓库>/device/anbernic/rg34xxsp/  \
      ~/android/lineage/device/anbernic/rg34xxsp/
```

## 2. 编内核（Android 版）

严格按 `kernel/README.md`，要点：

- 基线：**Linux 7.1.2** = torvalds `03e2778d1f11de9260543f969e9e888a1c2bf830`
  （ROCKNIX 的 `packages/linux/package.mk` 就 pin 这个 commit）
- 打 ROCKNIX 的 31 个 H700 补丁（`projects/ROCKNIX/devices/H700/patches/linux/`）
- 叠加 `kernel/fragment.config`
- DTS 用本仓库的 `kernel/dts/sun50i-h700-anbernic-rg34xx-sp.dts`

先跑一遍 DTS 验证（不编译内核，只要 cpp+dtc）：

```sh
./tools/verify-dts.sh
```

```sh
cp out/Image  ~/android/lineage/device/anbernic/rg34xxsp-kernel/Image
cp out/sun50i-h700-anbernic-rg34xx-sp.dtb  /tmp/
```

⚠️ 内核**必须**含 `CONFIG_ANDROID_BINDER_IPC` + `CONFIG_ANDROID_BINDERFS`，
否则系统起来后第一个 binder 调用就会崩在 `init`，表现为"无限重启"。

## 3. 编 Android

```sh
cd ~/android/lineage
source build/envsetup.sh
breakfast lineage_rg34xxsp-userdebug
mka bacon -j$(nproc)
```

产物在 `out/target/product/rg34xxsp/`：`boot.img`、`super.img`、`system.img`、`vendor.img`…

> 第一次编大概率在 HAL 上失败——因为本设备树**故意**没塞进不存在的 HAL
> （见 `docs/PORTING.md`）。先编到能起 `init` 的镜像，再逐个补 HAL。

## 4. 引导链怎么走（生死攸关）

本机的实际情况（真机实测）：

- 系统在 **eMMC（`mmcblk0`）**，不是"启动 TF 卡"。原厂 GPT 是 8 个全志风格分区，
  `p4 = boot`（64 MiB）放内核和 bootargs，**bootargs 里带 `androidboot.mode=normal`、
  `boot_type=1`、`androidboot.hardware=sun50iw9p1`**。
- 原厂 U-Boot 是 `2018.05`，属于**全志 Android 系 U-Boot**。这意味着它可能
  直接能吃经典 Android boot.img —— 这条捷径值得先试，成了就不用换 U-Boot。

两条路，按代价排序：

| 路线 | 做法 | 代价 |
|---|---|---|
| **A. 先试原厂 U-Boot** | 只替换 `p4` 里的内核/boot，或把 `boot.img` 塞进 boot 分区 | 要先把 Android 镜像搞出来；能不能吃 boot.img 待验证 |
| **B. 换 sunxi U-Boot** | 把 `H700_DDR{3,4}_u-boot-sunxi-with-spl.bin` 写到 **8 KiB 偏移**，DTS 走 `extlinux.conf` | 需先确认 DDR3/DDR4，选错直接黑屏；快，但不可逆（可回刷原厂） |

DDR3/DDR4 的确认方法（**已确认，本机是 LPDDR3**）：

ROCKNIX 的 U-Boot 有两个 defconfig，靠 `vdd-dram` 的电压区分（原文 `bootloader/update.sh`）：
`1200000 µV → H700_DDR3_u-boot-sunxi-with-spl.bin`、`1100000 µV → H700_DDR4_...`。
真机实测 `axp2202-dcdc3 = 1200000 µV`（DCDC3 就是 DRAM 供电）→ **走 DDR3 那套**：

```
U-Boot  : 上游 u-boot v2026.01，defconfig anbernic_rg35xx_h700_lpddr3_defconfig
           （DDR4 机型用 ..._lpddr4_defconfig）
BL31    : ATF 平台 sun50i_h616（ATF_PLATFORM="sun50i_h616"）
产物    : u-boot-sunxi-with-spl.bin → dd bs=1K seek=8（即 8 KiB 偏移）
```

两条路线里，**B 是社区验证过的**（ROCKNIX 在这块板上就是这套 U-Boot + bl31），A 只是省事
但没人验证过 `boot_type=1` 的分支能不能吃标准 Android boot.img，别把它当默认路径。

## 5. 做卡 / 刷机

TF 卡只在"路线 B"或者"想先从卡上跑起来"时才需要：

```sh
sudo ./tools/mk-sdcard.sh /dev/sdX --force \
     --uboot H700_DDR4_u-boot-sunxi-with-spl.bin \
     --image-dir ~/android/lineage/out/target/product/rg34xxsp \
     --dtb /tmp/sun50i-h700-anbernic-rg34xx-sp.dtb
```

脚本做的事：GPT（boot 64M / misc 4M / metadata 16M / super 4G / userdata 剩余）→
U-Boot 写到 8 KiB → boot 分区放 `extlinux.conf` + `Image` + `dtb` + `initrd.img` →
`super.img` 直接 dd 到 super 分区。

> 注意：卡虽然在 TF 槽里能启动，但 `androidboot.boot_devices` 写的是 `mmcblk0`。
> 从 TF 卡启动时 TF 会变成 `mmcblk0`（eMMC 变 `mmcblk1` 或 `mmcblk2`，取决于 aliases），
> **首次卡启动前先 `cat /proc/cmdline` 确认真实设备名**，必要时改 boot 分区里的 `extlinux.conf`。

## 6. 首次启动

1. 接 UART0（115200 8N1）看日志，别一上来就指望屏幕。
   - 本机 Windows 侧目前只有蓝牙串口占位，真串口需要 USB-TTL 转接；
     `tools/watch-serial.ps1` 可以带着 COM 口跑。
2. U-Boot 阶段应该能看到 `extlinux.conf` 被读到、`Image` 被载入。
3. 内核阶段看：
   - `panel-mipi` / `rocknix-singleadc-joypad` 是否 probe
   - `tcon_lcd0` 是否 `okay`
   - `mmc0` 是否认出 GPT 分区
4. Android 起来后：
   ```sh
   adb devices
   adb shell getprop | grep -E "boot|hardware|display"
   adb shell cat /proc/cmdline
   ```

## 7. 常见故障对照

| 现象 | 大概原因 |
|---|---|
| 完全不亮、串口无输出 | U-Boot DDR3/DDR4 选错，或 U-Boot 没写到 8 KiB 偏移 |
| U-Boot 起来但找不到内核 | boot 分区不是 FAT32，或 `extlinux/extlinux.conf` 路径大小写 |
| 内核 panic `Unable to mount root fs` | `androidboot.boot_devices` 不对（从卡启动时设备名会变），或 ramdisk 没塞进 boot 分区 |
| 屏幕黑但 adb 在 | 面板没 probe：确认 `panel-mipi.c` 有 `anbernic,rg34xx-sp-panel` 条目；或试 v2 面板 DTB |
| 背光有、画面没有 | `tcon_lcd0`/`de` 没 `okay`，或 DRM 驱动没编进内核（`0002` 补丁的驱动部分没打上） |
| 无限重启 | 内核缺 binder，或 `super` 分区大小不足 4 GiB |
| SELinux 拒绝导致卡启动 | `androidboot.selinux=permissive`，先跑起来再收敛策略 |
| 摇杆只有一个方向 | `amux-channel-mapping` 或 PI1/PI2 顺序反了 |

## 8. 刷机（不用拆卡）

U-Boot 带 fastboot 时：

```sh
# 按住 START 上电，或串口里输入 fastboot usb 0
fastboot devices
fastboot flash super out/target/product/rg34xxsp/super.img
fastboot reboot
```

数据分区第一次开机会被 Android 格式化（`formattable`）。