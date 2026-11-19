# 修复清单（按使用场景排优先级）

> 目标设备：Anbernic RG34XX-SP（Allwinner H700 / Mali-G31 / 1 GiB / 720×480 / **无触摸屏** / 手柄）/ LineageOS 18.1。
> 排优先级的原则是"**普通用户拿起这台机器会想干嘛**"，而不是技术难度。
> 状态：✅已修 / 🟠绕过 / ❌未做。更新：2026-11-19（sd127，桌面+输入可用）。

---

## 使用场景 → 需要什么 → 现在怎样

| 场景（用户想做的） | 依赖 | 现状 | 缺口 |
|---|---|---|---|
| **A. 开机进桌面、开/切/退应用** | 启动链、SystemUI、桌面渲染、输入 | ✅基本可用 | 桌面走 CPU 渲染（GPU 工作绕过）；输入已用键位重映射 |
| **B. 玩模拟器 / 独立游戏** | GPU(GLES)、手柄、音频、性能 | ❌基本不可用 | **GPU 渲染会 fault**、无音频、1GB+软件渲染慢 |
| **C. 听音乐 / 看视频 / 看图** | 外置存储(sdcard)、音频、媒体解码 | ❌不可用 | /storage/emulated 挂不上、无音频 |
| **D. 联网（浏览器/更新）** | WiFi 驱动、WebView、设置 | ❌不可用 | WiFi 没驱动、浏览器(WebView)闪退 |
| **E. 系统设置 / 装应用 / 权限管理** | Settings App | ❌缺失 | 没编译 com.android.settings |
| **F. 待机 / 省电 / 充电** | 电源管理、suspend | 🟠绕过 | suspend 冻结失败，现靠 wakelock 常亮（费电） |
| **G. 外设（U盘/蓝牙/震动）** | USB-Host、BT、振动马达 | ❌未做 | musb 主机口可用但没整理；BT 未接；震动 moto |
| **H. 打磨（M键下拉、亮度/音量、截图）** | 小改 | ❌未做 | M键、亮度、screencap(走GPU会坏) |

---

## P0 —— 不修就不能"正常用"（下一步必须先啃）

### P0-1 GPU 渲染（panfrost js fault）——**最高优先，也最难**
- **现象/证据**：真实 UI 渲染时 `panfrost ... js fault, status=DATA_INVALID_FAULT/IMPRECISE_FAULT`（每 boot 几十~几百次）。SwiftShader 软渲染则在 `sw::Renderer::executeTask` 崩 SurfaceFlinger。
- **影响**：**几乎所有 App 与游戏都走硬件加速渲染**，现在只有我们的"CPU Canvas 桌面"能画。浏览器、图片、视频、游戏全部黑屏/闪退。**这是这台掌机"能不能真正用（尤其玩游戏）"的总闸。**
- **修复思路**：Mesa/panfrost 版本或内核 panfrost 驱动与 G31 不匹配，或 gralloc(minigbm) 给 GPU 的缓冲/命令流有误。方向：① 升级/对齐 Mesa 与内核 panfrost；② 核对 minigbm↔panfrost 的 buffer 导入(format/stride/modifier)；③ 作为兜底，评估"全局强制软件渲染(框架层)"——能画但慢。
- **进展（sd128）**：已试最便宜的"UAPI 对齐"——给 Mesa 20.3.4 的 `drm-uapi/panfrost_drm.h` 补上内核 7.1.2 要的 `jm_ctx_handle`/`pad`（40→48 字节）。**实测无效**（`js fault` 151→489），说明不是结构错位，而是 **Mesa 20.3.4 为 G31 生成的作业数据本身非法**（Mesa 太老）。真修需**升级 Mesa**。
- **有利条件**：本机 `/root/avium/external/mesa3d` 是 **Mesa 25.3.0-devel**（G31/panfrost 成熟），可作移植源；外网/apt 亦可取新版。但 20→25 跨度大（libdrm/soong/NIR 联动），需多轮、有编译风险。
- **工作量**：大（多轮，可能涉及重编 Mesa / 内核）。


