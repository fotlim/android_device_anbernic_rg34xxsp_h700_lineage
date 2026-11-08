# 显示不通的排查理论（截至 2026-11-08 晚）

目标：解释「内核显示链路全部 bind 成功、`/proc/fb` 有 `sun4i-drmdrmfb`、
往 `/dev/fb0` 涂纯色 6 次（红/绿/蓝/白/黑/棋盘，每次 hold 3 秒）屏幕**一个像素都不变**」。

## 一、已排除的嫌疑（有硬证据）

| # | 嫌疑 | 证据 | 结论 |
|---|------|------|------|
| 1 | fb0 参数不对 | `smem_len=1382400` = 720×480×4；`line_length=2880` = 720×4；`bpp=32`；`red{off=16} green{off=8} blue{off=0}` | 参数完全正确 |
| 2 | fb0 是影子缓冲（shadow buffer） | `sun4i_framebuffer.c:36` `.fb_create = drm_gem_fb_create` → `drm_gem_fb_funcs` **无 `.dirty`** → `drm_fbdev_dma_driver_fbdev_probe()` 走 `tail` 分支，`info->screen_buffer = map.vaddr`（CMA 真内存），`.fb_mmap = drm_fbdev_dma_fb_mmap` | **不是影子缓冲，写的就是真 framebuffer** |
| 3 | `smem_start=0x0` 是故障 | `drm_fb_helper.c:364` 因 `FBINFO_HIDE_SMEM_START` 跳过 `page_to_phys()`，属正常行为 | 不是故障 |
| 4 | 面板固件缺失/不同 | 我们 `Image` 与 ROCKNIX `KERNEL` 里 `rg34xx-sp-panel`(562B) 与 `-v2-panel`(826B) **逐字节相同** | 不是差异 |
| 5 | 配置差异 | 从 ROCKNIX 内核解出 IKCONFIG（`/tmp/rk.config`）逐项对比：`DRM_SUN4I/DRM_SUN8I_MIXER/DRM_SUN8I_TCON_TOP/DRM_SUN50I_PLANES/DRM_PANEL_MIPI/BACKLIGHT_PWM/PWM_SUN8I/FB/FRAMEBUFFER_CONSOLE/DRM_FBDEV_EMULATION/CMA_SIZE_MBYTES=32` **全部一致** | 不是差异 |
| 6 | dtb 不同 | 我们 v1 dtb 与 ROCKNIX rg34xx-sp dtb **sha256 完全相同**（`f7fe1d6e...`，50448 B） | 不是差异 |
| 7 | 补丁没打全 | `tools/audit-patches.py` 审计 34 个补丁：`broken: 3`，分别是 PWM Kconfig 帮助文本、USB OTG、`DRM_MODE_CONNECTOR_DSI` 命名，**全与像素无关** | 不是原因 |
| 8 | `-F3` fuzz 改坏了代码 | 从 base `03e2778d1` 干净应用子补丁 01/02/04 后与我们的提交**逐字节一致** | 不是原因 |
| 9 | `allwinner,sun50i-h616-tcon-lcd` 没进 of_match 表 | 该串从未作为 `.compatible` 表项存在；`0161` 只用 `of_device_is_compatible()` 判断。两边都靠第二串 `sun8i-r40-tcon-lcd` 匹配 `sun8i_r40_lcd_quirks` | 不是差异 |
| 10 | blender base 对 DE33 取错 | `sun8i_blender_base()` 对 DE33 返回 `DE2_BLD_BASE=0x1000`，ROCKNIX 代码**完全相同** | 不是差异 |
| 11 | spi-gpio 不支持三线 | `spi-gpio.c:368` `mode_bits = SPI_3WIRE \| SPI_3WIRE_HIZ \| ...` | 支持 |
| 12 | 面板 prepare 失败 | dmesg 无 `Failed to prepare the panel`；有 `firmware: ... loaded successfully` 与两条 Modeline | prepare 成功 |
| 13 | planes 驱动没 probe | `1100000.planes` 已绑定；无 `Unable to get mapping`；`devices_deferred` **完全为空** | probe 成功 |

