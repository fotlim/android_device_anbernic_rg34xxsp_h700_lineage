# 施工状态与续接点

> 这份文件是给"关机之后重新开工"用的。所有数字都是实测值，不是估计。

最后更新：2026-11-06 晚上（因断电暂停）

---

## 一、当前进度一句话

内核**已经编出可用 Image**（真编、含 SELinux），Android 系统**正在编**（增量剩余 17050 个目标），
SD 卡启动所需的 U-Boot **已经从 ROCKNIX 镜像里提取到位**。

## 二、明天开工的三条命令

```bash
# 1. 确认内核产物还在
ls -l /root/artifacts/Image            # 期望 32,020,488 字节

# 2. 续编 Android（增量，不会从头来）
bash /mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/tmp/start-bacon2.sh
#   日志：/tmp/bacon2.log     退出码：日志末行 BACON_RC=

# 3. 看进度
bash /mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/tmp/st2.sh
```

产物目录：`/root/android/lineage/out/target/product/rg34xxsp/`

## 三、必须记得改的一个参数（构建完成后再改）

`device/anbernic/rg34xxsp/BoardConfig.mk`：

```
BOARD_RAMDISK_OFFSET := 0x02000000     ← 现在，余量只剩 983 KB
BOARD_RAMDISK_OFFSET := 0x03000000     ← 改成这个，余量 16 MB
```

原因：内核 32,020,488 B 放在 `0x40080000`，**结束于 `0x41F0A0C8`**；ramdisk 原厂在 `0x42000000`，
两者只隔 983 KB。原厂内核只有 17.7 MB 所以不缺地方，我们加了 SELinux + netfilter + audit 后大了 12 MB。
改完只需重跑 `mka bootimage`（几秒），不用整编。

## 四、SD 卡启动路线（当前方向）

### 已确认的事实

| 项 | 值 |
|---|---|
| U-Boot 文件 | `F:\dsh-dl\uboot\H700-DDR3-u-boot-sunxi-with-spl.bin` |
| 大小 / sha256 | 661,504 B / `03ddc07421cf0b858766bf72246e5241fbf3b5e2bf9a2ee4e98d58cfa1cbbb00` |
| 版本 | U-Boot 2026.01 (Nov 01 2026) Allwinner Tech，DDR3 |
| 来源 | 从 `ROCKNIX-H700.aarch64-20261101-DDR3.img` 的 8 KiB 处切出（真实长度 661,010 B，末尾是零填充，按 512 对齐） |
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

注意第一个分区从扇区 73728（36 MiB）开始，不是 1 MiB —— 前面全留给引导程序。

### 卡写入被 Windows 卡住了（明天要解决）

`\\.\PhysicalDrive3` 可以打开、`IOCTL_DISK_GET_LENGTH_INFO` 拿到 31.91 GB、
读 16 KiB 也正常，但**第一次 `write()` 就 `OSError: [Errno 22] Invalid argument`**。

排查方向（按优先级）：

1. **卷还挂着** —— `G:` / `H:` 两个卷是这张卡上的，Windows 持有它们，raw 写被拒。
   先 `dismount`/删除盘符再写；`Get-Volume` 显示其 Size=0，说明文件系统已经坏了，本来就该清掉。
2. 用 `CreateFileW` + `FILE_FLAG_NO_BUFFERING` 重开句柄（ctypes 直接调 Win32，不走 python 的 `open()`），
   写入长度和偏移都按 512 对齐。
3. 绕开 raw 写：在 WSL 里做 **loop 文件**（`losetup -fP` 已确认可用，`mkfs.vfat`/`sgdisk` 都在），
   把卡的前 N 段做成镜像文件，再从 Windows 侧按偏移写入。
4. 最后手段：`diskpart` 对可移动介质拒绝 `offline`（已实测报"此操作不受移动媒体支持"），
   所以 **`wsl --mount --bare` 这条路走不通**，不要再去试。

## 五、已经做完的（不需要再动）

- 内核：7.1.2 + 31 个 ROCKNIX 补丁 + 3 个通用补丁，编出 `Image` 32,020,488 B，
  配置断言 17 条全过（SELinux / BINDERFS / MEMFD_CREATE / netfilter legacy / BT H4 / 面板 / 手柄）。
