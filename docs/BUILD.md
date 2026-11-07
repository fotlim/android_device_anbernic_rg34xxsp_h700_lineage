# 构建与刷机流程

目标：**LineageOS 18.1（Android 11）**，设备名 `rg34xxsp`，全部在 WSL / Linux 里做。
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

磁盘（18.1 实测量级，别按 22.2 的 250 GB 吓自己）：`repo sync --depth 1` 源码 ~70 GB、
`out/` 产物 ~55 GB；ccache 可选（0–20 GB）。**准备 ≥200 GB 空盘**，256 GB 也行、卡着走。
注意：WSL 的 ext4.vhdx 必须落在真实空闲空间够的盘上（本机是 `F:\wsl\Ubuntu-22.04`，F: 有 347 GB），
**不要**把 Android 源码放在 `/mnt/f` 这种 9p 挂载点上——单文件 IO 会慢 5–10 倍。

## 1. 拉源码

```sh
mkdir -p ~/android/lineage && cd ~/android/lineage
repo init -u https://github.com/LineageOS/android.git -b lineage-18.1 --git-lfs
repo sync -c -j8 --force-sync --no-clone-bundle --no-tags
```

把本仓库放到设备树位置（软链或复制都行）：

```sh
rsync -a --exclude .git  <本仓库>/device/anbernic/rg34xxsp/  \
      ~/android/lineage/device/anbernic/rg34xxsp/
```

### 施工笔记：代理是把 `repo sync` 卡死的元凶

本机（Windows + WSL2）直连 `android.googlesource.com` 是**被黑洞**的：
TCP 能发出 SYN，但对端永不回，表现成 `SYN-SENT` 卡到超时。代理（`127.0.0.1:12000`）
从 WSL 是通的（`curl` 1.7 s 拿到 200）。

坑在于 git 配置里有一条**空值**的接管：

```sh
git config --global --get-regexp 'http\..*proxy'
# http.https://android.googlesource.com/.proxy      <-- 空值 = 明确"不要代理"
```

空字符串不是"没设置"，它对 git 来说是"这条 URL 禁止用代理"，于是所有 fetch 都改直连、
全部卡在 `SYN-SENT`，`repo sync` 看起来在跑（进程都在、CPU 0%、日志一行不出）、实际零进度。

修法：

```sh
git config --global --unset-all 'http.https://android.googlesource.com/.proxy'
git config --global --add http.https://android.googlesource.com/.proxy http://127.0.0.1:12000
# 验证：应该 1 秒内返回一行
git ls-remote --tags https://android.googlesource.com/device/generic/x86 android-11.0.0_r46
```

**判断 sync 到底有没有在动**，别看日志（`repo` 会长时间一声不响），看这两样：

```sh
ss -tn | awk 'NR>1{print $1}' | sort | uniq -c   # 有 ESTAB 才是真在传，全是 SYN-SENT 就是卡死
du -sm ~/android/lineage/.repo                   # 隔 10 秒采一次，数字要涨
```

粘在网络上的进程最后要**杀干净**再重启，否则两套 `repo sync` 会互相踩 `.repo`：

```sh
pkill -9 -f 'repo-dir=.*android/lineage'
```

而且一定要用 `setsid nohup repo sync ... &` 起，不然 `wsl.exe` 一退出，整个 sync 跟着 SIGHUP 走。

> **内存决定版本**：这台机器 1 GiB LPDDR3，所以整条链按 **Android 11** 走（`PRODUCT_SHIPPING_API_LEVEL := 30`）。
> 18.1 的 build/make 不会替你编内核，也不会读 `TARGET_PREBUILT_KERNEL`（已在分支上核对过），
> 唯一可靠的做法是把编好的 `Image` 放到 `out/target/product/rg34xxsp/kernel` ——
> `device.mk` 里的 `PRODUCT_COPY_FILES += $(LOCAL_KERNEL):kernel` 就是干这个的，
> 源文件放在 `device/anbernic/rg34xxsp/kernel/Image`（不入库，见 `.gitignore`）。

## 2. 编内核（Android 版）

一条命令：`./tools/build-kernel.sh`（`JOBS=12`、`WORK=~/rg34xxsp-kernel` 可调）。
它在真实内核树上把 31 个 ROCKNIX 补丁打上、注入三处脚手架、拿 ROCKNIX 的 H700 全量
`linux.aarch64.conf` 当 base 合并本仓库 `kernel/fragment.config`，最后产出 `out/Image` + 两个 DTB。