## 二、仍然成立的三个候选根因

### 候选 A：`sun8i_mixer_commit()` 的 ROUTE 用了 `layer->index` 而不是 `layer->channel`

```c
/* sun8i_mixer.c:295 */
route |= layer->index << SUN8I_MIXER_BLEND_ROUTE_PIPE_SHIFT(zpos);
```

- 旧路径 `sun8i_layers_init()` 里 `index == channel`（两者都传 `i`），所以这一行在 DE2/DE3 上等价。
- 新路径 `sun50i_planes_setup()`（`sun50i_planes.c`）传的是
  `sun8i_ui_layer_init_one(drm, type, planes->regs, i, phy_ch, map->num_ch, &quirks->cfg)`
  → `layer->index = i`（0/1/2），`layer->channel = phy_ch`（0/6/7）。
- h616 quirks：`def_map[0] = { .map = {0, 6, 7}, .num_ch = 3 }`。
- 于是 mixer0 的 primary（UI，`index=1, channel=6`）在 ROUTE 里被写成 **channel 1**，
  而硬件期望的是 **channel 6**。UI 通道的数据因此进不了 blender 的 pipe。
- 症状吻合度：mixer 仍输出（时序正常、有背景色），但**永远不显示我们写的 framebuffer**。
- 需要确认：DE33 的 `BLEND_ROUTE` 字段语义是否就是「channel 号」。
  （ROCKNIX 用同一份 `sun8i_mixer.c`，所以若这是 bug，它也该中招——
   这提示要么 ROUTE 在 DE33 上语义不同，要么 ROCKNIX 实际跑的不是 DE33 路径。）

### 候选 B：`sun8i_mixer_init()` 的 `BLEND_MODE` 循环因为 `vi_num + ui_num == 0` 一次都不跑

```c
/* sun8i_mixer.c:464 */
plane_cnt = mixer->cfg->vi_num + mixer->cfg->ui_num;
for (i = 0; i < plane_cnt; i++)
	regmap_write(..., SUN8I_MIXER_BLEND_MODE(base, i), SUN8I_MIXER_BLEND_MODE_DEF);
```

- 我们的 `sun50i_h616_mixer0_cfg` 被 0002 子补丁 04 精简成只剩
  `.de_type = SUN8I_MIXER_DE33, .mod_rate = 600000000` → `vi_num = ui_num = 0`。
- 于是 `BLEND_MODE(base, 0..3)` 全部保持复位值，而不是 `0x03010301`。
- 后果：blender 的每级 pipe 混合模式未配置，输出可能不是「正常 alpha 混合」，
  而是未定义的原始扫描。

### 候选 C：DE33 分支不做寄存器复位

```c
if (mixer->cfg->de_type == SUN8I_MIXER_DE3) { ...清 DE3_MIXER_UNIT_SIZE... }
else if (mixer->cfg->de_type == SUN8I_MIXER_DE2) { ...清 DE2_MIXER_UNIT_SIZE... }
/* DE33 两段都不进 → mixer 寄存器保持上电默认值 */
```

- 上电默认值里有大量保留位/未定义位。若其中某位控制 pipe 使能或输入选择，
  就会表现为「时序对、但显示的不是我们给的数据」。

## 三、能一刀切分 A / B / C 的实验

sd12 已经内建了 `mmio_dump()`（读 `/dev/mem`），但被 SIGBUS 打断、输出没落盘
（FAT 目录项大小只在 `fsync`/`close` 时提交）。修法是给硬件探测阶段置
`durable = 1`（每行 `fsync`）。

修好后一次上机就能同时看到：

