# 续接笔记（2026-11-08 上午 · 显示链路修完，等上机验证）

明天从这里接着干。不要重走已经排除过的路。

## 现在的状态

- 镜像：**`F:\dsh-dl\rg34xxsp-sd9.img`**
  - 8,589,934,592 B，sha256 `eb6c693075822ff04e33219216b143229298205e7c007906f4e74a269c2fccbd`
  - 内含内核 `/root/artifacts/Image.planes2`（34,043,912 B，sha256 `b799ed87...3004df`）
- 写卡工具：`F:\dsh-dl\rufus-4.10p.exe`（1,936,744 B，Authenticode Valid）
- **待办**：Rufus 整盘写 sd9 → 上电 → 拔卡看 `BOOT` 分区

## 今天解决的三个真问题

### 1. 黑屏根因：分区表 —— 已改成 hybrid MBR（同时喂 U-Boot 和内核）

我们的 U-Boot（`kernel/uboot/H700-DDR3-u-boot-sunxi-with-spl.bin`，661,504 B）与 ROCKNIX
镜像里的那份**逐字节完全相同**，但 ROCKNIX 用 `label: dos`，我们原来用 GPT。

- U-Boot 侧：只认 MBR（part_dos），GPT 保护性 MBR 里只有一个 `type=0xEE` 整盘项 →
  `part list mmc 0 -bootable` 失败 → `fstype mmc 0:1` 失败 → `scan_dev_for_boot` 从不执行
  → extlinux 和 boot.scr 都不会被扫描 → **黑屏 + 指示灯亮 + 卡上无任何面包屑**。
- 内核侧：不能退回纯 MBR。`block/partitions/msdos.c:115` 把 `info->volname[0] = 0`，
  MBR 永远没有 `PARTNAME` → 没有 `/dev/block/by-name/*` → `fstab.rg34xxsp` 全失效。

**解法：hybrid MBR**。`tools/mk-sdcard.sh` 第 2b 节用 python 直接改 sector 0：
`[1] 0x0C bootable @2048 +131072`（U-Boot 用，必须是第一项）+ `[2] 0xEE @1`（内核
`is_pmbr_valid()` 判为 `GPT_MBR_HYBRID` 后继续解析 GPT，分区名保住）。
双解析器已用 python 复现两份 C 代码验证通过（header/entries CRC 都 match）。

### 2. 「满屏横向亮线」根因：DE33 planes 驱动根本没编进去

`sun50i_planes.c` / `.h` 是 ROCKNIX 补丁 `0002-rg35xx-enable-HDMI-LCD.patch` 里
`[PATCH 03/21]` 新增的文件。`build-kernel.sh` 原来用 `git apply --reject` 打这个补丁，
**子补丁 01/02/03/04 的 hunk 全被拒**（上下文漂移），只留下新建文件。

后果：mixer 照样 probe（compatible 在 `sun8i_mixer_of_table` 里），屏幕能亮，
但 DE33 的 plane→通道映射寄存器（`0x24`/`0x28`）从未被写过 → 出的是未配置的原始扫描线。

**修法**：`build-kernel.sh` 新增 `3d/6`（02/03/04）与 `3e/6`（01/05/06/07/16 + 0003）两节，
用 python 按 `^From [0-9a-f]{40} ` 从 0002 里切子补丁，逐个幂等应用（`apply_if` 按判据跳过），
末尾九条断言 + 编完 Image 后七条 compatible 断言，缺一即 `exit 1`。

### 3. `linux` vs `kernel` 关键字：**已推翻，别再往这上面想**

`web_fetch` 拿到 U-Boot v2026.01 `boot/pxe_utils.c`：`keywords[]` 里
`{"kernel", T_KERNEL}` 和 `{"linux", T_LINUX}` 都有，`parse_label()` 里
`case T_KERNEL: case T_LINUX:` 共用同一分支 → **`linux` 完全合法**。
二进制里搜不到 `linux` 这个独立 token 是因为编译器合并了重复短串，字符串表法对短串不可靠。

（`mk-sdcard.sh` 现在写 `kernel /Image`，无害；`Z:\extlinux\extlinux.conf` 的注释里还留着
那条错误说明，下次上机时顺手改掉。）

## 显示链路完整清单（都在 Image.planes2 里验证过 count=1）