**实测已验证**（本机 WSL，12 线程）：
`Image` = 30,959,624 字节（`Linux kernel ARM64 boot executable Image, little-endian, 4K pages`），
`sun50i-h700-anbernic-rg34xx-sp.dtb` = 35,662 字节（DTB v17）。
两个必须知道的坑（脱离 ROCKNIX 构建树就会炸）：
`CONFIG_INITRAMFS_SOURCE` 指向 buildroot 目录 → `usr/initramfs_data.cpio Error 1`；
`CONFIG_EXTRA_FIRMWARE` 引用 `external-firmware/rtl_bt/*.bin` → `No rule to make target`。
两者都在脚本里被清空。

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
- 原厂 U-Boot 是 `2018.05`，属于**全志 Android 系 U-Boot**。

### 实测：原厂 p4 就是标准 Android boot.img（路线 A 已验证成立）

之前把「原厂 U-Boot 能不能吃 boot.img」列为"待验证"。现在验证完了 ——
`dd if=/dev/mmcblk0p4 bs=1024 count=64 | base64` 取回头部，解析出来是：

```
magic         ANDROID!
kernel_size   17686536 (17.0 MiB)   kernel_addr   0x40080000
ramdisk_size   2606339 ( 2.5 MiB)   ramdisk_addr  0x42000000
second_size          0              second_addr   0x40f00000
tags_addr     0x40000100            page_size           2048
name          "sun50i_arm64"
cmdline       (空 —— 真正的 cmdline 在 U-Boot 的 bootargs 里，不在这里)
```

内核起始偏移 `2048` 处确实是 **arm64 Image**（偏移 +56 是 `41 52 4d 64` = `"ARM\x64"`）。

**结论：原厂 U-Boot 走的就是 `ANDROID!` + booti 这条路，路线 A 不需要任何猜测。**
`device/anbernic/rg34xxsp/BoardConfig.mk` 的 boot 头几何已经按上表逐字节对齐
（`BOARD_KERNEL_BASE := 0x40000000` + 四个 offset，`PAGESIZE := 2048`，
`--board sun50i_arm64`），所以 `mka bootimage` 出来的镜像在格式上和原厂同构。

两条路，按代价排序：

| 路线 | 做法 | 代价 |
|---|---|---|
| **A. 先试原厂 U-Boot** ✅**已验证可行** | 把 `boot.img` `dd` 进 `p4` | 只需 android 镜像；U-Boot 层面零改动，**拔卡/回刷即可恢复** |
| **B. 换 sunxi U-Boot** | 把 `H700_DDR3_u-boot-sunxi-with-spl.bin` 写到 **8 KiB 偏移**，DTS 走 `extlinux.conf` | 需先确认 DDR3/DDR4，选错直接黑屏；可回刷 |

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

两条路线里，**A 现在已经验证了格式前提**（原厂 p4 就是 `ANDROID!` boot.img），
所以默认就走 A：不碰 U-Boot，只把 boot.img 写进 p4，出问题 `dd` 回原厂镜像即可。
B 仍然保留，用于"想从 TF 卡启动、eMMC 一个字节都不动"的场合。

## 5. 刷机：把 boot.img 写进 p4（路线 A，默认）

既然原厂 p4 就是标准 `ANDROID!` boot.img（见上一节实测），升级 Android 在本机就等于：

```sh
dd if=boot.img of=/dev/mmcblk0p4
```

不用动 U-Boot，不用重分区，eMMC 上其它分区一个字节都不碰。整件事两步：先备份，再刷。

### 5.1 先备份（只需要做一次）

```powershell
# 在掌机上跑。脚本是纯 ASCII，所以可以安全地经 ssh 送进去
cmd /c "ssh root@192.168.137.30 bash -s < tools\backup-stock.sh"

# 取回宿主机
scp -r root@192.168.137.30:/mnt/mmc/backup-stock-* F:\dsh-backup\
```

`min` 模式（默认）只备份"回得去"必需的约 12 MB：GPT 快照、前 2 MiB 引导区、p1/p2/p3/p4/p7。
那 2 MiB 里 8 KiB 处就是 `0xea0004be` + `eGON.BT0` 的 sunxi SPL/U-Boot —— 换 U-Boot（路线 B）时
就是覆盖这 8 KiB，所以它必须在备份里。
`full` 模式额外压 p5/p6（整个原厂系统），约 1.2 GB。

