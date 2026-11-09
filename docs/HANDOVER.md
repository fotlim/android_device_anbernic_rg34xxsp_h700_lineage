# 交接文档（2026-11-09 20:30）

给接手的同事。这份文档的目标是：**读完就知道现在卡在哪、已经排除过什么、下一步该动哪一行**，不用重走弯路。

---

## 0. 一句话现状

LineageOS 18.1（Android 11）已经**在这台机器上完整启动**（内核、eMMC、WiFi、蓝牙、GPU、手柄、RTC、音频全部工作，`debug-init` 能落日志），**只剩显示**：屏幕有背光、无图像。显示管线的寄存器已经逐项读回来核对过，**全部符合预期**，但面板不出画面。当前候选收敛到「面板固件变体」与「BLEND_ROUTE 语义」两条，sd21 镜像已备好待上机验证。

---

## 1. 硬件与目标

| 项 | 值 |
|---|---|
| 设备 | Anbernic RG34XXSP |
| SoC | Allwinner H700（`sun50i-h616` 系，4×A53） |
| GPU | Mali-G31 MP2（Bifrost，panfrost 驱动，日志 `mali-g31 id 0x7093`） |
| 内存 | **1 GiB LPDDR3**（用户选 Android 11 的原因） |
| 屏幕 | 720×480 3:2，SPI 初始化 + RGB 并行（PD0–PD27） |
| 手柄 | 单 ADC（`rocknix-singleadc-joypad`，树外模块，已收编） |
| PMIC | AXP2202（日志里是 AXP717/AXP20x 驱动） |
| WiFi | RTL8821CS（SDIO，`mmc@4021000`） |
| 存储 | 系统在 TF 卡（`mmc0` = `/dev/mmcblk0`）；eMMC 是 `mmc1` |

目标产物：`lunch lineage_rg34xxsp-userdebug`，设备树在 `device/anbernic/rg34xxsp`。

---

## 2. 仓库与目录

```
仓库      C:\Users\LinOcean\Documents\deepseek-harness\default-workspace\anbernic-rg34xxsp
内核树    WSL /tmp/lk-rocknix           （Linux 7.1.2 + ROCKNIX 补丁）
Android   WSL /root/android/lineage     （repo sync 完成，74 GB，已 mka droid 通过）
initramfs WSL /root/initramfs-embed     （init = debug-init，init.android = 真 init）
产物      WSL /root/artifacts           （各版本 Image 与 debug-init 二进制）
镜像      F:\dsh-dl\                    （sd*.img + rufus-4.10p.exe + dmesg-*.txt）
旧镜像    F:\dsh-dl\old-images\
```

**git 状态**：

- 仓库 HEAD **`3483a5d`**（`docs: handover notes for the display work`），工作区干净。
- 内核树在分支 **`rg34xxsp-display`**，HEAD **`0ab7af527`**（route 回滚，见 §5 候选 A）。原来是 detached HEAD，已顺手建了分支。`master` 指向 `f66469d99`（上游 7.4-rc2），**不要切过去**。

---

## 3. 已经彻底解决、不要重做的部分

### 3.1 卡必须用 hybrid MBR（这是「黑屏 + 指示灯亮」的根因）

我们的 U-Boot（`kernel/uboot/H700-DDR3-u-boot-sunxi-with-spl.bin`，661,504 B）与 ROCKNIX 镜像里那份**逐字节完全相同**，但 ROCKNIX 用 `label: dos`，我们最初用 GPT。

- **U-Boot 侧**：只注册了 `part_dos`，没有 `part_efi`。纯 GPT 的保护性 MBR 里只有一个 `type=0xEE` 整盘项 → `part list mmc 0 -bootable` 失败 → `fstype mmc 0:1` 失败 → `scan_dev_for_boot` 从不执行 → extlinux 与 boot.scr 都不会被扫描 → 黑屏 + 指示灯亮 + 卡上没有任何面包屑文件。
- **内核侧**：不能退回纯 MBR。`block/partitions/msdos.c:115` 把 `info->volname[0] = 0`，MBR 永远没有 `PARTNAME` → 没有 `/dev/block/by-name/*` → `fstab.rg34xxsp` 全失效。