| 地址 | 期望值 | 若不符 → 指向 |
|------|--------|---------------|
| `0x01281080` BLEND_ROUTE | 主通道号（6 或 0x6000…） | 候选 A |
| `0x01281090` BLEND_MODE(0) | `0x03010301` | 候选 B |
| `0x011c1000` UI ch6 ATTR | 低位 = 1（EN） | plane 是否真被 enable |
| `0x011c1010` UI ch6 TOP_LADDR | fb 的物理地址（非 0） | mixer 扫的是不是我们那块内存 |
| `0x06511048` tcon0 BASIC0 | `0x01df02cf`（720×480） | 时序是否真写进去 |
| `0x0651001c` tcon-top PORT_SEL | `0x20` | 0003 hack 是否生效 |

期望值表已固化在 `tools/expect-regs.py`。

## 四、ROCKNIX 也不是「已知能亮」的参照系（重要修正）

早先我以为「ROCKNIX 在真机上能正常显示，所以拿它当基线」。**这个前提是错的。**

- ROCKNIX 的 `extlinux.conf` 写 `FDT /dtb.img`，但 boot 分区里**没有 `dtb.img`**
  （`mount -o loop,offset=$((32768*512))` 后确认只有 `KERNEL`/`SYSTEM`/`device_trees/`/`overlays/`）。
- U-Boot 里内嵌的唯一板级 FDT 在偏移 **624900**（35,784 B），
  `model = "Anbernic RG35XX 2024"`、`compatible = "anbernic,rg35xx-2024\0allwinner,sun50i-h700"`，
  且**完全没有显示节点**（panel/backlight/lcd-controller/display-engine/mixer/planes 全 0 命中）。
- 实测对照：把卡上 `Image` 换成 ROCKNIX 的 `KERNEL`（同一张卡、同一份 dtb、同一台机器），
  屏幕从「密集竖直亮线」变成「横条彩带」——**两个内核都在显示垃圾，谁也不是好的**。

因此：**不能再用「ROCKNIX 这样写所以没问题」来排除任何嫌疑**。
反过来，这也说明面板/背光/时钟这些硬件通路是通的（换内核画面会变），
问题在「送进面板的像素内容不对」这一层。

## 五、候选 D：面板初始化命令没真正生效

这是目前唯一还能同时解释下面三条观测的假说：

1. 显示链路全部 bind 成功、`/proc/fb` 有 fb0、fb0 参数全对；
2. 往 fb0 涂 6 种纯色，屏幕**一个像素都不变**（内容完全无关）；
3. 换内核后画面**会变**（说明面板在被驱动，只是收到的东西是垃圾）。

如果面板的 SPI 初始化序列（`panel_mipi_write_commands()`，v1 固件 434 B、
v2 固件 730 B，长度前缀流已逐条 walk 干净）没有真正被面板接受，
面板内部控制器的 RGB 输入通路就没配好，此时无论 mixer 输出什么，
面板都只会显示与信号电平相关的噪声 —— 正好是「密集竖直亮线 / 横条彩带」。

为什么这条没法用现有日志证伪：

- 三线 SPI 没有 MISO，`panel_mipi_read_display_id()` 必然读回 `0x000000`
  （`spi-gpio.c:76` 在 `SPI_3WIRE` 时 `getmiso()` 直接返回 0），
  所以 `MIPI Display ID: 000000` 既不能证明通、也不能证明不通。
- 没有任何 probe 失败、没有 `Failed to prepare the panel`、`devices_deferred` 为空。

## 五之二、DE2 总线 / SRAM 已排除

`bus@1000000` 的 `compatible = "allwinner,sun50i-h616-de33\0allwinner,sun50i-a64-de2"`，
而 `drivers/bus/sun50i-de2.c` 的 `of_match_table` **只有 `allwinner,sun50i-a64-de2`**
（第二串命中，能 bind）。它的 probe 做两件事：`sunxi_sram_claim()` 与
`of_platform_populate()`。