> 宿主机（Windows）没有 zstd。用 7-Zip 解：
> `& "C:\Program Files\7-Zip\7z.exe" e p4.img.zst -o<目录>`
> 解出来的大小必须和分区严格一致（p4 = 67,108,864 字节），前 8 字节是 `ANDROID!`。

### 5.2 再刷

```powershell
.\tools\flash-boot.ps1 -Image out\target\product\rg34xxsp\boot.img
```

脚本在落盘之前把每一条都验一遍，任何一条不过就直接退出、不写：

| 检查 | 期望值 |
|---|---|
| 魔数 | `ANDROID!` |
| `page_size` | 2048 |
| 内核基址（由 `kernel_addr` 反推） | `0x40000000` |
| board name | `sun50i_arm64` |
| 镜像大小 | ≤ 分区大小 67,108,864 |
| 设备上 p4 的实际字节数 | 与脚本假设一致（`blockdev --getsize64`） |
| 回滚镜像 | `-BackupDir` 里必须存在 `p4.img` 或 `p4.img.zst` |

全过之后：`scp` 上传 → 两端比 sha256 → `dd conv=fsync,notrunc` → `sync` →
`cmp` 逐字节回读比对 → `reboot`。加 `-DryRun` 只检查不写。

### 5.3 刷坏了怎么回去

```powershell
.\tools\flash-boot.ps1 -Restore
```

把 `-BackupDir` 里的 `p4.img.zst` 解出来再 `dd` 回 p4，重启就回到原厂。

> 起不来也不至于变砖：这是 Android 的 boot 分区，原厂分区（p5/p6）全程没被碰过；
> 还有 BROM 的 FEL 兜底，以及拔卡走卡启动（路线 B）。

## 6. 做卡：从 TF 卡启动（路线 B）

只在"想先从卡上跑、eMMC 一个字节都不动"时才需要。
Windows 上 raw 写可移动介质会被拒（`OSError: [Errno 22]`），所以现在**不在 Windows 直接写卡**，
而是在 WSL 里做镜像、再用 Rufus 的 DD 模式写。

```sh
# 在 WSL 里（需要 root：losetup + mount）
cd anbernic-rg34xxsp
sudo ./tools/mk-sdcard.sh \
     --out /root/rg34xxsp-sd.img \
     --total-gib 8 \
     --product-out /root/android/lineage/out/target/product/rg34xxsp \
     --force
```

默认输入全部从仓库取，不用手传：

| 参数 | 默认值 |
|---|---|
| `--uboot` | `kernel/uboot/H700-DDR3-u-boot-sunxi-with-spl.bin` |
| `--dtb` | `kernel/dtb/sun50i-h700-anbernic-rg34xx-sp.dtb` |
| `--kernel` | `device/anbernic/rg34xxsp/kernel/Image` |
| `--ramdisk` | `<product-out>/ramdisk.img` |
| `--super` | `<product-out>/super.img`（sparse，脚本自己 `simg2img`） |

脚本做的事（顺序即依赖关系）：

1. `truncate` 出 8 GiB 稀疏镜像；
2. `sfdisk` 建 GPT，**`table-length: 8`** —— 默认 128 项的分区表会覆盖扇区 16，把 SPL 砸掉；
3. U-Boot 写到 **8 KiB**，然后校验 `0xEA` 分支指令 + `eGON.BT0` tag；
4. `losetup -P` 挂上去，`mkfs.vfat -F 32 -n BOOT` / `mkfs.ext4 metadata` / `mkfs.ext4 userdata`；
5. `simg2img` 把超集转 raw，断言**大小正好 4 GiB**（等于 `BOARD_SUPER_PARTITION_SIZE`），再 dd 进 super 分区；
6. 挂 FAT 分区，放 `extlinux/extlinux.conf` + `Image` + `initrd.img` + `dtb`；
7. 自检：GPT 恰好 5 个分区、FAT32 卷标 `BOOT`、super 的 LP metadata 魔数 `67446c61`、
   boot 分区里 4 个文件都在。

做完把镜像拷到 Windows 给 Rufus：

```powershell
# 双端 sha256 必须一致
wsl -u root sha256sum /root/rg34xxsp-sd.img
```

Rufus 里**写盘方式必须选 "DD 镜像"**，ISO 模式会按 El Torito 去解释分区、写出来是坏的。
整张卡会被清空（这是预期行为）。