**解法：hybrid MBR**（`tools/mk-sdcard.sh` 第 2b 节，python 直接改 sector 0）：
`[1] 0x0C bootable @2048 +131072`（U-Boot 用，**必须是第一项**，这样 boot 分区编号为 1）
`[2] 0xEE @1`（内核 `is_pmbr_valid()` 判为 `GPT_MBR_HYBRID` 后继续解析 GPT，分区名保住）
双解析器已用 python 复现两份 C 代码验证（header/entries CRC 都 match）。**这一步之后内核就起来了。**

### 3.2 `androidboot.boot_devices` 必须是 `4020000.mmc`

不是 `mmcblk0`。依据 `system/core/init/devices.cpp:186-212` 的 `FindPlatformDevice()`（`soc` 是 simple-bus 不是 platform bus，返回值去掉 sysfs 前缀 = `4020000.mmc`），以及 `devices.cpp:321-368`（只有 `is_boot_device` 才建 `/dev/block/by-name/*`）。写错会导致 `first_stage_init.cpp:302` 挂载失败 = `LOG(FATAL)` = init abort = 重启。

### 3.3 `linux` vs `kernel` 关键字：**已推翻，别再想**

`web_fetch` 拿到 U-Boot v2026.01 `boot/pxe_utils.c`：`keywords[]` 里 `{"kernel", T_KERNEL}` 和 `{"linux", T_LINUX}` 都有，`parse_label()` 里 `case T_KERNEL: case T_LINUX:` 共用同一分支。二进制里搜不到独立 `linux` token 是因为编译器合并了重复短串——**字符串表法对短 token 不可靠**。现在 `mk-sdcard.sh` 写 `kernel /Image`，两种都合法。

### 3.4 「满屏横向亮线」的一个已修根因：DE33 planes 驱动没编进去

`sun50i_planes.c` / `.h` 是 ROCKNIX `0002-rg35xx-enable-HDMI-LCD.patch` 里 `[PATCH 03/21]` 新增的文件。`build-kernel.sh` 原来用 `git apply --reject`，**子补丁 01/02/03/04 的 hunk 全被拒**（上下文漂移），只留下新建文件。

后果：mixer 照样 probe（compatible 在 `sun8i_mixer_of_table` 里），屏幕能亮，但 DE33 的 plane→通道映射寄存器（CCU `0x24`/`0x28`）从未被写过 → 出未配置的原始扫描线。

**修法**：`build-kernel.sh` 有 `3d/6`（02/03/04）与 `3e/6`（01/05/06/07/16 + 0003）两节，用 python 按 `^From [0-9a-f]{40} ` 从 0002 里切子补丁，逐个幂等应用（`apply_if` 按判据跳过），末尾九条源码断言 + 编完 Image 后七条 compatible 断言，缺一即 `exit 1`。另有 `3f/6` 补 `pwm-sun8i.c` 的 Kconfig/Makefile（同类孤儿文件，不补则背光永远不亮）。

### 3.5 裸写 `\\.\PhysicalDriveN` 彻底不可用

写进去 **+1 扇区位移**、每 64 KiB **首扇区丢失**，而且**一律返回成功**。dd / C# P/Invoke / 对齐缓冲 / `LOCK_VOLUME` / `DISMOUNT` / 补偿写全试过，无一可行。→ 写卡只能走 **Rufus 整盘写（DD 模式）**，或已挂载 FAT 的 `G:\` 文件级 `Copy-Item`。

### 3.6 其它已验证正确、不用再查的

