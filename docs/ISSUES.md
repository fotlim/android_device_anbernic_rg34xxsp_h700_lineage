# 问题登记册（ISSUES）

> **这份文档只做一件事：把「已经踩过的坑」和「还没填的坑」逐条登记，谁接手都能一眼看清。**
> 叙事过程、推理链、修法细节在 `docs/HANDOVER.md` 和 `rg34xxsp开发日志/2026-11-12-sd49上机与cpio-mode根因.md` 里；这里只保留**现象 → 证据 → 根因 → 处置 → 状态**。
>
> 最后更新：2026-11-18（sd96 后，启动链已通、图形后端卡住）

---

## 0. 怎么用这份表

### 状态图例

| 标记 | 含义 |
|---|---|
| ✅ 已解决 | 已修并在实机验证过，有对应提交/补丁 |
| 🟡 已定位待修 | 根因确定，修法明确，但还没动手或还没验证 |
| 🟠 已规避 | 有临时办法能绕过，不是正解，迟早要还 |
| ❓ 未查 | 只看到现象，还没查根因 |
| ⛔ 不做 | 明确放弃的路线，别再往这儿花时间 |

### 新增一条问题的写法

在对应分类的表末尾加一行，编号顺延（`I-启动-07` 这种），并在文末「变更记录」加一行。模板：

```markdown
| I-分类-编号 | 一句话现象 | boot id + 日志文件名 + 关键行 | 根因（不知道就写「未查」） | 改了什么/打算怎么改 | 状态 | 提交或补丁名 |
```

**规矩**：一条问题只写一条根因。如果一个现象有两个独立原因（例如 SF 崩溃 + composer vsync 崩溃），拆成两条。

---

## 1. 启动链（SystemUI / 开机完成）

