# 续接笔记（2026-11-07 夜 · 断电中断）

明天从这里接着干。不要重走已经排除过的路。

## 当前症状

- 插卡上电：**黑屏，电源指示灯亮**（说明 SPL + U-Boot 起来了，内核没起来）
- 卡上 `BOOT` 分区**没有 `DEBUG/` 目录**（内建 initramfs 的 debug-init 没机会跑）
- 卡的分区表完好（GPT，5 个分区，`Z:` 是 FAT32 `BOOT`），`Image` 33,974,280 B sha256 `840DACDB...F284FFA` 在位

## 今天最重要的两个发现

### 1. ROCKNIX 官方镜像用的是 **MBR（dos）**，我们用 GPT

```
label: dos
label-id: 0x2b6f7891
rocknix-head.img1 : start=32768, size=4194304, type=c, bootable   <- FAT32 启动分区
rocknix-head.img2 : start=4227072, size=65536, type=83
```

**这是我们和"能跑的镜像"之间最显眼的差异。** 首要怀疑：这个 U-Boot 的 distro_boot 读不出 GPT，
`scan_dev_for_extlinux` 里的 `test -e ${devtype} ${devnum}:${devpart} extlinux/extlinux.conf` 直接判否
→ 什么都没加载 → 黑屏 + 指示灯亮。

**下一步实验（最优先）**：改 `tools/mk-sdcard.sh` 出 **MBR/dos** 分区表（`sfdisk --label dos`，
boot 分区 start 32768 对齐 ROCKNIX），重出 sd5，让用户 Rufus 整盘写。

### 2. `linux` → `kernel` 那个"修复"很可能是**错的**，别当定论

ROCKNIX 自己的 `extlinux.conf`（就在它的 boot 分区里）写的是**大写 `LINUX`**，而且这块板子
确实能引导 ROCKNIX。既然 U-Boot 二进制我们逐字节比对过 —— **我们的 U-Boot 与 ROCKNIX 的完全相同**
（`cmp` 0 处差异，661,504 B）—— 那 `linux`/`LINUX` 关键字就是能用的。

> 之前的"关键字表里没有 `linux`"结论：那张表确实存在，但 `linux` 大概率是大小写不敏感地
> 被接受（或者走了另一条解析路径）。**不要因为这个把排查方向锁死在关键字上。**

保留 `kernel /Image` 写法无害（ROCKNIX 用 `LINUX`，我们两版都试过），但**根因另找**。

## ROCKNIX 的 extlinux.conf 原文（可直接抄格式）

```
LABEL ROCKNIX
  LINUX /KERNEL
  FDT /dtb.img
  APPEND boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet console=ttyS0,115200 console=tty0 systemd.debug_shell=ttyS0
  FDTOVERLAYS /overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo
```

注意它用 `FDT` 而不是 `devicetree`，还带 `FDTOVERLAYS`。值得照抄一版做对照。

## 已排除（别重做）

- **裸写 `\\.\PhysicalDriveN` 彻底不可用**：写进去 +1 扇区位移、每 64 KiB 首扇区丢失，
  而且**一律返回成功**。dd / C# P/Invoke / 对齐缓冲 / LOCK_VOLUME / DISMOUNT 全试过。
  → 写卡只能走 Rufus 整盘写，或已挂载 FAT 的 `Z:\` 文件级 `Copy-Item`。
- 面板 v1/v2 固件、看门狗、initrd 传递、super 分区、FAT/iocharset —— 都是在错误前提上打转，
  先搁置，等内核真跑起来再看。
- 内建 initramfs 本身是好的：`usr/initramfs_data.cpio` 12 条目、`init`=debug-init 662,608 B、
  `init.android`=2,090,096 B，`.config` 与内建前只差 5 个符号。

## 环境备忘

- 仓库 `C:\Users\LinOcean\Documents\deepseek-harness\default-workspace\anbernic-rg34xxsp`
- 未提交改动：`tools/debug-init/debug-init.c`、`tools/mk-sdcard.sh`（含 `--embed-initramfs`）
- WSL 跑脚本必须 `wsl -u root -e bash <LF 文件>`；多行脚本用
  `[System.IO.File]::WriteAllText("$env:TEMP\x.sh", ($sh -replace "`r`n","`n"))` 写 LF，
  `Out-File -Encoding utf8` 会带 BOM 导致 `set: command not found`
- 写卡上文本文件用 `New-Object System.Text.UTF8Encoding($false)` 避免 BOM
- 参考镜像：`F:\dsh-dl\ROCKNIX-H700-DDR3.img.gz`，已解出前 320 MB 到 `/root/rocknix-head.img`
  （boot 分区 offset = 32768*512 = 16,777,216）
- 现成镜像：`F:\dsh-dl\rg34xxsp-sd4.img`（8,589,934,592 B，sha256 `DB295502...04168B`）—— GPT 版
- Rufus：`F:\dsh-dl\rufus-4.10p.exe`