- 面板固件 `panels/anbernic,rg34xx-sp-panel.panel` 已入库并内建进内核（`CONFIG_EXTRA_FIRMWARE`）。
- 手柄驱动 `rocknix-singleadc-joypad` 已收编为内建，`joypad_input_g` 由 0004 补丁提供。
- Android 设备树：`breakfast lineage_rg34xxsp-userdebug` rc=0，图形栈按真实模块接线
  （`hwcomposer.drm_minigbm` / `composer@2.2-service` / `allocator@4.0-service.minigbm` / `mapper@4.0-impl.minigbm`），
  `fstab` 已改为进 ramdisk，`manifest.xml` / `vendor.prop` 已就位。
- 原厂备份完整：`F:\dsh-backup\rg34xxsp-stock-20261106\`（含 SHA256SUMS，7-Zip 解压后大小与分区严格一致）。
- 刷 eMMC 的脚本 `tools/flash-boot.ps1`（含 7 道校验，`-Restore` 回滚），dry-run 已验证。

## 六、还欠的

1. **EGL**：`external/mesa3d` 是 host-only，编不出设备端 `libEGL_mesa`。
   `/root/ref/panfork-mesa`（23.0.0-devel，带 `android/` 目录）是唯一缺口，要接进 18.1 的 soong。
   这是 M1（SurfaceFlinger 出画面）之前最后一块。
2. 构建跑完后：改 `BOARD_RAMDISK_OFFSET` → `mka bootimage` → 得到 `boot.img`。
3. 卡路线：解决 raw 写 → 分区（GPT table-length=8）→ U-Boot 写 8 KiB → FAT 启动分区放
   `Image` / DTB / `initrd` / `extlinux/extlinux.conf` → `super.img` 写进对应分区。
4. 掌机实测：插卡开机看 U-Boot 是否走卡（`boot_targets` 的 mmc0）、能否进 Android first-stage。

## 七、踩过的坑（别再踩）

| 现象 | 真因 |
|---|---|
| `repo sync` 进程在、零进度、日志空 | `git config` 里 `http.https://android.googlesource.com/.proxy` 是**空字符串** = 该域名禁用代理 → 全部直连被墙。用 `--unset-all` 后重设 |
| `wsl.exe -u root bash -c "..."` 报 `unexpected EOF` | PowerShell 吞引号。**一律写 .sh 文件再 `bash /mnt/c/...`** |
| `zstd -t` 说 CORRUPT | WSL 根本没装 zstd，是"命令不存在"。Windows 侧用 7-Zip 解 `.zst` |
| `wsl --mount --bare` 失败 `ERROR_INVALID_DRIVE` | 可移动介质不支持 `diskpart offline`，这条路对本机无效 |
| PowerShell 改文件后中文乱码 | `Get-Content/-replace/Set-Content` 按 GBK 解码。**中文文件一律用 python3 + `io.open(...,newline='\n')` 改** |
| `git commit -F -` / `<<<` 报错 | PowerShell 没有 heredoc。把 message 写进 `$env:TEMP\*.txt` 再 `git commit -F <file>` |

---

## 八、构建最后一次失败的根因（22:56:25，断电前 3 分钟拿到）

```
FAILED: out/target/product/rg34xxsp/obj/APPS/Updater_intermediates/dexpreopt.sh
/bin/bash -c "out/soong/host/linux-x86/bin/dexpreopt_gen -global_soong -global \
   -module .../dexpreopt.config -dexpreopt_script .../dexpreopt.sh -out_dir out"
--global configuration file is required
ninja failed with: exit status 1        BACON_RC=1
```

注意 `-global_soong` 和 `-global` 后面**是空的** —— 参数传进来了但值是空串，
所以 `dexpreopt_gen` 认为没给全局配置。失败的是 3 个 APK 的 dexpreopt 规则
（`Updater` / `TrebuchetQuickStep` / `ThemePicker`），其余目标全编过了，进度 18%。

### 明天的两条修法（按顺序试）

1. **先确认 Soong 有没有产出全局配置**：
   `ls -l out/soong/dexpreopt_global.json out/soong/dexpreopt.config`
   如果都不存在，说明 `DexpreoptGlobalConfig` 这个 singleton 没被喂数据 —— 通常是
   设备没继承到定义 boot image / dexpreopt 的 common mk。
2. **想最快解封**：`BoardConfig.mk` 加 `WITH_DEXPREOPT := false`。
   本机只有 1 GiB，dexpreopt 迟早要调成 `WITH_DEXPREOPT_BOOT_IMG_AND_SYSTEM_SERVER_ONLY := true`
   这种轻量档；先把构建跑通再回头优化启动速度是合理的顺序。

不要急着重编 —— 增量只有约 13800 个目标，改完 `WITH_DEXPREOPT` 后重跑 `mka bacon` 很快。