### P0-2 模拟外置存储 /storage/emulated 挂不上（MediaProvider FUSE ANR）
- **现象/证据**：`ExternalStorageServiceImpl → Timeout executing service → ANR (循环)`；`vold: Mounting emulated fuse volume` 后会话超时。物理卡/ext4 都正常。
- **影响**：App 读写 sdcard、下载、截图、相册/音乐扫描全废。
- **修复思路**：常见根因是 `/dev/fuse` 节点缺失或 MediaProvider 首启初始化过慢。① 开机 early `mknod /dev/fuse c 10 229; mount fusectl`；② 先重启一次观察首启后是否自动恢复；③ 必要时给 MediaProvider 首启更多时间/预建媒体库。
- **工作量**：小-中（先试重启/补节点）。

---

## P1 —— 基本可用必需

### P1-1 音频（音频 HAL 缺失）
- **影响**：游戏/视频/音乐全静音。掌机没声音 = 半残。
- **思路**：写 H700 真实 audio_hw（tinyalsa + AXP/内置 codec），替换当前占位服务。
- **工作量**：中（需读 codec/路由、实现 HAL、验证输出与耳机检测）。

### P1-2 WiFi（RTL8821CS 驱动未接）
- **影响**：无法联网（浏览器、商店、更新、网络时间）。
- **思路**：接入 RTL8821CS SDIO 驱动（mmc1）+ 固件（仓库已有 rtw88/rtl_bt fw）；启用 wpa_supplicant。BT 同芯片可一并。
- **工作量**：中-大（内核驱动/firmware + HAL 联调）。

### P1-3 电源管理 / 待机（suspend 冻结失败）
- **影响**：现在靠 wakelock 永远常亮，**耗电快、不能合盖待机**。
- **思路**：查清 `Freezing user space processes aborted (N tasks refusing to freeze)` 的 D 状态任务（可能是某驱动/GPU 任务），修好后恢复真待机；合盖(LID)睡眠也要处理。
- **工作量**：中-大（内核级排查）。

---

## P2 —— 明显改善体验

### P2-1 Settings App（com.android.settings 缺失）
- **影响**：改设置、管应用、权限、显示/声音/网络设置都没入口；也是 SetupWizard 崩溃的直接原因（现靠 skip-suw 绕）。
- **思路**：把 Settings 纳入构建（Settings/SettingsLib 依赖），叠进 system_ext。装好后可去掉 skip-suw 的绕过。
- **工作量**：中（编译 + 依赖 + privapp 权限）。

### P2-2 浏览器 / WebView 闪退
- **影响**：不能上网浏览。
- **思路**：Jelly(Browser) 依赖 WebView，WebView 又是硬件加速渲染——随 P0-1(GPU) 一起多半能好；另确认 WebView 包是否被裁掉。
- **工作量**：跟随 GPU；小（若只是缺 WebView）。

### P2-3 性能（1 GiB RAM + 软件渲染偏慢）
- **影响**：卡顿、切换慢。
- **思路**：GPU 通了自然改善；否则调 zram/lmkd、裁剪后台。
- **工作量**：随 GPU。

---

## P3 —— 打磨 / 外设

- **P3-1 M 键 → 下拉状态栏**：root 守护 `cmd statusbar expand-notifications`（或用我们 launcher 接 BTN_MODE）。小。
- **P3-2 亮度/音量硬件键**：`gpio-keys-volume` 已有；亮度需映射 + 背光设备。小。
- **P3-3 截图**：`screencap` 走 GPU/PWL 现在会坏；GPU 好后可用，或改用 CPU 路径。小。
- **P3-4 USB-Host**（U盘/外接手柄）：musb 主机口，接 USB 存储/输入设备。中。
- **P3-5 蓝牙**：RTL8821CS BT（与 WiFi 同芯片）。中。
- **P3-6 震动马达**（PWM2 rumble）：映射到 Android Vibrator。小。

---

## 建议的推进顺序（结合"先硬件"）

1. **先重启一次**确认 P0-2（模拟存储）是不是首启慢导致，能自愈最好；不行我加 `/dev/fuse` 修复。
2. **P0-1 GPU**：这是决定"能不能玩/能不能跑普通 App"的总闸。要么小步试（先核对 minigbm↔panfrost buffer、试不同 Mesa），要么评估"全局软件渲染兜底"让普通 App 先能跑（慢）。
3. **P1-1 音频**、**P1-2 WiFi** 并行推进（硬件适配的重点）。
4. **P1-3 待机**、**P2 Settings** 随后。
5. 其余打磨按需。

> 注：P0-1(GPU) 一旦解决，会**连带**修好浏览器闪退、截图、以及让一切 App 从"CPU 软件渲染"升级到硬件加速（性能大跃升），所以它是投入产出比最高的"硬骨头"，只是技术最难。
