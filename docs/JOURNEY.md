# 开发历程（JOURNEY）— 把 Allwinner H700 掌机硬塞进 LineageOS 18.1

> 本文是这条移植线从 0 到"能开机进桌面"的完整历程与踩坑复盘。
> 细节证据在 `docs/ISSUES.md`（问题登记册）、`docs/HANDOVER.md`（逐 boot 交接）
> 以及开发日志里；这里给你一条能读懂的主线，和每个坑为什么会踩、怎么爬出来的。
>
> 硬件是 **Anbernic RG34XX-SP**：Allwinner H700（4×Cortex-A53 + Mali-G31 MP2），
> 1 GiB RAM，720×480 RGB 直连屏，**无触摸屏**，内置"H700 Gamepad"手柄。

---

## 0. 一句话现状（2026-11-19）

- ✅ 内核 + DTS 编译通过，`Image` 上电可跑
- ✅ Android 用户空间能启动：init → zygote → system_server → SurfaceFlinger → SystemUI → Launcher
- ✅ 图形真正出画面：Mesa 20.3.4 + Panfrost（Mali-G31），SurfaceFlinger 正常合成
- ✅ user 0 能解锁、`sys.boot_completed=1`
- 🟠 桌面（Launcher3）曾因"缺少 appwidget 系统服务"空指针崩溃循环，已用空保护修补，待验证
- ❌ `com.android.settings` 未编译（SetupWizard 的 WiFi 步因此崩溃，已临时绕过）
- ❌ WiFi/音频等 HAL 未实现（此前的 WiFi 只是占位 stub）
- ❌ 深睡（suspend）在无触摸时会进入失败重试循环，已用"常亮"临时规避

---

## 1. 起点：官方没有 Android BSP

Anbernic 只给 Linux（Ubuntu/ROCKNIX）固件。要做 Android，只能从 **mainline Linux + ROCKNIX 的 H700 支持**反推硬件事实，再逐项用真机校验（见 `docs/HARDWARE.md`）。

关键硬件事实（都是实测读出来的，不是猜的）：

| 项 | 值 |
|---|---|
| SoC | Allwinner H700，Mali-G31 MP2 |
| 屏幕 | 720×480 RGB/DPI 直连（**不是 MIPI-DSI**），3 线 SPI 初始化，dclk 26 MHz |
| 面板 | FOG FJ035FHD05（两个批次，DTB 二选一） |
| 输入 | H700 Gamepad `0019:484b:14df`，17 键 + 双摇杆（ADC）+ 震动 |
| 存储 | 系统在 eMMC（mmc0）；TF 卡 = mmc2 |
| 无线 | RTL8821CS（SDIO + UART BT），未接驱动 |
| 触摸 | **没有**（后面无数坑的根源之一） |

---

## 2. 内核与 DTS：只写 30 行覆盖

上游 mainline 已有 `rg35xx-2024 → plus → sp` 三层 DTS 继承，ROCKNIX 又叠了面板/手柄节点。我们**不手写整份板级文件**，只写 30 行 include 覆盖：`model/compatible`、`&panel`、`&joypad`。配合 ROCKNIX 的 31 个 H700 补丁（DE/TCON/HDMI/PWM/panel-mipi/joypad），Linux 7.1.2 下 `dtc` 通过。

内核配置要点：`CONFIG_INITRAMFS_SOURCE="/root/initramfs-embed"`（把 debug-init 打包进 Image）、PF_KEY、显示链路、quota。

---

## 3. Android 设备树与构建

- 经典 boot image（header v0），**不用 GKI/vendor_boot**：sunxi U-Boot 对经典格式支持最好。
- **GPT**（带分区名）→ ueventd 自动生成 `/dev/block/by-name/*`，fstab 稳。
- 动态分区 `super` = 4 GiB；`/data` 明文（`ro.crypto.state=unsupported`，无 TEE）。
- SELinux 先 permissive。

---

## 4. 启动链攻坚（最耗时的一大块）

### 4.1 U-Boot / 分区表
- 这个 U-Boot 只注册 DOS/MBR 解析器，纯 GPT 找不到可引导分区 → 黑屏。解决办法是**混合 MBR**：sector0 放一个 0x0C 可引导项（指向 FAT boot 分区，满足 U-Boot），LBA1 再放 0xEE 保护项（满足内核 `is_pmbr_valid()`），内核据此识别为 GPT_MBR_HYBRID 后仍解析真 GPT，分区名保住。

