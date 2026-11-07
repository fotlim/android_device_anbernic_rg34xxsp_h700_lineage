# kernel/dtb —— 为什么这里放的是二进制，不是从源码编的

## 内容

| 文件 | 大小 | 用途 |
| --- | --- | --- |
| `sun50i-h700-anbernic-rg34xx-sp.dtb` | 50,448 B | **上机用的就是这个**（v1 面板） |
| `sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb` | 50,460 B | 面板换版（v2）时用 |
| `rocknix-rg34xx-sp-reference.dts` | 58,899 B | 上面两个 dtb 的反编译文本，只读参考，不参与编译 |

## 出处

全部从 ROCKNIX 官方发行镜像里取的，不是我们编的：

```
ROCKNIX-H700.aarch64-20261101-DDR3.img.gz        (1.2 GB, ROCKNIX/distribution releases)
  -> 7z 解出 0.fat (FAT 引导分区, 155 MB)
     -> losetup -fP 0.fat && mount
        -> device_trees/sun50i-h700-anbernic-rg34xx-sp.dtb        (50,448 B)
        -> device_trees/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb (50,460 B)
```

- 选 DDR3 那一版是因为本机内存是 LPDDR3 —— 之前实测 `MemTotal 996480 kB`，与 ROCKNIX 的
  `overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo` 对应。
- sha256：
  - `f7fe1d6e1099b4db46f4bdb9fe73d2ed9cdd0c52da69465f932971bd8da99f58`  v1 面板
  - `345e4cd1477c3137b391b2e013401dc1b4a881bd22d32204686c185770c1bc54`  v2 面板
- 这份 dtb 的 `compatible` 是 `anbernic,rg34xx-sp`，与 `docs/HARDWARE.md` 里记的真机值一致；
  面板节点 `compatible = "anbernic,rg34xx-sp-panel", "panel-mipi-dpi-spi"`，正好匹配我们编进
  Image 的 `panel-mipi` 驱动（`CONFIG_DRM_PANEL_MIPI=y`）。

## 为什么不从 `kernel/dts/*.dts` 编

`kernel/dts/sun50i-h700-anbernic-rg34xx-sp.dts` 只有 37 行，是个 include 式覆盖：

```dts
#include "sun50i-h700-anbernic-rg35xx-sp.dts"
&panel { compatible = "anbernic,rg34xx-sp-panel", "panel-mipi-dpi-spi"; ... };
&joypad { ... };
```

也就是说它假设基树里**已经存在** `panel:` 和 `joypad:` 两个 label。而 mainline 的
`sun50i-h700-anbernic-rg35xx-2024.dts` 里根本没有这两个节点（`grep -c` = 0），它们是被
ROCKNIX 的补丁加进去的：

- `/root/rg34xxsp-kernel/patches/0111-rg35xx-2024-use-panel-mipi-dpi-spi-driver.patch`
  只改一行 `compatible`，前提是第 ~202 行已有 `spi_lcd: spi { ... panel: panel@0 {`；
- `0140-rg35xx-2024-use-rocknix-joypad-driver.patch` 加 `joypad: rocknix-singleadc-joypad {`；
- `0126-20241018_macroalpha82_rg35xx_add_additional_hardware_support.patch` 很可能是
  真正把 `spi-gpio` + `panel@0` + `joypad` 整块塞进 dts 的那个。

我们的 `tools/build-kernel.sh` 只打 3 个通用补丁 + 31 个 H700 补丁，这两段 dts hunk 没落到树里，
所以 `make ARCH=arm64 dtbs` 直接报：

```
sun50i-h700-anbernic-rg34xx-sp.dts:29.1-7 Error: Label or path panel not found
sun50i-h700-anbernic-rg34xx-sp.dts:34.1-8 Error: Label or path joypad not found
FATAL ERROR: Syntax error parsing input tree
```

**要改成自编**（以后想做的时候）的路线，二选一：

1. 把 0126/0140/0111 三个补丁也打进内核树，让基 dts 长出 `panel`/`joypad` label，
   再编我们的覆盖文件 —— 补丁顺序要对，0126 必须在 0111 之前。
2. 直接把 `rocknix-rg34xx-sp-reference.dts` 收编成我们自己的板级 dts（它已经反编译出来了，
   phandle 都是数字，`dtc -I dts -O dtb` 能原样编回去），删掉覆盖式写法。

现在不做这两件事：M1 的目标是让机器先点亮，跑官方验证过的 dtb 风险最低。等能进 Android
再把 dts 收归自己维护。

## 怎么验证 dtb 与 Image 匹配

```bash
dtc -I dtb -O dts kernel/dtb/sun50i-h700-anbernic-rg34xx-sp.dtb | grep -E 'compatible = "anbernic|panel-mipi|rocknix-singleadc'
```

应当看到 `anbernic,rg34xx-sp`、`anbernic,rg34xx-sp-panel` + `panel-mipi-dpi-spi`、
`rocknix-singleadc-joypad` 三组 compatible。缺任何一组都会表现为：黑屏（面板）、
手柄全无反应（joypad）。