| 编号 | 现象 | 证据 | 根因 | 处置 | 状态 | 关联 |
|---|---|---|---|---|---|---|
| I-启动-01 | 开机永远到不了 `sys.boot_completed`，user 0 卡在 `State: BOOTING`，桌面不出现 | `ad81325c`：`dumpsys user` = BOOTING；events 有 `boot_progress_ams_ready` 但无 `uc_finish_user_boot`/`boot_progress_enable_screen` | 镜像里**根本没有 SystemUI**（只在 `handheld_system_ext.mk:29`），无状态栏 → 无 decor window → `DisplayContent.shouldWaitForSystemDecorWindowsOnBoot()` 为真 → `WindowManagerService.performEnableScreen()` 提前 return → `ActivityManagerService.bootAnimationComplete()` 永不调用 → `finishBooting()` 在 `if (!mBootAnimationComplete)` 处 return → `boot_completed` 不置位 | `device.mk` 加 `SystemUI`+4 个包；快路线用 overlay 把 APK 塞进 `/system_ext/priv-app` | ✅ 已解决 | sd87～sd92，`01900a61` 首次 `boot_completed=1` |
| I-启动-02 | system_server 在 phase 600 连崩（每卡 6～10 次） | `da17cd8a`：`IllegalStateException: Unable to find a direct boot aware fused location provider`（`LocationManagerService.java:555`） | `FusedLocation` 不在 `PRODUCT_PACKAGES`，APK 不存在 | 同 I-启动-01 的快路线，把 `FusedLocation.apk` 一并发布进 `/system/priv-app` | ✅ 已解决 | sd88～sd90；`live-logcat-late` 里 fused location 命中降为 0 |
| I-启动-03 | first stage 读不到卡上 boot 分区，四个候选全部失败 | `da17cd8a`：`sysapp: could not mount the card boot partition from any candidate`，**且前面没有任何 `sysapp: mount` 行** | 这段代码跑在 ueventd coldboot **之前**，`/dev/block/by-name/boot`、`/dev/block/mmcblk0p1` 都还不存在 | 读 `/sys/class/block/<name>/dev` 取 `maj:min`，自己 `mknod("/dev/dsh_<name>")` 再挂 vfat | ✅ 已解决 | sd88；`b5c8841d` 节点造对（`179:1`） |
| I-启动-04 | 造出节点后 mount 仍失败：`EBUSY(16)` | `b5c8841d`：`sysapp: mount /dev/dsh_mmcblk0p1 -> /mnt/dshboot rc=-1 errno=16 (Device or resource busy)` | debug-init 的 kmsg 跟随者**有独立 mount namespace**，`umount2("/mnt", MNT_DETACH)` 只在本 namespace 生效，FAT superblock 被跟随者继续持有 → 块设备独占占用不释放 | 不再挂卡：debug-init 在 `/mnt` 还可见时把文件拷进 RAM rootfs 的 `/dshsysapp/` | ✅ 已解决（绕开） | sd89 |
| I-启动-05 | 文件明明拷进 rootfs 了，first stage 却报 `errno=2` | `2d61c94a`：boot log 有 `dshsysapp: staged …`，但发布日志是 `sysapp: /dshsysapp/SystemUI.apk missing (errno=2)` | 发布代码在 `SwitchRoot("/first_stage_ramdisk")` **之后**运行，`/` 已变成 system 镜像，原 ramdisk 只剩切换前打开的 `DIR*` | 改用 `openat(dfd, "dshsysapp/…", …)` / `faccessat`，与 `PublishRamdiskFstab`、vendor-overlay 的做法一致 | ✅ 已解决 | sd90 |
| I-启动-06 | APK 进系统后 PMS 直接拒绝，system_server 反复重启 | `2cc41b35`：`IllegalStateException: Signature\|privileged permissions not in privapp-permissions whitelist: {com.android.systemui …}` | 允许列表 `system_ext/etc/permissions/com.android.systemui.xml`（4,434 B / 57 条）在构建产物里有，但卡上 `system_ext` 镜像**比 SystemUI 早**，设备上没有 | 新增 `/system_ext/etc` overlay 发布该 XML；另加 `ro.control_privapp_permissions=log` 兜底（仍逐条打印缺失项，不用 `disable`） | ✅ 已解决 | sd91 |
| I-启动-07 | SystemUI 起来了但 `KeyguardService` inflate 崩，12 次 | `01900a61`：`InflateException: line #64 in layout/super_notification_shade: uid=10046 needs permission READ_CONTACTS to read lock_screen_owner_info_enabled` | `READ_CONTACTS` 是 **dangerous（运行时授权）**，不是 privileged，任何 allowlist 都救不了；新 /data 一个都没授过 | 快照服务（root/class core）循环 `pm grant` 5 个权限；授权持久化在 /data | 🟠 已规避 | sd92；正解是 `default-permissions` XML 或把包编进 system |
| I-启动-08 | bootanim 服务永不停止，`checkBootAnimationCompleteLocked()` 死等 | `9300494a`：`init.svc.bootanim = running`，SF 的 flips=0、frame-counter=0；`live-bootanim-run.txt` 曾 `exit=1` | `BootAnimation::readyToRun()` 失败使动画线程退出，但 `main()` 卡在 `joinThreadPool()`（`Threads.cpp:718` 只报线程创建成功），init 看不到退出 | 属性 `debug.sf.nobootanimation=1`（`BootAnimationUtil::bootAnimationDisabled()` 直接返回） | 🟠 已规避 | sd85；动画本身没修 |
| I-启动-09 | system_server 在 `ActivityManagerService.<init>` 卡死 60 s 被 Watchdog 杀掉 | 更早的卡：`Blocked in handler on main thread (main)` | `memtrack` HAL 缺失 → `Debug.getMemoryInfo` → `memtrack_proc_get` 阻塞 | 编了空的 `memtrack.default.so`（`patches/memtrack-stub`） | ✅ 已解决 | memtrack-stub |
| I-启动-10 | `WifiHandlerThread` fatal `failed to set system property` | `b30feba9`：`init: Control message: Could not find 'wpa_supplicant' for ctl.start` + `error code: 0x20` | 镜像里没有 `wpa_supplicant` 服务，却有人 `ctl.start` 它 | 发布占位 rc（`93-wpa-supplicant.rc`：`/system/bin/sleep 1000000`） | 🟠 已规避 | 真 WiFi 驱动另案（见 I-HAL-05） |
| I-启动-11 | Lineage 的 `ProfileManagerService` 在 phase 550 NPE | `a95db9e0` | `Context.getSystemService(WIFI_SERVICE)` 为 null（没有 wifi binder 持有者） | vendor 侧 stub binder（`patches/wifi-stub`）+ FEATURE_WIFI 声明 | 🟠 已规避 | sd77 |
| I-启动-12 | 内核 `map swap synchronize_rcu() failed (code 97)` / `BpfUtils: create PF_KEY socket failed` | `4f480d77` | 内核缺 `CONFIG_NET_KEY`、`CONFIG_NETFILTER_XT_MATCH_BPF` | `kernel/fragment.config` 补上 | ✅ 已解决 | sd83 |