- 内建 initramfs 本身是好的：`usr/initramfs_data.cpio` 2,768,896 B、`init`=debug-init、`init.android`=真 init，`.config` 与内建前只差 5 个符号。
- `allwinner,sun50i-h616-tcon-top` 在 Image 里 count=0 **是正常的**：DTS 首串是它，但驱动表里只有 `sun50i-h6-tcon-top`，靠第二串兜住。
- `allwinner,sun50i-h616-tcon-lcd` **不是** of_match 表项（只有 `0161` 用它做 `of_device_is_compatible` 判断），两边都靠 DTS 第二串 `allwinner,sun8i-r40-tcon-lcd` 匹配 `sun8i_r40_lcd_quirks`。
- pinctrl 的 `io_disabled` 缺失（56 条 `unsupported function io_disabled` 报错）**不影响功能**：`pinctrl-sunxi.c:484-491` 对不支持函数是 `continue` 不是失败，只是 sleep state 空。
- `possible_crtcs=0` 不是 bug：`drm_crtc.c:307-310` 给 primary 补、`sun4i_crtc.c:246-253` 给 overlay 补。
- 面板 SPI 三线 9-bit 通路软件层完全成立（`spi-gpio` 报 `SPI_BPW_RANGE_MASK(1,32)`，`mipi_dbi_typec1_command` 走硬件 9-bit 路径）。
- 背光默认就是亮的：`pwm_bl.c:212` 无 `brightness-levels` 时 `dft_brightness = max_brightness / 2`，**不是 0**。
- `smem_start=0x0` 是 `drm_leak_fbdev_smem` 未开导致的（`drm_fb_helper.c:363` 置 `FBINFO_HIDE_SMEM_START`，`drm_fbdev_dma.c:219` 跳过赋值），**不是故障**。
- 与 ROCKNIX 的差异已逐项排除：`.config`（只差 5 项且全 `=n`）、dtb 文件（v1 与 ROCKNIX 的 sha256 完全相同）、面板固件（逐字节相同）、`disp_regs`（ROCKNIX 也没有）、`tcon_top` 新旧版（两边都旧版）、blender base（两边都 `DE2_BLD_BASE 0x1000`）。

---

## 4. 显示链路：现在到底哪一步是好的

**全部 bind 成功**（sd13 之后每次启动都一样）：
`sun50i-planes→1100000.planes`、`sun8i-mixer→1280000.mixer + 12a0000.mixer`、`sun8i-tcon-top→6510000.tcon-top`、`sun4i-tcon→6511000.lcd-controller + 6515000.lcd-controller`、`sun4i-drm→display-engine`、`sun8i-pwm→300a000.pwm`、`pwm-backlight→backlight`、`panel-mipi→spi0.0`。`/sys/kernel/debug/devices_deferred` 为空。

**寄存器实测（sd21 之前那版 sd18 的 dump，逐项核对全部正确）**：

| 位置 | 实测 | 期望 | 判定 |
|---|---|---|---|
| CCU `0x24` CHN2CORE | `0x00100014` | 手算一致 | ✅ |
| CCU `0x28` PORT02CHN | `0x00000980` | 手算一致 | ✅ |
| CCU `0x2c` PORT12CHN | `0x00000a21` | 手算一致 | ✅ |
| mixer0 top `0x1008100` | CTL=1, STATUS=0x11, SIZE=0x01df02cf, CLK=1 | 720×480 | ✅ |
| mixer0 blend `+0x80` ROUTE | 0x1（sd14）/ 0x6（sd15–sd18） | ? | ⚠️ 见 §5 |
| blend `+0x90` MODE ×5 | `0x03010301` | `BLEND_MODE_DEF` | ✅ |
| blend BKCOLOR/OUTSIZE | `0xff000000` / `0x01df02cf` | 黑 / 720×480 | ✅ |
| ui ch6 `+0x00` ATTR | `0xff000401` | alpha=0xff, FBFMT=4=XRGB8888, EN=1 | ✅ |
| ui ch6 `+0x04` SIZE | `0x01df02cf` | 720×480 | ✅ |
| ui ch6 `+0x0c` PITCH | `0xb40` | 2880 = 720×4 | ✅ |
| ui ch6 `+0x10` LADDR | `0x7de00000` | CMA `0x7dc00000..` 内 | ✅ |
| tcon-top `+0x1c` PORT_SEL | `0x20` | 0003 hack 值 | ✅ |
| tcon0 GCTL | `0x80000000` | `TCON_ENABLE` | ✅ |
| tcon0 TCON0_CTL | `0x800001e0` | 使能 + CLK_DELAY=30 | ✅ |
| tcon0 DCLK | `0x80000003` | gate on, div=3 | ✅ |
| tcon0 BASIC0..3 | `02cf01df` / `032c002d` / `042a0013` / `00030003` | 720×480, H_TOTAL=813, V_TOTAL=1066 | ✅ |
| tcon0 IO_POL | `0x04000000` | 只有 `DCLK_DRIVE_NEGEDGE`（固件 `bus_flags=0xa` = DE_HIGH\|DRIVE_NEGEDGE，代码只对 `DE_LOW` 动作） | ✅ |
| tcon0 IO_TRI | `0` | 所有输出打开 | ✅ |
| pinctrl PD | `22222222 ×3, 00022222` | PD0–PD27 = `0x2` = `lcd0` | ✅ |
| pwm `0x300a000` | `+004=1`, `+020/+024=0x10` | 已使能 | ✅ |

