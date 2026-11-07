# 施工状态与续接点

> 这份文件是给"关机之后重新开工"用的。所有数字都是实测值，不是估计。

最后更新：2026-11-07 傍晚

---

## 一、当前进度一句话

内核**编好了**（含 SELinux，32,020,488 B），Android **编完了**（`mka droid` 11:34 成功，
`DROID_RC=0`），**SD 卡启动镜像也做好了**（8 GiB，sha256 见第四节），只差人把它用 Rufus
写到 TF 卡上插机实测。

构建产物：`/root/android/lineage/out/target/product/rg34xxsp/`
（`boot.img` 32,950,272 / `super.img` 895M / `system.img` 709M / `vendor.img` 49M /
`product.img` 105M / `system_ext.img` 35M / `ramdisk.img` 903K）。
卡镜像：`F:\dsh-dl\rg34xxsp-sd.img`（8,589,934,592 B）。

## 二、续编命令

```bash
# 确认内核产物还在（期望 32,020,488 字节）
ls -l /root/android/lineage/device/anbernic/rg34xxsp/kernel/Image

# 续编。注意是 droid，不是 bacon —— TARGET_SKIP_OTA_PACKAGE := true 时
# bacon 会在打包 OTA 那步因为 zip 不存在而 ln 失败。
cd /root/android/lineage && source build/envsetup.sh \
  && lunch lineage_rg34xxsp-userdebug && mka droid

# 重做卡镜像（会自己 simg2img 并把 Image/dtb 摆进 FAT）
cd /mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/anbernic-rg34xxsp
./tools/mk-sdcard.sh --out /root/rg34xxsp-sd.img --total-gib 8 --force
```

## 三、已经改掉的参数（不用再改）

```
BOARD_RAMDISK_OFFSET := 0x03000000     ← 已改，原来是 0x02000000
TARGET_NO_RECOVERY := true             ← recovery-as-boot 会把 7.4 MB 的 recovery ramdisk 塞进 boot.img
TARGET_SKIP_OTA_PACKAGE := true
PRODUCT_ENFORCE_VINTF_MANIFEST         ← 这行**没用**，见第九节；真正的开关在 lineage_rg34xxsp.mk 末尾
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

### 卡写入被 Windows 卡住了 → 改成"做镜像 + Rufus"

`\\.\PhysicalDrive3` 可以打开、`IOCTL_DISK_GET_LENGTH_INFO` 拿到 31.91 GB、
读 16 KiB 也正常，但**第一次 `write()` 就 `OSError: [Errno 22] Invalid argument`**。
`diskpart` 对可移动介质拒绝 `offline`（"此操作不受移动媒体支持"），
`wsl --mount --bare` 报 `ERROR_INVALID_DRIVE` —— 这两条路都别再试。

**现在的办法**：在 WSL 里把整张卡做成一个镜像文件，Windows 侧用 Rufus 的 **DD 模式**写。
风险留在用户手里（选错盘是人能看见的错），镜像也可以反复重做。

`tools/mk-sdcard.sh` 的布局（与 `fstab.rg34xxsp` / `BoardConfig.mk` 严格对齐）：

| 偏移 | 大小 | 分区名 | 内容 |
|---|---|---|---|
| 8 KiB | ~646 KiB | — | `u-boot-sunxi-with-spl.bin`（sunxi SPL + ATF） |
| 1 MiB | 64 MiB | `boot` | FAT32：`extlinux/extlinux.conf`、`Image`、`initrd.img`、`dtb` |
| 65 MiB | 4 MiB | `misc` | 全零（bootloader message buffer） |
| 69 MiB | 16 MiB | `metadata` | ext4 |
| 85 MiB | 4 GiB | `super` | raw `super.img`（**必须正好等于 BOARD_SUPER_PARTITION_SIZE**） |
| 4181 MiB | 余下 | `userdata` | ext4 |

实测产物：`/root/rg34xxsp-sd.img` → 拷到 `F:\dsh-dl\rg34xxsp-sd.img`，
8,589,934,592 B，sha256 `e860bb186d97264647e1b10c72216144679dcfd72b2b6b5dcc2daa0273bf06db`（双端一致）。

引导链：这份 U-Boot **没有 `abootimg` 支持**，走 `sysboot extlinux/extlinux.conf` + `booti`，
所以 boot.img 在这条路线里用不上 —— 内核 / ramdisk / cmdline 全拆开由 U-Boot 传。
详见 `kernel/uboot/README.md`。

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
- Android **编完了**：`mka droid` 11:34 成功，ramdisk 里实测有 `/ramdisk/init`（2,090,096 B）
  与 `/ramdisk/fstab.rg34xxsp`，`boot.img` 头各项几何全对（`kernel_addr 0x40080000` /
  `ramdisk_addr 0x43000000` / `page_size 2048` / `ramdisk_size 924660`）。
- 卡镜像 `tools/mk-sdcard.sh` 可用，产出 8 GiB 镜像并自带 5 项自检
  （GPT 恰好 5 个分区 / FAT32 卷标 BOOT / super 的 LP metadata 魔数 `67446c61` /
  boot 分区里 4 个文件 / 双端 sha256）。

## 六、还欠的

1. **上机实测**（唯一真正未知的一步）：用 Rufus 的 DD 模式把 `F:\dsh-dl\rg34xxsp-sd.img`
   写到 TF 卡，插机开机。观察顺序：U-Boot 有没有走 mmc0 → 内核有没有起来（串口 PH0/PH1，
   115200）→ 面板有没有点亮 → first-stage init 能不能挂上 /system。
2. **EGL**：`external/mesa3d` 是 host-only，编不出设备端 `libEGL_mesa`，现在用 SwiftShader 顶着。
   `/root/ref/panfork-mesa`（23.0.0-devel，带 `android/` 目录）是那块缺口，接进来要先把
   `external/libdrm` 升到 ≥2.4.109（补 `panfrost_drm.h`）并切 Bifrost 分支（本机 G31 MP2 不是 G78）。
3. **kernel/dts 收回自己维护**：现在用的是 ROCKNIX 验证过的二进制 dtb，
   自己那份覆盖式 dts 编不过（缺 `&panel` / `&joypad` label）。要做的话见
   `kernel/dtb/README.md` 里写的两条路线。
4. **低内存优化**：借 rk3326 的 `go_opt.mk`（`PRODUCT_DISABLE_SCUDO`、
   `TARGET_VNDK_USE_CORE_VARIANT`、`PRODUCT_MINIMIZE_JAVA_DEBUG_INFO`），等系统能起来再谈。

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
| 在 BoardConfig / 产品 mk 里写 `PRODUCT_ENFORCE_VINTF_MANIFEST := false` 完全没效果 | `build/make/core/config.mk:712-716` 用 `PRODUCT_FULL_TREBLE` 无条件覆盖它。**真开关是 `PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS`**，见第九节 |
| `mka bacon` 最后一步 `ln: cannot create hard link ... No such file` | `TARGET_SKIP_OTA_PACKAGE := true` 时不生成 OTA zip，但 bacon 的 ln 还在跑。**用 `mka droid`** |
| `sfdisk` 报 `last usable GPT sector is 16777212, but 16777215 is requested` | 末分区吃满了尾部保留区。镜像尾部要留 34 个扇区给备份 GPT 头和 8 项表 |
| 卡上 U-Boot 断言失败「no sunxi SPL at 8 KiB」 | 原厂 eMMC 的 SPL 是分支 `be0400ea`，ROCKNIX 的是 `160000ea`。**只校验 `xx xx 00 ea` + `eGON.BT0`，别写死偏移** |
| `boot.img` 里的内核和我们编的不是同一份 | `TARGET_PREBUILT_KERNEL` 指向 `device/anbernic/rg34xxsp/kernel/Image`，而该文件被 `.gitignore` 掉、`build-kernel.sh` 只"打印"了拷贝命令。**现在脚本会自己装进设备树并打印 sha256**；当前期望 `1e74ef643081216cf93b23da0bf3e8ee0d59c23f67c09796b963f4a63b71e91f` |

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

---

## 九、VINTF 兼容性检查：为什么只有这一个开关键有用

### 症状

`mka droid` 到 13% 死在这里：

```
files are incompatible: Runtime info and framework compatibility matrix are incompatible:
No kernel entry found for kernel version 7.1 ... Minimum LTS: 4.9.165, kernel FCM version: 4
```

编译目标是 `out/target/product/rg34xxsp/obj/PACKAGING/check_vintf_all_intermediates/check_vintf_compatible_log`。

### 为什么改不动

`PRODUCT_ENFORCE_VINTF_MANIFEST := false` 写在 BoardConfig 里、写在产品 mk 里，**都没用**。
`build/make/core/config.mk:712-716`：

```make
requirements := PRODUCT_TREBLE_LINKER_NAMESPACES PRODUCT_SEPOLICY_SPLIT \
                PRODUCT_ENFORCE_VINTF_MANIFEST PRODUCT_NOTICE_SPLIT
