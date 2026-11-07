# kernel/uboot —— 卡上的引导链

## 文件

`H700-DDR3-u-boot-sunxi-with-spl.bin`，661,504 B（512 字节对齐补零），
sha256 `03ddc07421cf0b858766bf72246e5241fbf3b5e2bf9a2ee4e98d58cfa1cbbb00`。

## 出处

从 ROCKNIX 官方 DDR3 镜像的头 8 KiB 往后切出来的：

```
F:\dsh-dl\unpack\ROCKNIX-H700.aarch64-20261101-DDR3.img
  -> dd if=...img of=...bin bs=4096 skip=2 count=162   (8192..669696)
  -> 真实内容 661,010 B, 写到 661,504 B（512 对齐，尾部补零）
```

## 它是什么

sunxi 的 `u-boot-sunxi-with-spl.bin` = **eGON SPL + ATF + U-Boot proper** 三段打包，
必须整块写到卡上 **8 KiB（扇区 16）** 处 —— BROM 固定从那里找 SPL，头 8 KiB 是保留区。

实测头部：

```
16 00 00 ea   ARM branch (SPL 第一条指令)
65 47 4f 4e 2e 42 54 30   "eGON.BT0"  (偏移 +4)
```

注意：原厂 eMMC 的 SPL 是 `be 04 00 ea`（分支偏移不同），ROCKNIX 的是 `16 00 00 ea`。
两者都合法，`tools/mk-sdcard.sh` 的断言因此只校验「`0xEA` 分支指令 + `eGON.BT0` tag」，
不写死具体偏移量。

版本串：`U-Boot 2026.01 (Nov 01 2026 - 06:28:14 +0000) Allwinner Tech`。

## 它怎么启动 Android

这份 U-Boot **没有 `abootimg` / `ANDROID!` 支持**（`strings | grep -c abootimg` = 0），
所以不能像手机那样 `bootm` 一个 boot.img。它走的是标准 distro boot：

```
boot_targets = fel mmc0 pxe dhcp
bootcmd      = run distro_bootcmd
boot_extlinux = sysboot ${devtype} ${devnum}:${distro_bootpart} any ${scriptaddr} ${prefix}extlinux/extlinux.conf
```

确认存在的命令：`fatload` / `fatwrite` / `ext4load` / `booti` / `bootm` / `part` / `sysboot`。
还有 `bootcmd_fel`，所以 Brick 了还能 FEL 救回来。

于是卡上的结构就是：FAT 分区里放 `extlinux/extlinux.conf` + `Image` + `initrd.img` + `dtb`，
U-Boot 用 sysboot 读 conf、`booti` 起内核。`tools/mk-sdcard.sh` 就是这么摆的。

**所以 Android 的 boot.img 在这条路线里是多余的** —— 内核、ramdisk（cpio.gz）、
androidboot.* cmdline 全部拆开由 U-Boot 传。boot.img 仍然会随 `mka droid` 产出来，
留着给「以后改回 eMMC + 原厂 SPL」那条路用。

## 如果以后要换回原厂引导

原厂 eMMC 的前 2 MiB 已完整备份在 `F:\dsh-backup\rg34xxsp-stock-20261106\`，
`tools/flash-boot.ps1 -Restore` 能写回。卡这条路完全不碰 eMMC。