**面板命令确实发出去了**：`panel_mipi_prepare()` 期间 2 ms 内发完 49 条 `mipi_dbi_typec1_command`（`ff,30` / `ff,52` / `ff,01` / `e3,01` / `0a,fd` / … / `b8,26` + 第二轮 `ff,02` / `b0,05` … / `b8,0f`），计数 `cmd=11`(EXIT_SLEEP)=2、`cmd=29`(DISPLAY_ON)=2、`cmd=04`(GET_ID)=2、`cmd=10`(ENTER_SLEEP)=0。`MIPI Display ID: 000000` 是三线无 MISO 的正常结果（`spi-gpio.c:76` `if (spi->mode & SPI_3WIRE) return 0;`）。

**`fbcon` 早在 4.03 秒就把启动文字画进 fb0**（`Console: switching to colour frame buffer device 90x30`）——用户看不到文字，所以断点确实在 fb0 内存与面板之间，不是「没人往 fb0 写」。

**唯一还没被解释的矛盾**：以上全部正确，面板依然「有背光、无图像」。

---

## 5. 当前两条候选（这是接手后要盯的）

### 候选 A：`BLEND_ROUTE` 要的是层号还是物理通道

sd14 与 sd15 恰好构成对照：

| 版本 | 内核 | `BLEND_ROUTE` | 屏幕 |
|---|---|---|---|
| sd14 | 原版（`layer->index`） | `0x1` | **密集亮线** |
| sd15 | 改成 `layer->channel` | `0x6` | **纯黑** |

**亮线 = 扫描链路在工作、数据用错格式/时序画了出来；纯黑 = 什么都没合成。** 所以结论是：`BLEND_ROUTE` 要的是**层号（index）**，`PORT02CHN` 那个 CCU mux 才是「层 → 物理通道」的映射。`layer->channel` 那个改动是**错误方向**。

> 这一条是本鱼自己改错又改回来的。`sun8i_mixer.c:315` 现在是 `route |= layer->index << ...`，已提交为内核树 `0ab7af527`（在分支 `rg34xxsp-display` 上），**sd21 用的 Image 就是这一版**。

### 候选 B：面板是 v2 变体，v1 固件点不亮它

`panel_mipi.c:447` 用 dtb 里 panel 节点的**第一个 compatible 字符串**拼固件名：`panels/<compatible>.panel`。

| dtb | panel 首串 | 固件 | 命令风格 |
|---|---|---|---|
| `...-rg34xx-sp.dtb`（v1） | `anbernic,rg34xx-sp-panel` | 562 B | `ff(30) ff(52) ff(01) e3(01) 0a(fd) …`（ILI9341/ST7789 系厂商寄存器序列） |
| `...-rg34xx-sp-v2-panel.dtb`（v2） | `anbernic,rg34xx-sp-v2-panel` | 826 B | `d7(61 74 97) d0(10) 60(ff) 61(ff) …`（ILI9881 系 gamma/GIP 表） |

**如果这台机器装的是另一颗 IC，v1 序列会让它「有背光但黑屏」——正是我们一直看到的现象。** sd21 的 extlinux `default` 已改成 `android-v2`，就是为了一次性判掉这一条。

（附带线索：ROCKNIX 通用镜像在这台机器上跑起来时，U-Boot 用的是内嵌的 rg35xx-2024 dtb，`panel@0` 首串是 `anbernic,rg35xx-plus-panel`（640×480）——**它连面板固件都不是 rg34xx-sp 那份**，所以「ROCKNIX 能亮」这件事不能作为「我们的 v1 固件是对的」的证据。）

---

## 6. 待上机的 sd21（已备好）

