# 施工状态与续接点

> 这份文件是给"关机之后重新开工"用的。所有数字都是实测值，不是估计。

最后更新：2026-11-07 上午

---

## 一、当前进度一句话

内核**已经编出可用 Image**（含 SELinux，32,020,488 B），Android 系统**正在编**。
早上定位并修掉了两个足以让系统永远起不来的产品配置 bug（见第八节），现在构建在跑。

构建日志：`/tmp/bacon3.log`（脚本 `tmp/start-bacon3.sh`，末行是 `BACON_RC=`）；
产物目录：`/root/android/lineage/out/target/product/rg34xxsp/`。

## 二、续编命令

```bash
# 确认内核产物还在（期望 32,020,488 字节）
ls -l /root/artifacts/Image /root/android/lineage/device/anbernic/rg34xxsp/kernel/Image

# 续编（增量）
bash /mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/tmp/start-bacon3.sh
```

## 三、已经改掉的参数（不用再改）

```
BOARD_RAMDISK_OFFSET := 0x03000000     ← 已改，原来是 0x02000000
```

原因：内核 32,020,488 B 放在 `0x40080000`，**结束于 `0x41F0A0C8`**；ramdisk 原厂在 `0x42000000`，
两者只隔 983 KB。改到 `0x03000000` 后余量 16 MB，且所有地址仍低于 `0x45000000`
（原厂 `boot_normal` 是 `sunxi_flash read 45000000 boot`，U-Boot 先把整个 boot.img 暂存在那）。

## 四、SD 卡启动路线（当前方向）

### 已确认的事实

| 项 | 值 |
|---|---|
| U-Boot 文件 | `F:\dsh-dl\uboot\H700-DDR3-u-boot-sunxi-with-spl.bin` |
| 大小 / sha256 | 661,504 B / `03ddc07421cf0b858766bf72246e5241fbf3b5e2bf9a2ee4e98d58cfa1cbbb00` |
| 版本 | U-Boot 2026.01 (Nov 01 2026) Allwinner Tech，DDR3 |
| 来源 | 从 `ROCKNIX-H700.aarch64-20261101-DDR3.img` 的 8 KiB 处切出（真实长度 661,010 B，末尾零填充，按 512 对齐） |
| 写入位置 | **8 KiB（扇区 16）**，sunxi BROM 硬性要求 |
| 启动策略 | `boot_targets = fel mmc0 pxe dhcp`，`bootcmd = run distro_bootcmd`，原生支持 `extlinux/extlinux.conf` |
| 救砖后路 | `bootcmd_fel` 存在 → USB FEL 可用 |

### 关键机制：GPT 和 8 KiB 的 U-Boot 可以共存（实测原厂就是这样）

原厂 `partitions.sfdisk` 原文：

```
label: gpt
first-lba: 4
last-lba: 122138620
table-length: 8        ← 只放 8 个分区表项 = 1 KiB
```

`table-length: 8` 意味着 GPT 分区表项数组只占扇区 2–3，**扇区 4–15 是空的**，SPL 从扇区 16（8 KiB）开始，
两者不打架。原厂备份里 `sector1 = "EFI PART"` 与 `sector16 = eGON.BT0` 同时存在，已核对。

所以**不要**用 sgdisk 默认的 128 项（占扇区 2–33，会盖掉 SPL），必须把 `NumberOfPartitionEntries` 改成 8。
`gdisk` 专家菜单的 `s` 命令可以改，或者用 python 直接改主/备两个头再重算 CRC32。

### 原厂分区表（照抄最安全，单位：扇区，512 B）

| 分区 | start | size | 用途 |
|---|---|---|---|
| p1 `special` | 73728 | 131072 | 36 MiB 起，64 MiB |
| p2 `boot-resource` | 204800 | 65536 | |
| p3 `env` | 270336 | 32768 | U-Boot 环境变量，**可以读出现成的 bootcmd** |
| p4 `boot` | 303104 | 131072 | Android boot.img |
| p5 `rootfs` | 434176 | 14673920 | |
| p6 `appfs` | 15108096 | 8388608 | |
| p7 `UDISK` | 23496704 | 1048576 | |
| p8 `primary` | 24545280 | 96372736 | TF 卡 |