> **注意**：I-启动-01～07 解决之后，`sys.boot_completed=1` **只出现过一次**（`01900a61`）。之后每卡都因图形崩溃（I-图形-01）把系统拖回去，所以"开机完成"目前不是稳定状态。

---

## 2. 图形（当前主战场）

| 编号 | 现象 | 证据 | 根因 | 处置 | 状态 | 关联 |
|---|---|---|---|---|---|---|
| I-图形-01 | 第一次要求 SF 合成图层，SF 连崩 8～9 次，屏幕全黑 | `35526bea`/`5789ea90`/`bbb58a26`：`Fatal signal 11 (SIGSEGV) code 2 (SEGV_ACCERR)`，`#01 … libGLESv2_swiftshader.so (sw::Renderer::executeTask(int)+324)` | **SwiftShader 采样不了 minigbm(dma-buf) 缓冲区**（详见 I-图形-02），而 SF 每层都要采样 | 待换图形后端（I-图形-03） | 🟡 已定位待修 | sd93～sd96 |
| I-图形-02 | 自建探针复现：往 gralloc 里渲染 OK，采样 gralloc 纹理坏 | `bbb58a26` `live-ahb-probe.txt`：step2 渲染进 720×480 AHB 读回 `00 ff 00 ff` ✅；step3 采样普通 GL 纹理 `ff 00 00 ff` ✅；**step4 采样 GPU 写过的 AHB 读回 `00 00 00 ff`（全黑）**；**step5 采样 CPU 写过的 AHB 在 `glFinish` 处 `exit=139`** | SwiftShader 的 Android EGL 按**模拟器/goldfish**那套"可 CPU 映射的 shadow 内存"实现：渲染时写自己的影子（所以读回对），采样 dma-buf 时影子是空的（全黑），CPU 动过内存后崩。没有属性开关能修 | 换后端（panfork-mesa） | 🟡 已定位待修 | sd96；§64 |
| I-图形-03 | `ro.hardware.egl=swiftshader` 是临时后端 | `live-sf-dump.txt`：`EGL implementation : 1.4 SwiftShader 4.1.0.7`、`GL_RENDERER = Google SwiftShader` | 项目从一开始就用 SwiftShader 顶住（`device.mk`/`vendor.prop` 注释写明），真路线是 panfork-mesa 吃 Mali-G31 | 待做：libdrm 2.4.99→≥2.4.109 + `panfrost_drm.h`；引入 mesa 23.0.0-devel（`android/` meson 交叉构建）；`ro.hardware.egl=mesa` | 🟡 已定位待修（未开工） | 需构建窗口 + 全卡重刷 |
| I-图形-04 | composer@2.1-service 的 vsync 线程空指针崩溃 | `d9d8722a`/`35526bea`：`#00 HwcHalImpl<…>::vsyncHook(void*, unsigned long, long)+4`，`fault addr 0x0`，`x0=0`，`Cause: null pointer dereference` | 厂商 HWC 用 `callbackData == NULL` 调 vsync 钩子。`external/drm_hwcomposer/drm/vsyncworker.cpp:188` 有 `if (callback)` 保护，说明是 composer 已注销后 HWC 又调了一次 —— **是 SF 先死的后果** | 先跟着 I-图形-01 一起解决；若 SF 稳定后仍出现，再查 HWC 注销时序 | 🟡 已定位（判定为后果） | — |
| I-图形-05 | 采样 GPU 写过的 minigbm 缓冲区读回**全黑** | `a1e0aab9` `live-ahb-probe.txt` step 4 = `00 00 00 ff` | 导入后的纹理只有 level 0，默认 `MIN_FILTER=NEAREST_MIPMAP_LINEAR` 使纹理**不完整**，GLES 规范规定不完整纹理采样为 `(0,0,0,1)` 且不报错 | sd98：在 `image_to_texture()` 里每次导入都设 `NEAREST` filter；`3897db49` 之后 step 4 读回 `00 ff 00 ff` 正确 | ✅ 已解决 | `1952d82` |
| I-图形-05b | 采样 **CPU 写过**的 minigbm 缓冲区仍读回全黑 | `d37cb627` `live-ahb-probe.txt`：step 5（`GPU_SAMPLED\|CPU_WRITE\|CPU_READ`）左右都 `00 00 00 ff`；step 7（纯 CPU usage）同样全黑；`AHardwareBuffer_unlock` 返回的 release fence `fd=-1` | CPU 写入未对 GPU 可见：unlock 没有产生 fence，也没有做 cache flush/invalidate（非一致缓存的 SoC 上，CPU 数据留在 cache 里，panfrost 读到 0） | 待修：让 minigbm 的 unlock 正确 flush 并返回 fence，或改用 GPU 写入路径 | 未修 | — |
| I-图形-06 | `/vendor/lib64` overlay 会隐藏 `/vendor/lib64/hw`，弄死 passthrough HAL | `6ccdec45`/`a1e0aab9`：health@2.1 `Abort message: Check failed: passthrough != nullptr / Cannot find passthrough implementation of health 2.1 HAL`（每 5 s，26 次）；`p11.txt` 的 ANR 栈 `memtrack_proc_get -> getRawServiceInternal -> ActivityManagerService.<init>`，system_server 每 ~75 s 重启（ps 里 pid 递增） | overlayfs 不支持「lower 层里有挂载点」：`/vendor/lib64/hw` 先被 overlay，再造 `/vendor/lib64` 的 overlay，于是 hw 的内容对新 overlay 不可见 | sd97d 不再 overlay `/vendor/lib64`，mesa 的 .so 全放 `/vendor/lib64/hw`，用 rc `export LIBGL_DRIVERS_PATH /vendor/lib64/hw` 指路 | 🟡 已修待验证 | `34d6cb9` |
| I-图形-07 | HWC 服务一启动就 exit 1，无任何 ALOG | 更早的卡：`hwcomposer.drm_minigbm.so` dlopen 后 1 ms 内退出 | SF 是 **composer@2.1 客户端**，镜像里只有 2.2 服务，且 2.2 的 rc 没有 `interface` 行 → 2.1 查询永远找不到 | 编 `android.hardware.graphics.composer@2.1-service` + rc 带 `interface android.hardware.graphics.composer@2.1::IComposer default`；2.2 rc 置空 | ✅ 已解决 | sd8x |
| I-图形-08 | `hwc-resource-manager: Failed to initialize any displays` | 更早的卡 | `hwc.drm.device` 指向 `card0`=panfrost（无 connector）；真实显示在 `card1`=sun4i-drm（DSI-1 + HDMI-A-1） | `prop.default` 加 `hwc.drm.device=/dev/dri/card1` | ✅ 已解决 | sd8x |
| I-图形-09 | gralloc/mapper/allocator 在纯 minigbm 下缺 health/memtrack 等 passthrough 实现 | 更早的卡 | 镜像只带了部分 HAL impl | vendor overlay 发布 9 个 .so / 可执行文件（gralloc.minigbm、mapper@4.0、allocator@4.0、health@2.1、memtrack 等） | ✅ 已解决 | `live-first-stage-publish` 里 9 条 copied |

