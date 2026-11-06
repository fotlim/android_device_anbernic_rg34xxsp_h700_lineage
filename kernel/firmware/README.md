# 内建固件（编译进 Image 的 blob）

`tools/build-kernel.sh` 会把本目录**整棵**复制到内核树的 `firmware-extra/`，
再把每个文件的相对路径写进 `CONFIG_EXTRA_FIRMWARE`，配 `CONFIG_EXTRA_FIRMWARE_DIR="firmware-extra"`。
也就是说这些 blob 会变成 `Image` 的一部分，运行时**不**从文件系统读。

## 为什么要内建，而不是放 /vendor/firmware

因为这几样东西索要固件的时机都早得离谱：

| 文件 | 谁要 | 什么时候要 |
|---|---|---|
| `panels/anbernic,rg34xx-sp-panel.panel` | `panel-mipi.c` | 驱动 probe（initcall），早于任何文件系统挂载 |
| `panels/anbernic,rg34xx-sp-v2-panel.panel` | 同上（v2 屏机型） | 同上 |
| `regulatory.db` / `.p7s` | `cfg80211` | `subsys_initcall`，同样早于 `/vendor` |
| `rtw88/rtw8821c_fw.bin` | `rtw88` SDIO | netdev bring-up |
| `rtl_bt/rtl8821cs_fw.bin` + `rtl8821cs_config.bin` | `btrtl` | HCI open |

后三个理论上晚于 `/vendor` 挂载（first-stage init 会挂），放 `/vendor/firmware/` 也能工作，
但这样一来 WiFi 能不能起来就多了一个「挂载时序」的变量。既然前两个非内建不可，
干脆全部内建，把这一类问题一次性消掉 —— 代价只有 Image 大 200 KB 左右。

## 出处

全部从 ROCKNIX `20261101` 的 H700 DDR3 发行镜像里取出，路径
`/usr/lib/kernel-overlays/base/lib/firmware/`（在镜像的 `SYSTEM` squashfs 内）：

```
http://github.com/ROCKNIX/distribution/releases/download/20261101/ROCKNIX-H700.aarch64-20261101-DDR3.img.gz
sha256 6c5567f5e29a041a1ae1a94c3737c640f5345b77fbaffa4010e8e8ebc6546af1
```

提取方法（在 WSL 里挂 loop 或用 7-Zip 拆）：

```sh
7z x  ROCKNIX-H700.aarch64-20261101-DDR3.img -ounpack      # 得到 0.fat 与 SYSTEM
7z e  unpack/SYSTEM 'usr\lib\kernel-overlays\base\lib\firmware\panels\*'   -opanels_out -y
7z e  unpack/SYSTEM 'usr\lib\kernel-overlays\base\lib\firmware\rtl_bt\*'   -ortl_bt_out -y
7z e  unpack/SYSTEM 'usr\lib\kernel-overlays\base\lib\firmware\rtw88\*'    -ortw88_out  -y
7z e  unpack/SYSTEM 'usr\lib\kernel-overlays\base\lib\firmware\regulatory.db*' -oreg_out -y
```

**不要**去 linux-firmware 或 ROCKNIX 的 git 树里找 `panels/*.panel`：
前者根本没有（`git.kernel.org` 的 plain 链接会返回 HTML），后者只在
`projects/ROCKNIX/devices/H700/config/kernel-firmware.dat` 里列了个名字，
blob 本身随发行镜像发布。本鱼在这上面白跑过好几趟，记在这里省得重复。

## 校验值

| 文件 | 大小 | sha256（前 16 位） |
|---|---|---|
| `panels/anbernic,rg34xx-sp-panel.panel` | 562 | 见 `git log` |
| `panels/anbernic,rg34xx-sp-v2-panel.panel` | 826 | |
| `rtl_bt/rtl8821cs_config.bin` | 25 | |
| `rtl_bt/rtl8821cs_fw.bin` | 56648 | 以 ASCII `Realtech` 开头 |
| `rtw88/rtw8821c_fw.bin` | 139472 | 以 `21 88` 开头（芯片号 0x8821） |
| `regulatory.db` | 6380 | 以 ASCII `RGDB` 开头 |
| `regulatory.db.p7s` | 1085 | |

有魔数可以自查，比记 sha 实用：

```sh
head -c 4 kernel/firmware/regulatory.db        # RGDB
head -c 8 kernel/firmware/rtl_bt/rtl8821cs_fw.bin   # Realtech
xxd -l 2 kernel/firmware/rtw88/rtw8821c_fw.bin     # 2188
```

## 面板固件格式（`PANEL-FIRMWARE`）

`kikuchan` 的 panel-mipi 用的自定义二进制格式，本仓库这两个 blob 实测解析结果：

- magic `PANEL-FIRMWARE\0`（15 字节）+ `version = 1`（u16 LE）
- `width_mm = 71`、`height_mm = 48` → 3.5" 3:2，和实机一致
- 时序：hactive 720、hbp 47、hsync 4、hfp 42；vactive 480、vbp 33、vsync 4、vfp 16
- 像素时钟 u32 `0x00006590` = 26000 kHz = **26 MHz**
- 合计 813×533 @ 26 MHz ≈ **60.0 Hz**

对照 stock 固件（全志 BSP，`lcd_driver_name = "rg34xxsp_v1"`）：
820×536，ht=820 vt=536，dclk 26 MHz → 59.2 Hz。
ROCKNIX 这份把前后沿重新标定到整 60 Hz，**两者不通用**，本仓库跟 ROCKNIX 走。

固件缺失时的表现是「背光亮、屏全黑」，dmesg 里会有
`panel-mipi ... Failed to load firmware`。想自己生成改时序，用
`kikuchan/panel-firmware-generator`，参数取上表。