第一个分区从扇区 73728（36 MiB）开始，不是 1 MiB —— 前面全留给引导程序。

### 卡写入被 Windows 卡住了（待解决）

`\\.\PhysicalDrive3` 可以打开、`IOCTL_DISK_GET_LENGTH_INFO` 拿到 31.91 GB、
读 16 KiB 也正常，但**第一次 `write()` 就 `OSError: [Errno 22] Invalid argument`**。

排查方向（按优先级）：

1. **卷还挂着** —— `G:` / `H:` 两个卷在这张卡上，Windows 持有它们。先删盘符再写
   （`Get-Volume` 显示其 Size=0，文件系统本来就坏了，该清掉）。
2. `CreateFileW` + `FILE_FLAG_NO_BUFFERING` 重开句柄（ctypes 直接调 Win32，不走 python `open()`），
   写入长度和偏移按 512 对齐。
3. 绕开 raw 写：在 WSL 里做 **loop 文件**（`losetup -fP` 可用，`mkfs.vfat`/`sgdisk` 都在），
   做成镜像再从 Windows 侧按偏移写入。
4. 最后手段：`diskpart` 对可移动介质拒绝 `offline`（实测报"此操作不受移动媒体支持"），
   **`wsl --mount --bare` 这条路走不通**，不要再试。

## 五、已经做完的（不需要再动）

- 内核：7.1.2 + 31 个 ROCKNIX 补丁 + 3 个通用补丁，编出 `Image` 32,020,488 B，
  配置断言 17 条全过（SELinux / BINDERFS / MEMFD_CREATE / netfilter legacy / BT H4 / 面板 / 手柄）。
- 面板固件 `panels/anbernic,rg34xx-sp-panel.panel` 已入库并内建进内核（`CONFIG_EXTRA_FIRMWARE`）。
- 手柄驱动 `rocknix-singleadc-joypad` 已收编为内建，`joypad_input_g` 由 0004 补丁提供。
- Android 设备树：`breakfast lineage_rg34xxsp-userdebug` rc=0，图形栈按真实模块接线
  （`hwcomposer.drm_minigbm` / `composer@2.2-service` / `allocator@4.0-service.minigbm` / `mapper@4.0-impl.minigbm`），
  `fstab` 已改为进 ramdisk，`manifest.xml` / `vendor.prop` 已就位。
- 产品配置已继承 AOSP base（`core_64_bit.mk` + `base.mk`），`PRODUCT_PACKAGES` 从 0 个基础包
  变成含 `init_system` / `init_vendor` / `linker` / `servicemanager` 的完整 416 项。