$(foreach req,$(requirements),\
  $(eval $(req) := $(if $($(req)_OVERRIDE),$($(req)_OVERRIDE),$(PRODUCT_FULL_TREBLE))))
```

这个 foreach 无条件覆盖 —— 变量最终就等于 `PRODUCT_FULL_TREBLE`（本体是 true）。

`_OVERRIDE` 那条路**更不能用**：紧接着一行 `PRODUCT_FULL_TREBLE := $(if $(filter false,$($(req))),false,...)`，
设 `PRODUCT_ENFORCE_VINTF_MANIFEST_OVERRIDE := false` 会把 `PRODUCT_FULL_TREBLE` 一起拉成 false，
VNDK / linker namespace / 一堆 treble 相关配置全灭，比原报错严重得多。

### 正确开关

```make
# device/anbernic/rg34xxsp/lineage_rg34xxsp.mk 末尾（必须在所有 inherit-product 之后）
PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS := false
```

依据：

- `build/make/core/Makefile:3920` `ifeq ($(PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS),true)`
  才把 `--kernel $$(cat $(BUILT_KERNEL_VERSION_FILE)):...` 加进 `check_vintf_compatible_args`；
- `build/make/core/product_config.mk:336-339` 该变量为空时默认置 true，所以必须显式写 false；
- AOSP 自己在 `build/make/core/Makefile:3947` 的注释里把它列为第 (3) 种解法。

它只砍掉"内核版本查表"那一半，HAL 那一半（gatekeeper / keymaster 之类）照查 —— 安全性没丢。
内核 7.1 在 Android 11 的 matrix 里查不到，是 2020 年的表没有 7.x 行，不是真不兼容。

实测确认：`VINTF_KERNEL_REQ=false`、`ENFORCE=true`、`FULL_TREBLE=true`，然后 `mka droid` 一次过。