```
F:\dsh-dl\rg34xxsp-sd21.img
8,589,934,592 B
sha256 490975A9DE21A34B58982CA0E00F99A84E5D0AB2DB82F095C0E97C5F60F81C84
内核    /root/artifacts/Image.sd21 = 34,043,912 B, sha256 c1c0d1defe167e7a9f0672216ce7d8d3366b16e184be8eb03d0663b4d72cb844
debug-init /root/artifacts/debug-init.sd21 = 671,768 B, sha256 d200beaeeabbc42801de33fdbbd6eb91cd5fb77834d09079a29b565a6e87b7fd
```

三个改动：

1. **内核 route 回滚**为 `layer->index`（见 §5 候选 A）。
2. **`extlinux.conf` 的 `default` 改成 `android-v2`**，且所有 append 行加了 **`drm_leak_fbdev_smem=1`**（让 `FBIOGET_FSCREENINFO` 能给出 fb0 物理地址）。
3. **`display_probe()` 砍成单阶段**：涂不透明红 → 把 route 强制设为 `0x1` → **等 8 秒**。之前的八阶段各睡 3 秒，每次都被掐断（sd16/sd18 都在中途没了），**被掐断的测试什么都不能证明**。

`display_probe()` 同时会打印 `S0:`（fb0 `smem_start` / pagemap / ch6 LADDR 三者对比）、`S0b:`（若地址不同则把 LADDR 改成 fb0 真实物理地址再涂红 6 秒）、`D1:`（route 强制前后的值）。

**上电后按这三种结果分流**：

| 观察 | 结论 | 下一步 |
|---|---|---|
| 出红 8 秒 | route 定论 | 只剩格式/时序：查 `FBFMT`（当前 `4`=XRGB8888）与面板总线位宽 |
| 又出亮线 | 我们涂的缓冲不是通道扫的缓冲 | 看 `S0:` 的地址比较结果 |
| 仍全黑 | 断点在 blender 之后 | 查 `GLOBAL_DBUFF`（DE33 从不 latch，见下）/ `OUTCTL` / tcon 输出极性 |

读 `G:\DEBUG\dmesg.txt`，重点 `S0:` / `S0b:` / `D1:` 三行 + `handing over` 是否出现（这次应该能跑到底）。

---

## 7. 还没验证过的候选（sd21 之后如果还不行）

- **`GLOBAL_DBUFF` 从不 latch**：`sun8i_mixer.c:331` 的 `regmap_write(engine->regs, SUN8I_MIXER_GLOBAL_DBUFF, ENABLE)` 被 `if (de_type != SUN8I_MIXER_DE33)` 包住。DE2/DE3 用 shadow bank 需要这个 latch 才把新配置推到前台。**但 ROCKNIX 内核同样跳过、ROCKNIX 也点不亮这块屏** → 更像上游共享 bug 而非我们的移植问题。sd20 曾想测它，被砍掉了；`debug-init` 里手写 `*(mixer+0x08) = 1` 即可验证。
- **`SUN4I_TCON0_HV_IF_REG 0x58`** 全树只有定义、**无人写**，保持复位值 0。若面板是 HV 模式（不是 DE 模式），这个寄存器可能要配。
- **`mixer0 top GLOBAL_STATUS = 0x11`** 语义未知（`sun8i_mixer.h` 没定义任何 STATUS 位宏）。若它含 underflow/error 位，可能是线索。
- **`tcon0 GINT0 = 0xc8000003`**：两次 dump 时机不同（sd13 是 `0x00008003`），说明 `drm_crtc_vblank_on()` 确实开了中断。不是故障，但比较寄存器时要注意时序。

---

## 8. 工作流与坑（照做能省几小时）

### 8.1 改 debug-init → 出镜像