---

## 3. 存储 / 加密 / 用户解锁

| 编号 | 现象 | 证据 | 根因 | 处置 | 状态 | 关联 |
|---|---|---|---|---|---|---|
| I-存储-01 | `resolve HOME` 返回 `No activity found`，`Stack #1 type=home … sz=0` | 多卡：`live-home-state.txt`、`live-window-dump.txt` | `RootWindowContainer.resolveHomeActivity()` 只对**已解锁**用户生效，而"已解锁"只看 `StorageManagerService.isUserKeyUnlocked()`（`:3355`）里的 `mLocalUnlockedUsers`，它只在 `unlockUserKey()` 成功时 append（`:3334`） | 设备 `ro.crypto.state=unsupported`、fstab `/data` 无加密标志；`init_user0` 无条件执行但 kmsg 无输出，`sys.user.0.ce_available` 不存在 | 🟠 需决策 | 见 I-存储-02 |
| I-存储-02 | CE 解锁没走通，user 0 长期 `State: BOOTING` | `live-user-state.txt`（含 `am unlock-user 0` 的 stderr）、`live-vold-log.txt` | 同上；CE 解锁链路在 vold 侧就断了。`am unlock-user` 需要 token 参数，探针当时写法有误（`IllegalArgumentException: Argument expected after "0"`），证据不完整 | 决策点：① 补软件 keymaster/gatekeeper 让 `/data` 真走 FBE；② 或让框架把该用户视为已解锁（改 vold/StorageManager 行为） | ❓ 未查清 | 待定；`01900a61` 之后 `boot_completed=1` 时 user 仍 BOOTING |
| I-存储-03 | kmsg：`init: Top-level directory needs encryption action, eg mkdir /data/vendor …` | 多卡 kmsg | 缺 `mkdir /data/<top>` 少写 `encryption=` 参数，init **拒绝**创建该目录 | 未处理（要等 I-存储-02 的方向定了再改） | ❓ 未查 | 与 I-存储-02 同族 |
| I-存储-04 | FAT 卡偶发"文件损坏/设备不存在" | `G:\Image` 报 `The file or directory is corrupted and unreadable`；另一次整盘掉线 `A device which does not exist was specified` | 写入缓存未落盘的瞬时读失败（`chkdsk /F` 报 no problems，重读即正常）；也曾发生 FAT 真损坏（`DEBUG` 目录消失、`FOUND.000` 恢复） | 规律：先 `chkdsk /F`，再重读；写卡后 `Write-VolumeCache` + 校验 sha256 再弹出 | 🟠 已知现象 | 每次写卡都执行 |
| I-存储-05 | 8 GiB 卡镜像尚未产出；GPT 有警告 | 早前 `mk-sdcard.sh` 相关讨论 | 卡是 32 GB，镜像按 8 GiB 规划的方案还没落地 | 未做（等图形后端定了一起重刷） | ❓ 未做 | §54.5 Option A |