- 原厂备份完整：`F:\dsh-backup\rg34xxsp-stock-20261106\`（含 SHA256SUMS，7-Zip 解压后大小与分区严格一致）。
- 刷 eMMC 的脚本 `tools/flash-boot.ps1`（含 7 道校验，`-Restore` 回滚），dry-run 已验证。

## 六、还欠的

1. **EGL**：`external/mesa3d` 是 host-only，编不出设备端 `libEGL_mesa`。
   `/root/ref/panfork-mesa`（23.0.0-devel，带 `android/` 目录）是唯一缺口，要接进 18.1 的 soong。
   这是 M1（SurfaceFlinger 出画面）之前最后一块。
2. 构建跑完后：确认 `installed-files-ramdisk.txt` 里出现 `/ramdisk/init`，
   再取 `boot.img` / `super.img`。
3. 卡路线：解决 raw 写 → 分区（GPT table-length=8）→ U-Boot 写 8 KiB → FAT 启动分区放
   `Image` / DTB / `initrd` / `extlinux/extlinux.conf` → `super.img` 写进对应分区。
4. 掌机实测：插卡开机看 U-Boot 是否走卡（`boot_targets` 的 mmc0）、能否进 Android first-stage。

## 七、踩过的坑（别再踩）

| 现象 | 真因 |
|---|---|
| `repo sync` 进程在、零进度、日志空 | `git config` 里 `http.https://android.googlesource.com/.proxy` 是**空字符串** = 该域名禁用代理 → 全部直连被墙。`--unset-all` 后重设 |
| `wsl.exe -u root bash -c "..."` 报 `unexpected EOF` | PowerShell 吞引号。**一律写 .sh 文件再 `bash /mnt/c/...`** |
| `zstd -t` 说 CORRUPT | WSL 根本没装 zstd，是"命令不存在"。Windows 侧用 7-Zip 解 `.zst` |
| `wsl --mount --bare` 失败 `ERROR_INVALID_DRIVE` | 可移动介质不支持 `diskpart offline`，这条路对本机无效 |
| PowerShell 改文件后中文乱码 | `Get-Content/-replace/Set-Content` 按 GBK 解码。**中文文件一律用 python3 + `io.open(...,newline='\n')` 改** |
| `git commit -F -` / `<<<` 报错 | PowerShell 没有 heredoc。把 message 写进 `$env:TEMP\*.txt` 再 `git commit -F <file>` |
| 脚本里 `source build/envsetup.sh` 后整段无声退出 | 脚本开了 `set -u`，envsetup.sh 引用未定义变量直接终止。**envsetup 之前不要 `set -u`** |
| `get_build_var PRODUCT_PACKAGES` 里搜不到 `libc` / `toybox` | 不是缺包：18.1 里它们不是 PRODUCT_PACKAGES 的顶层项（`libc` 在 `com.android.runtime` APEX，`adbd` 叫 `adbd_system_api`）。搜 `init_system` / `init_vendor` 更可靠 |

---

## 八、早上定位的两个致命 bug（已修，同一根因）

### 症状

1. `ramdisk.img` **只有 777 字节**，`installed-files-ramdisk.txt` 全文只有一行
   `1904  /ramdisk/fstab.rg34xxsp` —— **first-stage init 不在 ramdisk 里，这个 boot.img 永远起不来**。
2. `mka bacon` 死在 dexpreopt：
   `dexpreopt_gen -global_soong -global ...` → `--global configuration file is required`
   （`-global_soong` 和 `-global` 后面都是空串），失败的是 `Updater` / `TrebuchetQuickStep` / `ThemePicker`。

### 根因：产品配置链没有继承 AOSP base

`device/anbernic/rg34xxsp/lineage_rg34xxsp.mk` 原来只继承 `device.mk` +
`vendor/lineage/config/common_full_tablet_wifionly.mk`。而 Lineage 那条链
（`common_full_tablet_wifionly` → `common_full` → `common_mobile` → `common`）里
**一个 AOSP base 都没继承** —— `common.mk` 全文只有一行
`$(call inherit-product-if-exists, vendor/extra/product.mk)`。
原文件第 15 行那句 `full_base.mk` 继承是被注释掉的，这就是 bug 源头。

后果逐条对得上：

- `init_first_stage` 全靠 `base_vendor.mk` 里的 phony `init_vendor` 通过 `LOCAL_REQUIRED_MODULES`
  拉进 `TARGET_RAMDISK_OUT` → 没继承就没有 → ramdisk 空。
- boot image / `PRODUCT_USES_DEFAULT_ART_CONFIG` 没配置 → Soong 的 dexpreopt bootjars singleton
  不产出 `out/soong/dexpreopt_soong.config` → make 侧
  `build/make/core/dex_preopt_odex_install.mk` 加进规则的两个 `PRIVATE_GLOBAL_*` 变量是空串。

### 修法

```make
$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/base.mk)
$(call inherit-product, vendor/lineage/config/common_full_tablet_wifionly.mk)
```

`base.mk` = `base_system` + `base_system_ext` + `base_vendor` + `base_product`，四分区全有底座，
且不拖 `full_base.mk` 的 sounds/languages（1 GiB 机上没必要）。

修后实测：`PRODUCT_PACKAGES` 里出现 `init_system` / `init_vendor` / `linker` / `servicemanager`，
`PRODUCT_USES_DEFAULT_ART_CONFIG = true`，`WITH_DEXPREOPT = true`。