```bash
# 1. 编译（-static 必须；产物要 readelf -d 确认无 NEEDED）
aarch64-linux-gnu-gcc -static -O2 -Wall -Wextra -o /root/artifacts/debug-init.X /path/debug-init.c

# 2. 装进 initramfs（注意必须 chmod 755）
cp /root/artifacts/debug-init.X /root/initramfs-embed/init && chmod 755 /root/initramfs-embed/init

# 3. 重编内核（改了 .config 里的 INITRAMFS_SOURCE 才会重新打包）
cd /tmp/lk-rocknix
rm -f usr/initramfs_data.cpio usr/initramfs_data.o usr/built-in.a arch/arm64/boot/Image
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j12 Image

# 4. 出镜像
cd /mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/anbernic-rg34xxsp
bash tools/mk-sdcard.sh --out /root/rg34xxsp-sdNN.img \
  --kernel /root/artifacts/Image.X \
  --super /root/android/lineage/out/target/product/rg34xxsp/super.img \
  --embed-initramfs --force

# 5. 拷到 F 盘（必须从 WSL 侧 cp，Windows 访问 \\wsl.localhost\...\root 会 Access denied）
cp /root/rg34xxsp-sdNN.img /mnt/f/dsh-dl/
```

### 8.2 读卡上日志

- 卡挂成 `G:`（不是 `Z:`，盘符会漂）。`Get-Volume G` 常报 `Full Repair Needed`。
- **文件读不出来（`The file or directory is corrupted and unreadable`，err 1392）先跑 `cmd /c "chkdsk G: /F"`** —— FAT 目录项大小坏了，数据通常没丢。sd13 的 166,273 B 变成 164,352 B 后可正常读。
- **PowerShell 5.1 的 `[System.Text.Encoding]::Latin1` 不可用**（报 `不能对 Null 值表达式调用方法`）→ 读日志统一走 WSL 的 `grep -a` / `tail -c` / `xxd`。
- 日志里有 `\x0a` 之类的转义序列，**必须 `grep -a`**。

### 8.3 脚本执行

- **WSL 跑脚本必须 `wsl -u root -e bash <LF 文件>`**。`wsl -e bash -c "..."` 会被 PowerShell 引号解析搞坏（`TerminatorExpectedAtEndOfString`）。
- 多行脚本写成独立 `.sh` 文件：`[System.IO.File]::WriteAllText("$env:TEMP\x.sh", ($sh -replace "`r`n","`n"))`，再 `wsl -u root -e bash /mnt/c/.../Temp/x.sh`。**不要用 `Out-File -Encoding utf8`**（带 BOM 导致 `set: command not found`）。
- 写卡上文本文件用 `New-Object System.Text.UTF8Encoding($false)` 避免 BOM。
- 本机**没有 `pwsh`**，只有 Windows PowerShell 5.1；测试脚本要存 `.ps1` 用 `powershell -NoProfile -ExecutionPolicy Bypass -File` 跑（`Add-Type` 定义的类型只在同一进程内有效）。

### 8.4 git 提交

- 仓库无 `user.name`/`user.email`，历史作者是 `dsh <dsh@local>` → 必须 `git -c user.name='dsh' -c user.email='dsh@local' commit`。
- **PowerShell here-string 当 commit message 会被当成多个 pathspec**（`error: pathspec 'every' did not match any file(s)`）→ 把 message 写进文件再 `git commit -F <file>`。

### 8.5 看门狗（曾经坑了三次）

`sunxi-wdt` 默认 **16 秒**到期硬复位（`WDT_MAX_TIMEOUT 16`，`nowayout=0`）。**任何超过 16 秒的探测都会被掐断**，而掐断的日志看起来像「跑完了」。sd13（涂色 18 s）、sd14（13 s sleep）、sd16（9 s + dump）全被这个坑过。

`debug-init` 的 `wdt_start()` 现在用 **`ioctl(fd, WDIOC_SETOPTIONS, WDIOS_DISABLECARD)` 直接把定时器关掉**，`wdt_stop()` 在交棒前用 `WDIOS_ENABLECARD` 重新武装（这样 Android 真挂死仍会重启）。日志里应看到 `watchdog: timer disabled for the probe` 与 `watchdog: timer re-armed for handover`。

> 之前试过 fork 子进程喂狗，**不可靠**：父子共享同一 `wd_data`，`watchdog_release()` 的 magic 位也是共享的，子进程任何异常都会静默丢掉整轮。

---

## 9. 其它待办（与显示无关，排在显示之后）