---

## 4. HAL / 服务 / 平台

| 编号 | 现象 | 证据 | 根因 | 处置 | 状态 | 关联 |
|---|---|---|---|---|---|---|
| I-HAL-01 | vendor 侧 libbinder 连不上 framework servicemanager：`addService(wifi) failed: -129` | `EX_TRANSACTION_FAILED(-129)` | `Parcel.cpp:531`：`__ANDROID_VNDK__` 下 header 是 `VNDR`，否则 `SYST`；vendor 编译的 libbinder 与 framework servicemanager 握手失败 | 放弃 vendor binder stub 路线，改 host 侧 HIDL/占位服务 | ⛔ 不做 | 别再试 |
| I-HAL-02 | 一堆 HAL 不存在（gnss / thermal / weaver / vibrator / ir / oemlock / soundtrigger） | logcat 有 `OemLock HAL not present on device` 之类；`hwservicemanager` 列表缺项 | 镜像没带这些 HAL | 目前**不影响开机**，按需逐个补；oemlock 可回落到默认实现 | ❓ 未做 | 优先级低 |
| I-HAL-03 | `AudioSystem::listAudioProductStrategies error -19`、`setForceUse` 失败 | `live-logcat-late.txt` | audio policy 配置缺失/不匹配（`audio_policy_configuration.xml`） | 未处理；不影响开机 | ❓ 未做 | 优先级中 |
| I-HAL-04 | camera 服务不可用 | `CameraManagerGlobal: Camera service is unavailable` | 无 camera HAL（设备是否有摄像头未确认） | 未处理 | ❓ 未做 | 低 |
| I-HAL-05 | WiFi 没有真驱动，只有占位服务 | `init.svc.wpa_supplicant = running`（其实是 `sleep`）+ stub binder | H700 的 WiFi 模块驱动未接入 | 未做 | 🟠 已规避 | 见 I-启动-10/11 |