### 4.2 没有串口、没有 pstore → 自造 debug-init
板子不引 UART、USB 早期不枚举、pstore 没编。为此写了 `tools/debug-init`：一个静态 aarch64 的 `/init`，开机把 `/proc`、`/sys`、dmesg 快照到卡上，fork 一个跟随 `/dev/kmsg` 的子进程，然后 `exec` 真 Android init（`/init.android`）。**从此所有真机调试都靠"卡上的 DEBUG/ 证据"**。

### 4.3 外置 initrd 与 cpio 的坑
内核先解包内置 initramfs，再把 U-Boot 给的外置 initrd 解包进**同一个 rootfs**（同名覆盖）。所以能用外置 `initrd.img` 换掉单个 ramdisk 文件，而不用重编 Image。

- cpio 陷阱①：内核只为**父目录已存在**的条目建文件。`make-initrd.py` 因此会自动补祖先目录项。
- cpio 陷阱②（很晚才炸）：`newc_entry` 给**目录项**也 `| S_IFREG`，`0o040755|0o100000=0o140755`，类型位 `0o140000` 是 **S_IFSOCK** → 内核 `do_name()` 把它 `mknod` 成 socket 节点而不是 `mkdir`。以前只带 vendor-overlay/*（那些目录内置 initramfs 里已有，socket 盖不上所以没暴露）；sd111 第一次新增全新子树 `dshsysapp/` 时 socket 赢了 → 其下所有 APK `ENOTDIR` → SystemUI 没落地 → 退回控制台黑屏。修法：只有 mode 没类型位时才补 `S_IFREG`。

### 4.4 overlayfs 的坑
first stage 用 overlay 把 `vendor-overlay/` 里的文件发布到只读的 `/vendor`。坑：**若把 lower 层的某个挂载点本身也 overlay，它下面的子挂载会被隐藏** —— 早期把 `/vendor/lib64` 整个 overlay，结果 `/vendor/lib64/hw`（health/memtrack passthrough HAL）被藏掉，导致 HAL abort。改为发布到 `/vendor/lib64/hw` 等具体目录。

### 4.5 缺 SystemUI / FusedLocation
产品继承链没经过 `handheld_system*.mk`，少了 SystemUI、FusedLocation 等。缺 SystemUI 时没有状态栏 decor 窗口，`performEnableScreen()` 提前 return；缺 FusedLocation 时 phase 600 直接崩。做法：卡上带 APK，debug-init 拷进 RAM rootfs 的 `/dshsysapp/`，first stage 通过 `openat(dfd,...)` 发布进 `/system*_priv-app`（不能按路径——`SwitchRoot` 后原 ramdisk 路径没了）。

---

## 5. 图形：SwiftShader → Mesa/Panfrost → DRM master

- 先用 SwiftShader 软件渲染把"出画面"解锁（验证整条 SF 链）。
- 再上 **Mesa 20.3.4 + Panfrost**：Mali-G31 跑起来了（GLES2 "Mali G31 (Panfrost), OpenGL ES 2.0 Mesa 20.3.4"）。EGL 文件以 swiftshader 之名发布（`ro.hardware.egl=swiftshader` 在 vendor build.prop 里是 write-once）。
- **DRM master 之争**：内核把 master 给第一个打开者。allocator 进程先开卡拿到 master，composer 的 `drmSetMaster` 就 EBUSY、`drmModeAtomicCommit` 返回 -EACCES。解法：composer/allocator 的 rc 以 `user root` 跑，让 gralloc 在打开后 `drmDropMaster`，composer 再 `drmSetMaster`；而且真正开卡的是 allocator 进程里的 **mapper@4.0-impl.minigbm.so**，所以要重编那个 so（不是 gralloc.minigbm）。
- 多个 GL 探针（`patches/gl-probe`）证明 panfrost 的 EGL image / gralloc 导入 / MT 渲染都 OK，SF 合成 30 层、flips 增长。

---

## 6. 最硬的骨头：user 0 卡在 BOOTING、黑屏（sd108 → sd111b）

图形全绿之后仍然黑屏。逐层读 `frameworks/base` 源码后定位到**一条闭环**：

1. `PackageManagerService.updateFlags()`：调用方没显式给 direct-boot 位时，**用户没解锁就只匹配 `MATCH_DIRECT_BOOT_AWARE`**。`resolveHomeActivity()` 用 `STOCK_PM_FLAGS`（不带 direct-boot 位），于是锁屏态只剩 directBootAware 的 HOME 能解析。
2. 本产品**没编译 `com.android.settings`**，AOSP 用来在锁屏态兜底的 `Settings.FallbackHome` 不存在；Launcher3 非 directBootAware → 被过滤 → `resolveHomeActivity()=null`。
3. 没有 home → 没有 activity → `activityIdleInternal` 永不触发 → 而 `ActivityStackSupervisor.checkFinishBootingLocked()`（`finishBooting()` 的唯一入口）只在 idle 时跑 → `sys.boot_completed` 不置、`enableScreen` 不跑、user0 的 `finishUserBoot()` 不跑 → 永远 BOOTING。**全是同一个"缺一个能跑的 home"导致的。**

**修法**：自建一个极简 **directBootAware HOME**（`patches/fallback-home`，模块 `DshFallbackHome`）——锁屏态它作为唯一可解析的 home 起来、绘制→idle，瞬间打通 `finishBooting → sendBootCompleted → finishUserBoot`（`ro.crypto.state=unsupported` 下 `unlockUserKey` 直接 append，不用 vobd），解锁后再收到 `ACTION_USER_UNLOCKED` 把接力棒交给真正的 launcher。

> 期间还踩过：`am unlock-user 0` 语法要带 token（`0 ! !`），但 user 在 BOOTING 态时即便解锁也会被 `finishUserUnlocking` 拒（状态不是 RUNNING_LOCKED），所以只能靠 home 触发。

---

## 7. SetupWizard 挡路（缺 Settings）

进到系统 UI 后是 LineageOS SetupWizard。选完语言/时间点下一步就崩：
`WifiSetupActivity` 要拉起系统 Settings 的 WiFi 页（`android.net.wifi.SETUP_WIFI_NETWORK`），本产品无 `com.android.settings` → `ActivityNotFoundException`，而且向导后续的 location/指纹/锁屏/恢复各步也都依赖 Settings，逐个补 stub 是无底洞。
**临时办法**：`patches/patch-skip-suw.py` 开机后置 `device_provisioned/user_setup_complete=1`、`pm disable-user org.lineageos.setupwizard`、`am start HOME` 直接进桌面。治本仍是把 `com.android.settings` 编进来。

---

## 8. ADB：这块 USB 口走不通

想让电脑 `adb` 直连以摆脱"拔卡拷 DEBUG"。结果：`dmesg` 显示 USB 是 **musb host 模式**（`MUSB HDRC host driver`），USB-C 是**主机口**，插电脑根本不会枚举成设备；而把 `sys.usb.config=adb` 硬切过去反而把 USB 栈/冻结搅崩，触发 **PM suspend 死循环**。加上没有触摸屏、点不了"允许 USB 调试"授权框。结论：**ADB over USB 这条口不可行**（除非改 DTB 让 musb 走 peripheral，属内核工程）。此路已放弃，回到卡内日志。

> 附带教训：我曾用 `Disable-PnpDevice` 去"重启"读卡器，结果把它禁用（问题码 22），导致"重新插卡也不认"，最后用 `Enable-PnpDevice` 才恢复。以后弹卡/动设备要更谨慎。

---

## 9. 无触摸 → 息屏 → 深睡死循环

桌面常亮后（`patches/patch-skip-suw.py` 里设了 `screen_off_timeout` 极大 + `stay_on` + `svc power stayon true`）不再假关机。但根因是：**没有触摸屏 ⇒ 永远没有 user activity ⇒ 60 秒必然息屏 ⇒ SystemSuspend HAL 进深睡 ⇒ 内核冻结 userspace 失败（`172 tasks refusing to freeze`）⇒ 每秒 abort 重试**，屏幕一黑一亮像关机。真正的修复是查清"为什么有任务拒绝冻结"（多半是某驱动/GPU 任务停在 D 状态），属于深水区；当前先用常亮规避。

---

## 10. Launcher3 崩溃：缺 appwidget 系统服务

桌面纯黑、只有电池图标，方向键没高亮但按 A 能打开音乐（说明输入在进、系统在跑，是应用层没画/在崩）。日志抓到：
```
FATAL EXCEPTION: launcher-loader
NullPointerException: AppWidgetManager.getInstalledProvidersForProfile(...) on a null object reference
  at WidgetManagerHelper.allWidgetsSteam(:156)
```
`live-services.txt` 里**没有 `appwidget` 服务** → `getSystemService(AppWidgetManager.class)` 为 null → Launcher3 每次枚举小组件就 NPE、崩溃循环。治本应在 system_server 补 appwidget 服务；先做了 `patches/fallback-home` 之外的一处 launcher 源码空保护（`allWidgetsSteam` 里 `awm==null → Stream.empty()`），重编 `TrebuchetQuickStep` 并经卡 overlay 落地。

---

## 11. 卡空间与损坏 → 256 MiB 启动分区整卡镜像

反复出现"DEBUG 缺失/坏卡/写不进 APK"，最后定位为结构性问题：**boot 分区只有 64 MiB**，Image(35.7M)+SystemUI(12M)+Launcher(7.9M)+initrd(5M)≈60M 已满，一写 live-* 日志就 FAT 写爆、目录损坏。
**修法**：`tools/mk-sdcard.sh` 新增 `--boot-mib`（默认 64），偏移改为按 BOOT_SIZE 递推；用 `--boot-mib 256` 生成 8 GiB 整卡镜像（boot 256M / misc 4M / meta 16M / super 4096M / userdata 3818M），并把 SystemUI/Launcher/各 APK 直接烘进镜像的 boot 分区。以后 DEBUG 有 ~190MB 余量，不再坏卡。

---

## 12. 当前已知问题（截至 2026-11-19）

详见 `docs/ISSUES.md`。摘要：

- 🟠 桌面 Launcher3 仍待验证（appwidget NPE 已打空保护）
- ❌ `com.android.settings` 未编译（SetupWizard 被临时跳过）
- ❌ 无触摸屏操作 → 正做 **摇杆当鼠标**（`patches/joymouse`，uinput 虚拟鼠标）
- ❌ WiFi/音频 HAL 未实现
- ❌ 深睡（suspend）失败循环未根治（先用常亮规避）
- 🟠 8 GiB 卡镜像、CE 解锁策略等后续项

---

## 13. 如何构建 / 烧录

见 `docs/BUILD.md`。要点：
1. 把 `device/anbernic/rg34xxsp` 放进 LineageOS 18.1 源码树；`lunch lineage_rg34xxsp-userdebug`；
2. `tools/build.sh` 出 system/vendor/super；
3. `tools/mk-sdcard.sh --boot-mib 256 ...` 生成整卡镜像，Rufus/Etcher **DD 模式**烧录；
4. 无串口，靠卡上 `DEBUG/` 里的 `dmesg/kmsg/ran/live-*` 判读。

---

## 14. 里程碑

- M0 `adb shell` → M1 出画面（SurfaceFlinger）→ M2 进桌面（Launcher）→ **现在到 M2 附近**
- M3 可交互（摇杆当鼠标 / 补全 Settings）→ M4 基本可用（WiFi/音频/电源管理）→ M5 日常使用

---

## 15. 经验教训（Lessons learned）

1. **先拿到"证据通道"再做系统**：这块板没有串口/pstore，debug-init 的卡内日志救了整个项目；一定要先保证每轮都能拿到本 boot 的真实日志（后来还专门做了 live-snapshot 和 256M 卡分区）。
2. **读源码定位，不要靠猜**：黑屏那条闭环、direct-boot 过滤、finishBooting 唯一入口，全是逐行读 `frameworks/base` 才钉死的。
3. **一个根因可能伪装成很多症状**：黑屏 / 亮一下锁屏 / 没画面 / 假关机，背后其实就"缺一个 home"和"无触摸息屏"两件事。
4. **小心工具本身**：cpio 的目录项、overlayfs 的挂载点、Windows 的 PnP 禁用——工具/环境自己的坑会伪装成系统 bug。
5. **给调试留空间**：64M 启动分区是长期隐雷，早该换大分区。

---

> 这条线还在继续。愿后来者少踩几个坑。