本机是 **DDR3**（`axp2202-dcdc3 = 1200000 µV`），所以 U-Boot 用 DDR3 那份；
ROCKNIX 的判据就是读这个 regulator 的微伏值：`1200000` → DDR3，`1100000` → DDR4。

> 注意：卡虽然在 TF 槽里能启动，但 `androidboot.boot_devices` 写的是 `mmcblk0`。
> 从 TF 卡启动时 TF 会变成 `mmcblk0`（eMMC 变 `mmcblk1` 或 `mmcblk2`，取决于 aliases），
> **首次卡启动前先 `cat /proc/cmdline` 确认真实设备名**，必要时改 boot 分区里的 `extlinux.conf`。
>
> 另外这份 U-Boot 没有 `abootimg` 支持，所以**卡路线不需要 boot.img**：内核、ramdisk、
> `androidboot.*` cmdline 全部拆开由 U-Boot 通过 extlinux 传。细节见 `kernel/uboot/README.md`。

## 7. 首次启动

1. 接 UART0（115200 8N1）看日志，别一上来就指望屏幕。
   - 本机 Windows 侧目前只有蓝牙串口占位，真串口需要 USB-TTL 转接；
     `tools/watch-serial.ps1` 可以带着 COM 口跑。
2. 内核阶段看：
   - `panel-mipi` / `rocknix-singleadc-joypad` 是否 probe
   - `tcon_lcd0` 是否 `okay`
   - `mmc0` 是否认出 GPT 分区
3. Android 起来后：
   ```sh
   adb devices
   adb shell getprop | grep -E "boot|hardware|display"
   adb shell cat /proc/cmdline
   ```
4. 还是原厂系统在跑？说明 p4 没写进去，或者 `boot.img` 的大小超过了 `cmdline` 之后的对齐——
   `flash-boot.ps1` 会在最后那步 `cmp` 就报出来。

## 8. 常见故障对照

| 现象 | 大概原因 |
|---|---|
| 完全不亮、串口无输出 | 走了路线 B 而 U-Boot DDR3/DDR4 选错，或没写到 8 KiB 偏移 |
| 刷完还是原厂系统 | p4 没被改写：`flash-boot.ps1` 的 `cmp` 那步会报；或 boot 分区大小被改过 |
| 内核 panic `Unable to mount root fs` | `androidboot.boot_devices` 不对（从卡启动时设备名会变），或 ramdisk 没塞进 boot.img |
| 屏幕黑但 adb 在 | 面板没 probe：确认 `panels/anbernic,rg34xx-sp-panel.panel` 在 `CONFIG_EXTRA_FIRMWARE` 里 |
| 背光有、画面没有 | `tcon_lcd0`/`de` 没 `okay`，或 `panel-mipi.c` 的 Kconfig/Makefile 条目没补回（驱动没编进内核） |
| 开机动画走完黑屏 | SurfaceFlinger 起来了但 gralloc/composer 没通，看 `logcat -b all \| grep -i -E "gralloc\|hwc\|composer"` |
| 无限重启 | 内核缺 binder，或 `super` 分区不足 4 GiB |
| SELinux 拒绝导致卡启动 | `androidboot.selinux=permissive`，先跑起来再收敛策略 |
| 摇杆只有一个方向 | `amux-channel-mapping` 或 PI1/PI2 顺序反了 |
| 手柄完全没反应 | `joypad_input_g` 未导出：`0004` 补丁没打上，`adc-keys.c` 里没有那个全局变量 |

## 9. fastboot（备用）

U-Boot 自带 fastboot 时：

```sh
# 按住 START 上电，或串口里输入 fastboot usb 0
fastboot devices
fastboot flash boot out/target/product/rg34xxsp/boot.img
fastboot reboot
```

数据分区第一次开机会被 Android 格式化（`formattable`）。

---

## 附：两个脚本的编码约定

`tools/backup-stock.sh` 是**纯 ASCII**：它要经 ssh 流进掌机，而 Windows 侧的
`Get-Content | ssh` 会把中文读成 GBK 再编码回去，脚本直接 `unexpected EOF`。
正确姿势是让 `cmd.exe` 自己重定向，字节原样过去：

```powershell
cmd /c "ssh root@192.168.137.30 bash -s < tools\backup-stock.sh"
```

`tools/flash-boot.ps1` 同理是纯 ASCII —— **Windows PowerShell 5.1 读没有 BOM 的 `.ps1`
时按 ANSI 解码**，中文注释会把整个文件带偏。中文说明一律放这个文档里。