---

## 5. 构建 / 工具链 / 流程

| 编号 | 现象 | 证据 | 根因 | 处置 | 状态 | 关联 |
|---|---|---|---|---|---|---|
| I-工具-01 | `m … \| tail` 吞掉编译失败，装进去的是旧二进制 | 卡 `b30feba9` 发的是上一版 init.android | 管道退出码取 `tail` 的 | 所有构建脚本加 `set -o pipefail` | ✅ 已解决 | tools/*.sh |
| I-工具-02 | `ninja: unknown target 'system_extimage'` | 构建日志 | 目标名是 `systemextimage`（无下划线） | 记住名字即可 | ✅ 已解决 | — |
| I-工具-03 | `ccache: error: Failed to create directory /home/builder/.ccache/tmp: Permission denied` | 编译 init_first_stage 时 | soong 在 nsjail 里跑，命名空间内的 root 不等于宿主 root，写不了 `/home/builder`（`750 builder:builder`） | 构建时 `CCACHE_DIR=/tmp/ccache`（1777） | 🟠 已规避（每次要带） | 所有构建命令 |
| I-工具-04 | 补丁链"块在漂移"，`verify-patch-chain.sh` 假报警 / 甚至把别的补丁块删掉 | ① `anchors for kLiveSnapshotSh not found`；② `redefinition of 'MountCardBoot'`；③ `+7 lines at 488 / -7 at 502` | ① 按共享锚点截断，把夹在中间的 live-snapshot 块一起删了；② 用"下一个顶层定义"当边界会**自匹配**到块自己的函数；③ 新条目插在列表中间，增量跑和全链跑顺序不一致 | 块尾加 `// sd88-end` 哨兵；重新插入时**插回原位置**；新增 overlay 条目**必须放列表最后** | ✅ 已解决 | sd88/sd89/sd93 |
| I-工具-05 | `/system` 只读，system 二进制（需 `libandroid`）放不进去 | soong：`dependency "libandroid" of "dsh-glprobe" missing variant: image:vendor.30` | `AHardwareBuffer` 在 `libandroid`，本树无 vendor 变体 | 做成 **system 变体**，用 `/system/bin` overlay 从卡上发布（和 APK 同一条通道） | ✅ 已解决 | sd95；`bbb58a26` 验证 `sysapp: copied … /system/bin/dsh-glprobe2 … ok` |
| I-工具-06 | 探针自身 bug 浪费两轮 | ① step3 读回全黑；② step6 `linked=0` | ① `glUniform*` 写在 `glUseProgram` **之前**（只作用于当前 program）；② fragment 用了 vertex 未输出的 `varying` | 先 `glUseProgram` 再设 uniform；改用 `gl_FragCoord`；失败时打印 `glGetProgramInfoLog`/`glGetShaderInfoLog` | ✅ 已解决 | sd95/sd96 |
| I-工具-07 | 改 `hardware/libhardware/*/Android.bp` 触发 kati 重跑，单次 7～10 分钟 | `ckati` 占满一核 | soong 重新生成 makefile | 一次改完再编，别一处处试 | 🟠 已知 | — |
| I-工具-08 | PowerShell 写中文/多行文件被 ANSI 或数组展平搞坏 | `HANDOVER.md` 曾被压成一行；`.ps1` 里中文引号报错 | PS 5.1 默认 ANSI；`Set-Content -NoNewline` 对数组行为特殊 | 用 Python `io.open(..., encoding="utf-8", newline="\\n")` 写；或 `[System.IO.File]::WriteAllLines` + UTF8 无 BOM | ✅ 已解决 | — |
| I-工具-09 | initrd 里的新子目录文件在 first stage 报 `missing (dirfd=4 errno=2)` | `dd077509`：`vendor-overlay/lib64/egl/*.so`、`lib64/dri/*.so` 全 missing，而同层 `lib64/libglapi.so` 拷成功 | `tools/make-initrd.py` 的 `build()` 只写给定条目、**不生成父目录项**；内核解包器只在父目录已存在时才建文件。`lib64/hw` 之类能work是因为它们随内核内置 initramfs 已存在 | `build()` 自动补齐缺失祖先目录（`040755`，去重、按序写） | ✅ 已解决 | sd97 二卡；`live-first-stage-publish` 应无 `missing` |
| I-工具-10 | 工作区迁移后路径变了 | `迁移说明_20261118.md` §2.7 | 仓库从 `default-workspace\03_WSA与安卓\anbernic-rg34xxsp` 移到 `rg34xxsp开发日志\anbernic-rg34xxsp`；WSL `/root/repo` 已同步改指 | 一律走 `/root/repo`（软链）或新路径；`docs/` 内引用按新路径理解 | ✅ 已知 | — |

---

## 6. 流程/纪律类（不是 bug，但每次都要遵守）

| 编号 | 规则 | 为什么 |
|---|---|---|
| I-纪律-01 | 写卡流程：F 盘暂存 → Windows 复制 → `Write-VolumeCache` → 校验 sha256 → `Shell.Application` 弹出 | WSL drvfs 或资源管理器会占住卡导致弹出失败 |
| I-纪律-02 | 卡上 `DEBUG/` 只留**当前一卡**的 `ran/dmesg/kmsg`，其余先归档到 `F:\rg34xxsp\debug-archive-<id>\` 再删 | 曾累积 4 卡 1.5 MB 旧日志 |
| I-纪律-03 | 归档只保留关键节点（首次开机完成、首次崩溃、决定性证据各一份），旧的删掉 | F 盘工作区曾到 54 MB，清理后 33 MB |
| I-纪律-04 | 删除/移动前先打印绝对路径并校验前缀 | 防止误删 |
| I-纪律-05 | 不跑 `wsl --shutdown`；不动 `/root/avium`；构建 `-j4` 上限；WSL 忙时不抢 | 其他人/其他会话在用 WSL |
| I-纪律-06 | 临时脚本用完即删（`append-*.py`、`dsh_*.py`） | 曾遗留在 `Documents\deepseek-harness\` 根目录 |

---

## 7. 当前卡点与下一步（2026-11-18）

**当前状态（2026-11-18 晚）**：panfork-mesa 已能用（`GLES: Panfrost, Mali G31`，SF 不再崩溃），剩下两条：I-图形-05（采样 gralloc 读回全黑）与 I-图形-06（`/vendor/lib64` overlay 的回归，已改）。

开工清单（需要 WSL 构建窗口 + 一次全卡重刷）：

1. [ ] `external/libdrm` 2.4.99 → ≥2.4.109，补 `include/drm/panfrost_drm.h`
2. [ ] 引入 mesa（23.0.0-devel，`android/` meson 交叉构建）→ `libEGL_mesa.so` / `libGLESv2_mesa.so`
3. [ ] `vendor.prop`：`ro.hardware.egl=swiftshader` → `mesa`
4. [ ] 确认 panfrost 绑定：`/dev/dri/card0`=panfrost、`renderD128` 可采样 dma-buf
5. [ ] 用 `dsh-glprobe2` 复测 step4/5（应变成正确颜色且不崩）
6. [ ] 刷整卡后验证：`live-ps.txt` 出现 `com.android.systemui` → `sys.boot_completed=1` → 屏幕出画面
7. [ ] 顺带：I-存储-02 的 CE 解锁决策、I-图形-04 的 vsync 时序复查

---

## 8. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-18 | 建档。把 §54～§64 的发现整理成 I-启动/图形/存储/HAL/工具/纪律 六类共 30 条，标注状态与证据 |