| compatible | 驱动 | 子补丁 |
|---|---|---|
| `allwinner,sun50i-h616-de33-planes` | `sun50i_planes.c` | 03 |
| `allwinner,plane-mapping`（属性） | `sun50i_planes.c` | 03 |
| `allwinner,sun50i-h616-de33-mixer-0` | `sun8i_mixer.c` | 04 |
| `allwinner,sun50i-h616-display-engine` | `sun4i_drv.c` | 07 |
| `allwinner,sun50i-h616-tcon-tv` | `sun4i_tcon.c` | 05 |
| `allwinner,sun8i-r40-tcon-lcd` | `sun4i_tcon.c` | 16 |
| `allwinner,sun50i-h616-hdmi-phy` | `sun8i_hdmi_phy.c` | 06 |

外加 0003 的 tcon-top 首行 hack（`writel(0x20, regs + TCON_TOP_PORT_SEL_REG)`）。

**注意**：`allwinner,sun50i-h616-tcon-top` 在 Image 里 count=0 —— 但 DTS 里 tcon-top 节点的
compatible 是 `"allwinner,sun50i-h616-tcon-top\0allwinner,sun50i-h6-tcon-top"`，
驱动表里只有 `sun50i-h6-tcon-top`，靠第二串兜住，**这是正常的，不是缺漏**。

## 上机后怎么判断走到哪一步

卡上 `BOOT` 分区会出现这些文件（`boot.scr` 面包屑，`tools/mk-sdcard.sh` 第 7b 节生成）：

| 文件 | 含义 |
|---|---|
| `BREAD.TXT` | boot.scr 被 `source` 了（U-Boot 已经认出分区） |
| `STEP1DTB.TXT` | `fatload` dtb 成功 |
| `STEP2IMG.TXT` | `fatload /Image` 成功 |
| `STEP3FAIL.TXT` | `booti` 返回了（即内核没接住） |

另外 `DEBUG/ran.txt` = debug-init 跑起来了；`DEBUG/dmesg.txt` = 内核日志落盘了。

## 已排除（别重做）

- **裸写 `\\.\PhysicalDriveN` 彻底不可用**：写进去 +1 扇区位移、每 64 KiB 首扇区丢失，
  而且**一律返回成功**。dd / C# P/Invoke / 对齐缓冲 / LOCK_VOLUME / DISMOUNT 全试过。
  → 写卡只能走 Rufus 整盘写，或已挂载 FAT 的 `Z:\` 文件级 `Copy-Item`。
- `linux` 关键字（见上，已推翻）。
- 面板 v1/v2 固件、看门狗、initrd 传递、super 分区、FAT/iocharset —— 都是在错误前提上打转，
  先搁置，等内核真跑起来再看。
- 内建 initramfs 本身是好的：`usr/initramfs_data.cpio` 2,759,680 B / 12 条目、
  `init`=debug-init 662,608 B、`init.android`=2,090,096 B，`.config` 与内建前只差 5 个符号。

## 环境备忘

- 仓库 `C:\Users\LinOcean\Documents\deepseek-harness\default-workspace\anbernic-rg34xxsp`
- 未提交改动：`tools/build-kernel.sh`（+150 行，3d/3e 两节 + 断言）、`tools/mk-sdcard.sh`
  （+196/-14 行，hybrid MBR + 面包屑 + `--embed-initramfs` + `INITRD_SUFFIX`）
- 内核树 `/tmp/lk-rocknix`，已提交 `b9f828cf8`（DE33 planes 02-04）；01/05/06/07/16/0003
  **在工作区未提交**（下次重跑 `build-kernel.sh` 会自动重打）
- WSL 跑脚本必须 `wsl -u root -e bash <LF 文件>`；多行脚本写成独立 `.sh` 文件再执行，
  `wsl -e bash -c "..."` 会被 PowerShell 引号解析搞坏
- 写卡上文本文件用 `New-Object System.Text.UTF8Encoding($false)` 避免 BOM
- 参考镜像：`F:\dsh-dl\ROCKNIX-H700-DDR3.img.gz`，已解出前 320 MB 到 `/root/rocknix-head.img`
  （boot 分区 offset = 32768*512 = 16,777,216）
- 旧镜像都在 `F:\dsh-dl\old-images\`（sd / sd2 / sd3 / sd4 / sd5 / sd6 / sd7 / sd8）
