# 构建与刷机流程

目标：**LineageOS 22.2（Android 15）**，设备名 `rg34xxsp`，全部在 WSL / Linux 里做。
下面的命令都在 WSL Ubuntu（22.04+）里跑，Windows 侧只用来插卡。

## 0. 准备

```sh
sudo apt install -y git git-lfs ccache rsync gdisk dosfstools mtools \
                    python3 python3-pip openjdk-17-jdk bc bison build-essential \
                    flex g++-multilib gcc-multilib lib32z1-dev libncurses5 \
                    libsdl1.2-dev libssl-dev libxml2 libxml2-utils lzop pngcrush \
                    schedtool squashfs-tools xsltproc zip zlib1g-dev
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

严格按 `kernel/README.md`：

- 基线用 ROCKNIX 的 H700 内核（省掉所有 DTS 补丁）
- 叠加 `kernel/fragment.config`
- DTS 用本仓库的 `kernel/dts/sun50i-h700-anbernic-rg34xxsp.dts`

```sh
cp out/Image  ~/android/lineage/device/anbernic/rg34xxsp-kernel/Image
cp out/sun50i-h700-anbernic-rg34xxsp.dtb  /tmp/
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

## 4. 选对 U-Boot（生死攸关）

H700 有两个 U-Boot 二进制，区别只在 DRAM 电压：

- `H700_DDR3_u-boot-sunxi-with-spl.bin` → `vdd-dram` = 1.20 V
- `H700_DDR4_u-boot-sunxi-with-spl.bin` → `vdd-dram` = 1.10 V

从 Anbernic 原厂卡或 ROCKNIX 镜像里取（`/boot/` 或镜像首个 1 MiB 附近），
用 `strings` / `binwalk` 确认。**选错就不会亮屏**，别怀疑镜像，先怀疑这里。

## 5. 做卡

```sh
sudo ./tools/mk-sdcard.sh /dev/sdX --force \
     --uboot H700_DDR4_u-boot-sunxi-with-spl.bin \
     --image-dir ~/android/lineage/out/target/product/rg34xxsp \
     --dtb /tmp/sun50i-h700-anbernic-rg34xxsp.dtb
```

脚本做的事：GPT（boot 64M / misc 4M / metadata 16M / super 4G / userdata 剩余）→
U-Boot 写到 8 KiB → boot 分区放 `extlinux.conf` + `Image` + `dtb` + `initrd.img` →
`super.img` 直接 dd 到 super 分区。

## 6. 首次启动

1. 卡插 **TF1**，接 UART0（115200 8N1）看日志，别一上来就指望屏幕。
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
| 内核 panic `Unable to mount root fs` | `androidboot.boot_devices` 不对，或 ramdisk 没塞进 boot 分区 |
| 屏幕黑但 adb 在 | 面板没 probe：检查 `panel-mipi.c` 是否有 `rg34xx-sp-panel` 条目 |
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