1. `panel_mipi_get_modes()` 每次都调 `drm_connector_set_panel_orientation()` → 每次 probe 新建属性 → 属性泄漏 + `drm_mode_object.c:46` / `:246` 双 WARN（日志里能看到两次）。
2. 补打 `0155`（`sun4i_rgb.c:225` `DRM_MODE_CONNECTOR_Unknown` → `DRM_MODE_CONNECTOR_DSI`，现在日志里显示 `Unknown-1`）。
3. 补打 `0162` 的 `pinctrl-sun50i-h616.c` 部分（PD0–PD27 加 `SUNXI_FUNCTION(0x7, "io_disabled")`，消 56 条报错；已知不影响功能）。
4. panfork-mesa 接 18.1（需 libdrm ≥ 2.4.109 + Bifrost 分支；本机是 G31 MP2 不是 G78）。
5. `kernel/dts` 收回自维护（见 `kernel/dtb/README.md` 的两条路线）。
6. 低内存优化（1 GiB 内存）：`PRODUCT_DISABLE_SCUDO` / `TARGET_VNDK_USE_CORE_VARIANT` / `PRODUCT_MINIMIZE_JAVA_DEBUG_INFO`。
7. 8 GiB 镜像写到 32 GB 卡上会触发 `GPT: Primary header thinks Alt. header is not at the end of the disk` / `GPT:16777215 != 62333951` 警告（**不影响启动**）。可让 `mk-sdcard.sh` 把 altLBA 留 0 或按卡实际大小生成。

---

## 10. 显示链路完整清单（Image 内 compatible 已验证 count=1）

| compatible | 驱动 | ROCKNIX 子补丁 |
|---|---|---|
| `allwinner,sun50i-h616-de33-planes` | `sun50i_planes.c` | 03 |
| `allwinner,plane-mapping`（属性） | `sun50i_planes.c` | 03 |
| `allwinner,sun50i-h616-de33-mixer-0` | `sun8i_mixer.c` | 04 |
| `allwinner,sun50i-h616-display-engine` | `sun4i_drv.c` | 07 |
| `allwinner,sun50i-h616-tcon-tv` | `sun4i_tcon.c` | 05 |
| `allwinner,sun8i-r40-tcon-lcd` | `sun4i_tcon.c` | 16 |
| `allwinner,sun50i-h616-hdmi-phy` | `sun8i_hdmi_phy.c` | 06 |
| `allwinner,sun50i-h616-pwm` | `pwm-sun8i.c` | 0008 |

外加 0003 的 tcon-top 首行 hack（`writel(0x20, regs + TCON_TOP_PORT_SEL_REG)`）。

---

## 11. 关键文件索引

| 路径 | 作用 |
|---|---|
| `tools/mk-sdcard.sh` | 出 TF 卡镜像（hybrid MBR + GPT + super + boot 分区 + boot.scr 面包屑 + extlinux.conf） |
| `tools/build-kernel.sh` | 打补丁 + 配置断言 + 编 Image（3d/3e/3f 三节处理孤儿文件） |
| `tools/debug-init/debug-init.c` | 包装真 init：快照 / MMIO dump / 显示探针 / 看门狗控制，最后 `execl("/init.android")` |
| `tools/audit-patches.py` | 逐条检查补丁的**新增行**是否真进了树（`git apply --reject` 与 `patch -F3` 都会静默丢 hunk） |
| `tools/expect-regs.py` | 打印 720×480 正确状态下每个寄存器的期望值 |
| `docs/DISPLAY-THEORY.md` | 已排除嫌疑表 + 候选 + DE33 地址换算表 + 一次上机切分候选的判定表 |
| `docs/HARDWARE.md` | 真机硬件事实 |
| `docs/STATUS.md` / `docs/BUILD.md` / `docs/PORTING.md` | 早期构建与移植记录 |

---

## 12. 建议的接手顺序

1. 读 §5 与 §6，理解「route 要 index 不要 channel」这个反转。
2. 让用户刷 `sd21`（sha256 `490975A9...81C84`），按 §6 的三种结果分流。
3. 若出红 → 转查格式/位宽（`FBFMT`、面板总线位宽、`TCON0_HV_IF`）。
4. 若还全黑 → 测 `GLOBAL_DBUFF` 手写 latch（§7 第一条）。
5. 内核树已经在 `rg34xxsp-display` 分支上，route 回滚也已提交（`0ab7af527`），不用再动 git。