关键推理：**如果 `sunxi_sram_claim()` 失败，probe 会直接 return，`of_platform_populate()`
就不会执行，planes / mixer / tcon-top 这些子节点根本不会被创建**。
而实测 `1100000.planes`、`1280000.mixer`、`6510000.tcon-top` 全都 bind 成功了
（dmesg 有 `bound 1280000.mixer (ops sun8i_mixer_ops)` 等），
说明子节点确实被 populate 出来了 → **SRAM claim 成功、DE 总线正常**。

## 六、地址换算（复核过，避免踩坑）

`planes@100000` 与 `mixer@280000` 都挂在 `bus@1000000` 下，所以绝对地址要加 `0x1000000`：

| 逻辑名 | bus 内偏移 | 绝对地址 | 对应 sysfs 名 |
|---|---|---|---|
| planes | `0x100000` | `0x1100000` | `1100000.planes` |
| DE2 CCU | `0x8000` | `0x1008000` | `1008000.clock` |
| mixer0 | `0x280000` | `0x1280000` | `1280000.mixer` |
| mixer0 top | — | `0x1008100` | （mixer 的 `reg` 第 2 段） |
| tcon-top | `0x6510000` | `0x6510000` | `6510000.tcon-top` |

DE33 通道基址 = `planes_base + 0x1000 + channel * 0x20000`：

| channel | 绝对地址 |
|---|---|
| ch0 (VI) | `0x1101000` |
| ch6 (UI, primary) | `0x11C1000` |
| ch7 (UI, overlay) | `0x11E1000` |

## 七、下一步：一次上机能同时切分四个候选

`tools/debug-init/debug-init.c` 已升级（939 行，sha256 `bce4d5b8…`，编译产物
`/root/artifacts/debug-init.mmio2` 667,000 B，全静态），新增：

- `durable = 1`：硬件探测阶段每行 `fsync`，SIGBUS 之前的内容不会再被 FAT 目录项吞掉；
- `mmio_channels()`：遍历 ch0..ch9 打印 `attr/size/coord/pitch/laddr`，`p[0]&1` 标 `[ENABLED]`；
- `mmio_scan()`：在 planes / mixer0 窗口里逐字扫，打印所有落在 CMA 区间
  （`0x70000000..0x80000000`）的值 —— 用来确认 fb 的物理地址到底有没有写进任何通道寄存器；
- `mmio_dump()` 补上 `mixer0 top 0x1008100` / `mixer1 top 0x1008140`，并修正
  `pinctrl PD` 基址（`0x300b000 + 3*0x24 = 0x300b06c`，原先错写成 `+0x700`）。

一次上机读到的组合与结论对应关系：

| 观测 | 结论 |
|---|---|
| `ch6 ATTR` 低位=0（未 ENABLED） | 平面从未被 enable → 候选 C（bind 不清寄存器）/ commit 没跑到 |
| `ch6 TOP_LADDR` = 0 或不是 CMA 地址 | `update_buffer` 没执行或写错通道 → 候选 A |
| `mmio_scan` 在 planes 里扫不到 fb 地址 | 同上，且可定位到底写去了哪个偏移 |
| `ch6 ATTR`=1、`LADDR` 正确、`BLEND_ROUTE`=0x6000 类 | mixer 侧完全正确 → 嫌疑集中到候选 D（面板初始化） |
| `BLEND_ROUTE` 的 pipe1 字段是 1 而不是 6 | 候选 A 成立（`layer->index` vs `layer->channel`） |
| `BLEND_MODE(0)` ≠ `0x03010301` | 候选 B 成立（`vi_num+ui_num=0` 跳过初始化） |
| `tcon0 BASIC0` ≠ `0x01df02cf` | 时序没写进去（`sun4i_tcon0_mode_set_rgb` 没被调） |
| `tcon-top PORT_SEL` ≠ `0x20` | 0003 hack 没生效 / 被后续 `de_config` 覆盖 |
