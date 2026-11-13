# 交接文档（2026-11-11 20:30）

给接手的同事。目标：**读完就知道现在卡在哪、已经排除过什么、下一步该动哪一行**，不用重走弯路。

上一份 `docs/HANDOVER.md`（2026-11-09）描述的是「只剩显示」的阶段，那部分已经全部解决。**以本文为准。**

---

## 0. 一句话现状

> # ★ 2026-11-13 12:3x：镜像没错，卡卡在 `/dev/block/by-name/super`
>
> **先更正上一轮的结论**：我说"super 分区全零 = 镜像没写进去"，**是误判**。
> 我自己的探针读错了偏移——liblp 跳过 `LP_PARTITION_RESERVED_BYTES(4096)` 才写 geometry，
> 偏移 0 本来就该是零。losetup 实测：magic `gDla` 在 **4096 / 8192**，
> `/sys/class/block/loop0p4/uevent` 里有 **`PARTNAME=super`**，五个 GPT 名字全对。**镜像是对的。**
>
> **真正的失败点**（连续 22 次开机都一样）：
> ```
> init: realpath failed: /dev/block/by-name/super: No such file or directory
> init: Failed to mount required partitions early ... → InitFatalReboot: signal 6
> ```
> 出处 `system/core/init/first_stage_mount.cpp:270`。`/dev/block/by-name/<name>` 由 init 自己的
> `BlockDeviceInitializer`（`devices.cpp:361`）依据内核 uevent 的 **`PARTNAME`** 创建。
> 所以只剩两种可能：**(1) 设备内核没发 `PARTNAME=super`；(2) uevent 来了但 symlink 没建出来。**
> **这与 super 分区内容无关**——realpath 发生在读 super 之前。
>
> **已做的**：`debug-init` 探针修好偏移并新增
> `===== block uevents (PARTNAME is what by-name uses) =====`，
> 新 initrd 已写入卡（FAT 可直更，**不需要重刷整卡**）：
> `G:\initrd.img` 1,243,665 B sha256 `881c82b7…`，`/init` BuildID `237c60d5…`。
>
> **下一步**：上机一次，把 `DEBUG\dmesg-*.txt` 里
> `super (mmcblk0p4) LP metadata`（看 `geometry @4096`）与 `block uevents`（看 `PARTNAME=super`）
> 两节发我，即可定位是内核侧还是 init 侧。
>
> **两条新纪律**
> * **镜像一律 `--total-gib 8`**（16 GiB 里 11.9 GiB 是空 userdata，白白把写盘时间翻倍）。
> * **构建脚本不要 `| tail`**：上一版 debug-init 编译失败被管道吞掉，脚本"成功"了却没产出新文件。

> ## ⚠ 落盘位置约定（2026-11-13 用户要求，必须遵守）
>
> **本会话产生的一切大文件/产物一律放 `F:\rg34xxsp\`，不要放别的盘。**
> 起因：整卡镜像 `rg34xxsp-sd.img`（16 GiB）被误放到 `E:\`（用户的**游戏盘**）。
> `E:` 是游戏盘，`C:` 系统盘只剩 ~95 GB，只有 `F:` 是留给这类产物的。
>
> 具体做法：
> * WSL 侧先把产物做在 `/root/`，再 `cp` 到 `/mnt/f/rg34xxsp/`（`F:` 在 WSL 里是 `/mnt/f`）；
> * `tools/mk-sdcard.sh --out` 的默认值在仓库内，**交付时统一拷到 `F:\rg34xxsp\`**；
> * 不要再往 `E:\`、`C:\` 根或仓库里塞镜像；仓库只放源码、脚本与文档。

> # ★ 2026-11-13 11:2x：`/system/etc` 修复已上卡；又发现缺字体（整卡镜像在生成）
>
> **进展**
> 1. **`/system/etc` 0700 的修复已编进 init 并上卡**（sd52）：
>    `mkdir -m 0755` + 挂载后立刻 `chmod 0755 /system/etc`。
>    `G:\initrd.img` = 1,243,537 B，sha256 `6eeecbf5…`，含 `/init` + `/init.android`。
>    **这份卡可以直接上机验**：只要 `/system/etc` 回到 0755，六类 HIDL 崩溃应一起消失。
> 2. **新发现：产品根本没有字体。** `installed-files.txt` 里 fonts 计数为 0，
>    `/system/fonts` 在 `system.img` 里**整个目录都不存在**，`/system/etc/fonts.xml` 也没有。
>    于是 zygote 死在 `java.io.FileNotFoundException: /system/etc/fonts.xml` →
>    `Error preloading android.graphics.Typeface`。
>    **修好 `/system/etc` 权限后这里只会从 EACCES 变成 ENOENT，仍然过不去。**
>
>    **根因**：字体/断词表/键盘表/WebView 都在 `build/make/target/product/handheld_system.mk`，
>    而它只被 `generic_no_telephony.mk` 和 `mainline_system.mk` 继承；
>    我们这条链（`core_64_bit → base → common_full_tablet_wifionly`）一次都没碰到它。
>
>    **修法**（已改 `lineage_rg34xxsp.mk`，注意 `inherit-product-if-exists` 实测无效，必须用 `inherit-product`）：
>    ```make
>    $(call inherit-product, frameworks/base/data/fonts/fonts.mk)
>    $(call inherit-product, external/noto-fonts/fonts.mk)
>    $(call inherit-product, external/roboto-fonts/fonts.mk)
>    $(call inherit-product, external/hyphenation-patterns/patterns.mk)
>    $(call inherit-product, frameworks/base/data/keyboards/keyboards.mk)
>    ```
>    效果已核实：`/system/fonts` **246 个文件 / 74 MB**、`/etc/fonts.xml` 33,537 B、
>    `system.img` 748→827 MB、`super.img` 943→1,023,117,616 B（system 分区 829,988,864 B）。
>
> **★★ 两个新增的工具/纪律（不遵守会静默失败）**
> * `tools/sync-device-tree.sh` —— 构建树里的 `/root/android/lineage/device/anbernic/rg34xxsp`
>   是**实体目录，不是指向本仓库的符号链接**。在这里改 `*.mk` 再 `m` 会得到
>   `ninja: no work to do`，镜像里什么都没有（`lcd_density` 那批属性就是这么丢的）。
>   同步后还要 `touch` 那几个 mk 才会让 kati 重生成规则。
> * **卡在 WSL 里不是块设备**（`lsblk` 只有虚拟盘，`G:` 是 drvfs/9p），
>   所以**无法直接 dd 到 super 分区**；字体这 74 MB 也塞不进 64 MiB 的 boot 分区，
>   只能走**整卡镜像 + Rufus 刷写**。
>
> 见开发日志 §14。

> # ★ 2026-11-12 22:5x：根因已确认，修复已写好但**未编译**
>
> **`/system/etc` 被我们自己的 overlay 兜底改成了 `drwx------`（0700 root:root）。**
> sd51 探针在 boot `888a17de` 上直接量到：
>
> ```
> drwxr-xr-x 14 root root 4096 2026-11-07 /system
> drwx------  1 root root   60 2026-11-12 14:34 /system/etc      ← 0700，mtime=本次开机
> -rw-r--r--  1 root root 2960 /system/etc/vintf/manifest.xml    ← 文件本身正常
> root read /system/etc/vintf/manifest.xml = yes ; root open … OK
> ```
>
> 非 root 进程**连目录都进不去**（缺 `o+x`），于是 hwservicemanager 读不到框架 VINTF 清单、
> media.codec 读不到 seccomp 策略、zygote 读不到字体配置；空清单再让每个 HIDL
> `getTransport()` 返回 EMPTY → `getService()` 得 null（keystore 的 CHECK）、
> `registerAsService()` 得 `UNKNOWN_ERROR`(-2147483648)（system-suspend / allocator / wificond）。
> **一个权限位解释了全部六类崩溃。** binder / namespace / 节点权限已全部排除
> （节点 `crw-rw-rw-`、binderfs 两个 ns 都挂着、`service list rc=0` 且 vold 已注册）。
>
> 凶手是 `patches/patch-public-libs-filter.py` 里 `fix-public-libs.sh` 的
> **最后兜底**：它把 overlay 挂在 `/system/etc` 自身上，而 overlay 挂载点的属性取自
> **upperdir**，那个 upperdir 是 `mkdir -p` 按 shell umask 建出来的，不是 0755。
>
> **已改（`3ce11ff`，未编译）**：`mkdir -m 0755` + 挂载后立刻 `chmod 0755 /system/etc`。
>
> **下次接手第一件事**（顺序别错）：
> 1. `ln -sfn '<checkout>' /root/repo`
> 2. 确认 `patch-public-libs-filter.py` 是**替换**已有块而不是「已打就跳过」；
>    若跳过，手工把新片段补进 `system/core/init/first_stage_init.cpp` 的 `kFixPublicLibsSh`
> 3. `REPO=/root/repo JOBS=8 bash /root/repo/tools/build-init-first-stage.sh`
> 4. **清 `G:\DEBUG\`**（必须，两次栽在这：上次 28 MB 把 boot 分区写满，卡上留了截断文件）
> 5. `REPO=/root/repo bash /root/repo/tools/install-initrd.sh /mnt/g`
> 6. 上机；只要 `/system/etc` 回到 `0755`，六类 HIDL 崩溃应一起消失
>
> 待办：uid 探针的输出通道（`/metadata/dsh` 对 keystore/system 不可写，它们 exit 1 无输出）；
> `fonts.xml` 在本 build 里根本不存在，权限修好后重查 zygote 真正缺的字体配置；
> 以及彻底不 overlay `/system/etc` 自身的替代方案。
>
> 完整证据与判读见
> [2026-11-12 开发日志 §12](../../../../../rg34xxsp开发日志/2026-11-12-sd49上机与cpio-mode根因.md)。
>
> ## ★★ 通用规则（2026-11-13 由先行项目 H700Android 交叉验证）
>
> **规则一：init `exec` 脚本里创建的文件/目录必须显式给模式。**
> `system/core/init/service.cpp:500` 是 `umask(077)`，**每个 init 服务（含 `exec`）都在这之下运行**，
> 所以 `mkdir`/`open(O_CREAT)` 的默认模式会被削成 0700/0600。我们的 `/system/etc` 0700 事故就是
> `mkdir -p /dev/dsh_upper` 造成的。复查范围：`fix-public-libs.sh`、`96-system-fix.rc`、
> `98-framework-logs.rc`、`99-mount-data.rc` 等**所有** exec 脚本。
> （原生 HTML 对照：H700Android 的 RC5 也因 PID1 继承 `0022` 把 `0770` 变 `0750`，Bootfix1 修法就是恢复 `umask 000`。）
>
> **规则二：显示面板绝不靠字符串猜分辨率。**
> H700Android 的 DisplayAuto Test2 因把 `lcd_type=old` 当作「640×480」判据，导致 720 实物屏
> 花屏重影、RG34XXSP 闪 Logo 后黑屏，**整包被否决**。他们的原文结论：
> 「不能用单改 `wm size` 或盲改 DTB 宽度代替面板/时序适配」。
> → **我们坚持显式 `v2-panel` DTB + 固定 720×480，不做运行时自动识别。**
>
> **规则三：任何功能都不许用内存大小做开关。**
> RG34XXSP 存在 2GB（首发）/1GB（现行）/512MB（错发批次）三种；我们实测 1 GiB 是**当前标准**，不是缺陷。
> H700Android 曾把摇杆模块写成「<1.5GiB 拒绝加载且不进 Android」，结果 1GB 机型直接被阻断，后来不得不改成可选。
>
> ## ★★ 从 H700Android 抄来的配置（已改进 `lineage_rg34xxsp.mk`，待重编生效）
>
> | 属性 | 值 | 理由 |
> |---|---|---|
> | `ro.sf.lcd_density` | `213` | SurfaceFlinger 明报 `must be defined as a build property`；同屏同值 |
> | `debug.hwui.renderer` | `opengl` | Mali-G31 MP2 + 1 GiB 上更稳 |
> | `debug.hwui.use_buffer_age` | `false` | 同上（他们实机结论） |
> | `debug.hwui.use_partial_updates` | `false` | 同上 |
>
> 已对齐无需改：`ro.config.low_ram=true`、`zramsize=75%`。
> 可考虑：`ro.lmk.use_psi=true`（我们现在走 `use_minfree_levels`）。
>
> ## ★★ 需要重新审视的 HAL 声明（他们用"少声明"绕开了我们的崩溃）
>
> - **health**：他们只声明 **2.0**；我们声明 **2.1**，而崩溃信息正是
>   `Cannot find passthrough implementation of health 2.1 HAL for instance default`。
>   → 要么补上真正的 2.1 实现，要么降到 2.0。
> - **gatekeeper**：他们直接 `PRODUCT_PACKAGES -= android.hardware.gatekeeper@1.0-impl`，
>   靠 gatekeeperd 的进程内软件回退（我们有 gatekeeper SIGABRT 记录）。→ 可以同样删掉。
> - `PRODUCT_ENFORCE_VINTF_MANIFEST_OVERRIDE := false` 只绕过**构建期**强制，
>   **不解决运行期 `getTransport() → EMPTY`**；真正要做的是让 `manifest.xml` 与实装一致。
>
> 详细对照表（含图形栈、分区、sepolicy 为何不适用）见
> [开发日志 §13](../../../../../rg34xxsp开发日志/2026-11-12-sd49上机与cpio-mode根因.md)。

> ### 【2026-11-12 17:5x 更新】boot `4afef271`
>
> **整条启动链第一次全通了**：内核 → external initrd（`stage 8: size=2106832`，`c_mode` 修复连续三轮成立）
> → 一阶段 → SELinux → 二阶段 → `post-fs-data` 走完 → `class_start hal/core/main/late_start` **四个全部
> `succeeded`** → SurfaceFlinger 起来了（`Using HWComposer service: 'default'`）→ `zygote` + `zygote_secondary`
> 起来并 `Accepting command socket connections`。**`vold` 也修好了**（v8 表合并生效：`keeping 249 platform
> lines` / `keeping 86 platform lines`，`No match for … in service_contexts` 整份 4.7 MB kmsg 里 **0 次**，
> `start vold … took 271ms and succeeded`）。全程 `up=396s`（6 分 36 秒）**无 panic、无重启**。
>
> **新的、也是唯一剩下的卡点：两个 service manager 都不可达。** 227 条 abort 去重只有 6 种，
> 归成两类：`Failed to get ServiceManager`（keystore ×37、audioserver ×32，`defaultServiceManager()` 返 null）
> 和 `-2147483648`（= `0x80000000` = `status_t UNKNOWN_ERROR`；system-suspend ×41、allocator ×39、
> wificond ×34，HIDL 注册事务失败）。于是 `class main` 的服务起来就死、被 init 反复重启，
> **`boot completed` 永远到不了**。
>
> **根因已查到内核源码层**：当前内核 `CONFIG_ANDROID_BINDERFS=y`，而
> `drivers/android/binder.c:7119` 是
> `if (!IS_ENABLED(CONFIG_ANDROID_BINDERFS) && strcmp(binder_devices_param, "") != 0) { … init_binder_device(…) }`
> —— **开了 BINDERFS，`CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"` 就整块被跳过**，
> 三个 misc 设备一个都不会注册（实测：`/proc/devices` 里是动态主设备号 `238 binder` 而不是 misc `10:58`，
> devtmpfs 的 `ls /dev` 里没有任何 binder 节点，`/proc/filesystems` 里有 `nodev binder`）。
> 而 `system/core/rootdir/init.rc:175-189` 是 **binderfs 版**（`mount binder binder /dev/binderfs` +
> 三条 `symlink`），命令没报失败、`/dev/binderfs` 确实挂上了（composer 打开过 `/dev/binderfs/vndbinder`），
> 但 `enter_default_mount_ns`（post-fs-data，26.5 s）之后的服务就拿不到它们了 ——
> servicemanager 24.39 s 启动（namespace 切换**之前**）活着，keystore 41 s 启动（**之后**）就 null。
>
> **两条修法（都要动 WSL，目前 WSL 被别的任务占着，先别跑）：**
> **A（推荐，最对味 A11）** 内核 `# CONFIG_ANDROID_BINDERFS is not set`，保留 `BINDER_DEVICES` → 重编 Image，
> 三个 misc 设备出现，`ueventd.rc:37-39` 的规则生效；
> **B（改动小）** 保留 binderfs，在 `first_stage_init.cpp` 的 patch 里改到 `on late-init` 之后
> 再 `mount` + `symlink` 一次，并把 `ls -l /dev /dev/binderfs` 打进 `/dev/kmsg` 当探针。
>
> 详细证据、判读表、以及两个待解疑点（`media.codec` 的 `mediacodec.policy` 明明存在却报读不到；
> hwservicemanager 读 VINTF 清单 `EACCES`）见
> [2026-11-12 开发日志 §7](../../../../../rg34xxsp开发日志/2026-11-12-sd49上机与cpio-mode根因.md)。
>
> ### 【2026-11-12 18:5x】sd50：先让设备自己回答，不再猜
>
> 这一版**不加功能、不改任何现有行为**，只加一个只读探针。新增 `patches/patch-devprobe.py`
> （已加进 `patches/apply.sh` 的循环），在 init 里发布
> `/metadata/dsh/devprobe.sh` + `/vendor/etc/init/95-devprobe.rc`，在 **post-fs-data**
> （即 `enter_default_mount_ns` 之后）和 **boot** 两个点各跑一次，全部输出进 `/dev/kmsg`
> （tag `devprobe[...]`），两个触发点都用 `exec_background`，卡住也不会堵 boot。探针只读：
>
> * `/dev/{binder,hwbinder,vndbinder}` 此刻是符号链接还是设备节点、指向哪、目标在不在；
> * `/dev/binderfs` 里有什么；`/dev` 在本进程与 pid 1 各自由哪个 mount 支撑；
> * 本进程 `: < /dev/binder` 能否打开；
> * `pidof servicemanager` 的 `State/Threads/SigBlk` 和它的 fd 表里有没有 binder
>   （即它到底是不是 context manager）；`boot` 那一轮再跑 `timeout 5 service list`。
>
> 顺手修掉两个坑：`tools/build-init-first-stage.sh` 的变量 `OUT` 会被 `lunch` 覆盖（导致
> `cp` 拷的是整个产品目录），已改名 `BIN`；`/mnt/g` 在 WSL 晚启动时**不是挂载点**（只是根目录下的
> 普通目录），新增的 `tools/install-initrd.sh` 会硬校验 `$CARD/extlinux/extlinux.conf`，
> 没有就拒绝写入并提示 `mount -t drvfs G: /mnt/g`。
>
> 已上卡：`init.android` 2,106,632 B（sha256 `1994966…`，devprobe marker ×19）、
> `G:\initrd.img` 927,354 B（sha256 `7401e42c…`）、`G:` 空闲 29.4 MB、`DEBUG\` 已清空。
> **判读表见开发日志 §8.4**，四条分支的处置和“假设指纹”见 **§9**；这一次同时会拷出
> 4afef271 那轮的 logcat（p01–p03），里面有 libbinder 自己打印的 `E binder:` 原文。
>
> **预案补丁（条件性，未启用）**：`patches/patch-binderfs-early.py` —— 只有当探针显示
> “default ns 里 `/dev/binderfs` 是空的 / `/dev/binder` 悬空 / `open /dev/binder` FAILED”
> （即假设 H1）时才用。它把 binderfs 的 mount 提前到一阶段、`SetupMountNamespaces()` 克隆挂载树
> **之前**，使所有 namespace 都继承；`init.rc` 那次 mount 会 EBUSY（无害）。**故意没有列进
> `patches/apply.sh`**，而且写的时候 WSL 被别的任务占着，**尚未编译验证**。
>
> 另：2026-11-12 18:58 本任务误把另一个项目的 WSL 当成僵尸 VM（跑了 `wsl --shutdown`、kill 了
> `vmwp`），之后 `ext4.vhdx` 一直被残留的 `vmmemWSL` 独占。**WSL 恢复前不要再动它**；
> 这不影响上机验证——sd50 的 initrd 已经在卡上。
>
> ### 【2026-11-12 20:4x】源码级纠正：不是"service manager 坏了"，是两件事
>
> 上面「两个 service manager 都不可达」的说法**已被源码推翻**，以后面的为准：
>
> * `frameworks/native/.../IServiceManager.cpp:91` 的 `defaultServiceManager()` 是
>   `while (sm == nullptr) { ALOGE; sleep(1); }` **死循环，永不返回 null**；
>   `ProcessState.cpp:411` 则是 `LOG_ALWAYS_FATAL_IF(mDriverFD < 0, "Binder driver ... could not
>   be opened.  Terminating.")` —— 这条 abort **从未出现**，所以系统 binder 一直是好的。
> * keystore 的 `Failed to get ServiceManager` 用的是 **HIDL** 的
>   `::android::hidl::manager::V1_2::IServiceManager::getService()`
>   （`keystore_main.cpp:48,105`）。而 `system/libhidl/transport/ServiceManagement.cpp:174` 里
>   `defaultServiceManager1_2()` **只有** `access("/dev/hwbinder", F_OK|R_OK|W_OK)` 失败才返回
>   null（且不打日志）。**⇒ P1 = `/dev/hwbinder` 对 keystore(1017)/audioserver(1041) 不可读写。**
> * `registerAsServiceInternal`（同文件 826 行）：`sm == nullptr` 返回 `INVALID_OPERATION`(-38)，
>   而 `-2147483648`(UNKNOWN_ERROR) 来自 **VINTF 清单检查**或 `addWithChain` 失败。
>   那三个服务拿到的是 `-2147483648` ⇒ 它们**已经能和 hwservicemanager 通信**。
>   **⇒ P2 = VINTF 清单**（`system.suspend`、`hidl.allocator` 的声明其实在
>   `/system/etc/vintf/manifest/` 的碎片里，主清单 `manifest.xml` 2960 B 只有 framework HAL）。
>
> sd50 v3 探针已上卡（`G:\initrd.img` 927,712 B，sha256 `496e9a83…`）：post-fs-data 留**原状快照**，
> `on boot` 先 `chmod 0666` 三个 binderfs 节点、再以 root / **keystore** / **system** 三种身份
> 各测一遍 access 与 VINTF 文件可读性。判读表见开发日志 §10.5。
>
> ### 【2026-11-12 22:0x】boot `8f4589fb`：binder 全线无罪，真凶是 `/system/etc` 不可读
>
> sd50 v3 的探针结果把整套 binder 假设**全部排除**：
>
> * 三个节点 `crw-rw-rw- 1 root root 238,{1,2,3}`（0666 已生效）；
> * `/dev/binderfs` 的 mount 同时出现在探针自己的和 pid 1 的 mountinfo 里（`shared:24`）；
> * root 下 `open /dev/binder|hwbinder|vndbinder` 全部 OK；
> * **`service list` 返回 rc=0，列出 6 个服务，包括 `vold`** —— AIDL servicemanager 完全健康；
> * hwservicemanager（uid 1000）自己也 `fd 6 -> /dev/binderfs/hwbinder`。
>
> 真正的原因在日志里被点名了（`p03`，这轮 logcat 终于不是旧数据）：
>
> ```
> E HidlServiceManagement: Service … must be in VINTF manifest in order to register/get.
> W hwservicemanager: Cannot fetch /system/etc/vintf/manifest.xml:
>                     Cannot read /system/etc/vintf/manifest.xml: Permission denied
> ```
>
> **非 root 进程读不到 `/system/etc` 下的文件**：VINTF 清单（hwservicemanager）、
> `seccomp_policy/mediacodec.policy`（media.codec）、`fonts.xml`（zygote）。而 **root 读同一个
> 子树完全正常**（`ctx: probe /system/etc/selinux/…: access=0(ok)`）。
> 一个 EACCES 解释全部六种崩溃：框架清单读不到 → `getTransport()` 全返回 EMPTY →
> `getService()` null（keystore 的 CHECK 就是这个）、`registerAsService()` 返回
> `UNKNOWN_ERROR`(-2147483648)（system-suspend / allocator / wificond）。
>
> `system.raw` 里这些文件是 `0644 root:root`、父目录 `0755`，按 DAC 不该被拒 ——
> 所以要么设备启动的 `super.img` 里 system 逻辑分区与 `system.img` 内容不同，要么是挂载/标签层，
> **只有设备能量**。sd51 因此改成：
>
> * 探针输出写 **`/metadata/dsh/probe.log`**（ext4，重启不丢），不再只依赖会丢行的 `/dev/kmsg`
>   （上一轮 35.5–36.0 s 因 audit 洪峰丢了 433 行，uid 探针整段消失）；
> * `debug-init.c` 的回传清单加 `dsh/probe.log` → 下次开机自动拷成 `DEBUG/meta-dsh_probe.log`；
> * **root / keystore(1017) / system(1000) 三种身份**，全部在 `post-fs-data` 触发（早于崩溃），
>   量 `ls -lL`/`readlink`/`[ -r ]`/实际 open，对象含三个 binder 节点、`/system`、`/system/etc`、
>   `/system/etc/vintf`、`manifest.xml`、碎片目录、`mediacodec.policy`、`fonts.xml`，
>   外加**完整 mountinfo**（上一版只过滤 `/dev`，覆盖层会被漏掉）与 SELinux 标签。
>
> **`/init` 也走 external initrd 了**：改 debug-init 不必重建内核内建 cpio，
> `tools/install-initrd.sh` 同时打包 `/init`(699,136 B) + `/init.android`(2,106,696 B)，
> 代价只有卡上 650 KiB。当前卡上 `initrd.img` = 1,243,232 B，sha256 `4a0274c4…`。
>
> ⚠️ **每次上机前必须清 `G:\DEBUG\`**：本轮它塞到 28.08 MB，boot 分区只剩 10,240 B，
> `cp initrd.img` 报 `Input/output error`，卡上留下 917,504 B 的截断文件。这是第二次栽在同一处。
>
> 下次读法：先看 `DEBUG/meta-dsh_probe.log` 是否存在；`ran-*.txt` 里
> `metadata receipts from the previous boot` 从 5 变 6 即说明新清单生效。详见开发日志 §11.7。
>
> 下面 §0 的原文是 **11-11 20:30 的历史记录**（当时卡在 vold，现已解决），保留以便追溯。

LineageOS 18.1 在 RG34XXSP 上已经**跑到 Android 第二阶段 init 的 `post-fs-data`**：SELinux 策略加载、`/data` 挂载、`/metadata` 挂载、apexd、ueventd、logd、servicemanager、hwservicemanager 全部正常，**卡在 `vold` 起不来**（`Unable to start VoldNativeService`，exit 1）→ `vdc checkpoint` 等 50 秒超时 → `post-fs-data` 永远走不完 → **到不了 `class_start hal`，SurfaceFlinger 和 zygote 从未启动**。

根因已定位到：**servicemanager 的 `service_contexts` 查不到 `vold`**（`No match for vold in service_contexts` × 489）。平台表内容与 libselinux 后端都已在主机上验证无误（见 §5），所以问题在「设备上 servicemanager 读表那一刻」。修复补丁（v8）已写好并编译通过，**只差重编内核 Image 落卡实测**——而内核构建树被 WSL 的 `/tmp` 清理掉了，需要先重建（见 §6.1）。

> **2026-11-11 晚补记：不需要重编内核了。** 已证明内核会把 external initrd 解到同一个 rootfs 并
> 覆盖同名文件，所以把 sd49 的 `init.android` 单独打成 `G:\initrd.img` + 在 `extlinux.conf` 加一行
> `initrd` 即可，**卡已经铺好，直接上机就能验 v8**（见 §6.0）。重编内核降级为备用路线。

---

## 1. 硬件与目标

| 项 | 值 |
|---|---|
| 设备 | Anbernic RG34XXSP |
| SoC | Allwinner H700（`sun50i-h616` 系，4×A53） |
| GPU | Mali-G31 MP2（panfrost，`renderD128`；**只有 render 节点，没有 modeset**） |
| 显示控制器 | sun4i-drm（`card0`，`DRM_GEM_DMA`，dumb buffer 是 CMA 连续内存） |
| 内存 | 1 GiB LPDDR3（选 Android 11 的原因） |
| 屏幕 | 720×480 3:2，SPI 初始化 + RGB 并行（PD0–PD27），**v2 面板** |
| 存储 | 系统在 TF 卡（`mmc0` = `/dev/mmcblk0`）；eMMC 是 `mmc1` |
| 目标 | `lunch lineage_rg34xxsp-userdebug`，设备树在 `device/anbernic/rg34xxsp` |

---

## 2. 仓库与工作树（注意：路径已变）

```
仓库      C:\Users\LinOcean\Documents\deepseek-harness\default-workspace\03_WSA与安卓\anbernic-rg34xxsp
Android   WSL /root/android/lineage            （AOSP 树，super.img 就在这里出）
内核树    WSL /root/rg34xxsp-kernel/linux      （Linux 7.1.2 + ROCKNIX 补丁；**/tmp/lk-rocknix 已不存在**）
initramfs WSL /root/initramfs-embed            （init = debug-init，init.android = 真 init，vendor-overlay/ = 图形模块）
产物      WSL /root/artifacts                  （Image.sdNN 与 debug-init.sdNN）
卡        G:\  （FAT，盘符会漂；Image / boot.scr / extlinux/ / DEBUG/）
```

- 仓库 HEAD **`19c5ad1`**（`init: carry the rebuilt graphics modules in and overlay them onto /vendor`），工作区干净。
- 内核树 `/root/rg34xxsp-kernel/linux` 是 **Linux 7.1.2，单 commit `03e2778d1`**，`.config` 是 **11-06 20:37 的旧版**（`# CONFIG_QFMT_V2 is not set`、`CONFIG_INITRAMFS_SOURCE=""`）——**不能直接拿来编**，见 §6.1。

---

## 3. 版本演进（sd31 → sd49，每一版解决什么）

| 版本 | 解决 / 发现 |
|---|---|
| sd21–sd31 | 显示（v2 面板 DTB + DE blender `route=layer->index`）；Android 一阶 init；内核编入 SELinux；`androidboot.boot_devices=soc/4020000.mmc` |
| sd32–sd33 | 一阶 init 在 SwitchRoot 后把 ramdisk 上的 `fstab.rg34xxsp` 发布到 `/vendor/etc/`（`/vendor` remount 被 EACCES 拒 → 改用 overlayfs，成功）；用 `/metadata/first-stage-publish.txt` 做「不会丢的收据」 |
| sd34–sd35 | 设备 rc 里**根本没有 `mount_all`** → 一阶 init 发布 `/vendor/etc/init/99-mount-data.rc`；`mount_all` 仍 EINVAL，因为内核缺 `CONFIG_QFMT_V2`/`CONFIG_QUOTA_TREE` → 补内核配置，**`/data` 挂上了**（`Quota mode: journalled`） |
| sd36–sd41 | debug-init 采集 `/data` 证据；**看门狗**：H616 走 sun6i-a31 fallback、缺 `0x16aa0000` 钥匙，驱动「停止」实际仍在倒数 → 补 match/key 并手写钥匙，设备从 28 秒复位变成能活 500 秒以上 |
| sd39–sd42 | DRM 把 panfrost 这个 render-only GPU 分到 `card0`，HWC 打开它 `SET_CLIENT_CAP` 返回 `-95` → 内核改成只有 `DRIVER_MODESET` 才拿 PRIMARY；另解绑 fbcon（`/sys/class/vtconsole/*/bind=0`） |
| sd43–sd44 | kmsg 跟随进程遇 `EPIPE` 后无法重开 `/dev/kmsg`（`/dev` 已卸）→ 用同一 fd 重试；logcat 落 `/data/local/tmp/dsh-android.log`，下次启动拷成 `p01…p12.txt` + `pmap.txt` |
| sd44–sd45 | zygote 崩溃真因 #1：**`/system/etc/public.libraries.txt` 在镜像里不存在** → 一阶 init 发布清单 + `/metadata/dsh/fix-public-libs.sh` 在 `on early-init` 装进 `/system/etc` |
| sd45–sd46 | 崩溃真因 #2：清单里有本产品没有的 `libwebviewchromium_plat_support.so` → nativeloader 视为致命 → 安装前按 `/system/lib[64]`、`/vendor/lib[64]` 过滤（sd46 后 zygote 的 WebView 崩溃归零） |
| sd47–sd48 | 图形栈真因：**minigbm 没有 sun4i 后端**（`grep panfrost external/minigbm` = 0，`cros_gralloc` 只扫 `renderD*` 而 sun4i 只有 `card0`），且 **`gralloc.minigbm` 没装进 `hw/`**（`Android.bp` 缺 `relative_install_path`、`device.mk` 没列）→ HWC 静默回退到 AOSP 空壳 `gralloc.default.so`（只操作 `/dev/fb0`）→ 写 `sun4i.c` 后端 + card 回退 + 装 `hw/` + sepolicy 标签 + 一阶 init 把三个模块 overlay 到 `/vendor` |
| sd48 实测 | 发布链**全部成功**（`gfx: copied … 3`、`/data` 挂上），但**卡在 `post-fs-data`**：`vold` 每 5 秒起一次、立刻 exit 1 |
| sd49（待上机） | 定位 `vold` 死因 → servicemanager `No match for vold` → 主机复现证明表与后端没问题 → v8 补丁把平台表镜像进 vendor 表并逐路径记录 `access/stat` 结果 |

---

## 4. 已经彻底解决、**不要重做**的部分

### 4.1 卡必须用 hybrid MBR（「黑屏 + 指示灯亮」的根因）
U-Boot 只注册了 `part_dos`（无 `part_efi`），纯 GPT 的保护性 MBR 让 `part list mmc 0 -bootable` 失败 → extlinux / boot.scr 从不被扫描。而纯 MBR 又会让内核没有 `PARTNAME` → 没有 `/dev/block/by-name/*` → fstab 全失效。
解法在 `tools/mk-sdcard.sh` 第 2b 节：`[1] 0x0C bootable @2048`（U-Boot 用，**必须第一项**）+ `[2] 0xEE @1`（内核识别为 hybrid 后继续解析 GPT）。

### 4.2 `androidboot.boot_devices` 必须是 `soc/4020000.mmc,4020000.mmc`
不是 `mmcblk0`。依据 `system/core/init/devices.cpp` 的 `FindPlatformDevice()`（`soc` 是 simple-bus，返回值去掉 sysfs 前缀 = `4020000.mmc`），且只有 `is_boot_device` 才建 `/dev/block/by-name/*`。写错 → `first_stage_init` 挂载失败 = `LOG(FATAL)` = 重启。

### 4.3 显示（全部解决）
- **面板是 v2**：`android-v2` DTB + `panels/anbernic,rg34xx-sp-v2-panel.panel`；v1 固件 = 黑屏/亮线。
- **`BLEND_ROUTE` 要的是层号（`layer->index`），不是通道号**。sd14（index，`0x1`）出密集亮线、sd15（channel，`0x6`）纯黑 → 反转定论。
- `boot.scr` 与 `extlinux.conf` 必须都用 v2；`extlinux.conf` **必须 LF**（CRLF 会让 U-Boot sysboot 失败而退回 boot.scr）。
- DE33 planes 驱动必须真编进去（`sun50i_planes.c` 是 ROCKNIX 子补丁新增文件，`git apply --reject` 会静默丢 hunk → 用 `tools/audit-patches.py` 查）。

### 4.4 看门狗（曾经坑了很多轮）
H616 匹配到 sun6i-a31 fallback，**缺 `0x16aa0000` 钥匙**，所以 `WDIOC_SETOPTIONS/DISABLECARD` 看起来成功、实际计数仍在降。修法：内核加 H616 的 match/key，并且 debug-init 启动时直接手写钥匙到 `0x16aa0000`、**不再 re-arm**。实测设备从 uptime≈28 s 复位变成能活 694–697 s。

### 4.5 `/data` 挂载链（sd35 起稳定）
三处缺一不可：
1. 一阶 init 在 `SwitchRoot` 之前把 ramdisk 上的 `fstab.rg34xxsp` 写到 `/vendor/etc/fstab.rg34xxsp`（`/vendor` 是 ro，`MS_REMOUNT` 被拒 EACCES(13)，**改用 overlayfs 挂 `/vendor/etc`** 后写入成功）；
2. 设备 rc（`init.rg34xxsp.rc`）里**从来没有 `mount_all`** → 一阶 init 额外发布 `/vendor/etc/init/99-mount-data.rc`（`on fs` + `mount_all /vendor/etc/fstab.rg34xxsp`）；
3. 内核必须有 `CONFIG_QUOTA=y`、`CONFIG_QUOTA_TREE=y`、`CONFIG_QFMT_V2=y`、`CONFIG_QUOTACTL=y`（fs_mgr 会 `tune2fs -Oquota` 永久打开 quota 特性，内核读不了就 EINVAL）。

### 4.6 `public.libraries.txt`（zygote 崩溃 #1）
镜像里**不存在** `/system/etc/public.libraries.txt`（这条产品继承链没有 `media_system.mk`）。一阶 init 发布清单到 `/metadata/dsh/`，`/vendor/etc/init/96-system-fix.rc` 的 `on early-init` 执行 `fix-public-libs.sh` 装进 `/system/etc`（直接 cp → remount rw → overlay `/system/etc` 三级兜底）。

### 4.7 图形栈（sd47–sd48 的四处修复）
- `external/minigbm/sun4i.c`：新增 `backend_sun4i`（`.name="sun4i-drm"`，`bo_create=drv_dumb_bo_create`、`bo_map=drv_dumb_bo_map`、`bo_import=drv_prime_bo_import`、`bo_destroy=drv_dumb_bo_destroy`），格式组合 `ARGB8888/XRGB8888/ABGR8888/XBGR8888/RGB565/BGR565` + `BO_USE_RENDER_MASK|BO_USE_SCANOUT`，纹理组 `R8/NV12/NV21/YVU420`。
- `cros_gralloc_driver::init()`：`node_patterns[] = {"%s/renderD%d", "%s/card%d"}` 两轮扫描（sun4i 没有 `DRIVER_RENDER`，`/dev/dri` 里只有 `card0`）。
- `Android.bp` 给 `gralloc.minigbm` 加 `relative_install_path: "hw"`；`device.mk` 加 `gralloc.minigbm`。
- `sepolicy/vendor/file_contexts` 补 `.minigbm`/`.software` 后缀四条规则。
- **为什么不写 panfrost 后端**：panfrost 是 render-only + shmem，`drm_gem_dma_prime_import_sg_table` 要求单段连续（`drm_prime_get_contiguous_size(sgt) < size` 就 EINVAL）；而 EGL 是 SwiftShader，渲染在 CPU，GPU 不在分配路径上。
- 三个模块（`gralloc.minigbm.so` 80,632 B、`mapper@4.0-impl.minigbm.so` 199,656 B、`allocator@4.0-service.minigbm` 77,608 B）随 initramfs 携带，一阶 init overlay 到 `/vendor/{lib64,bin}/hw`。**HWC 的 `.so` 不用换**（它只 include 头文件、运行时 `hw_get_module` 取 gralloc 模块）。

---

## 5. 当前阻塞：`vold` 起不来 → `post-fs-data` 卡死

### 5.1 现象（sd48 实测，`kmsg-107acea1.txt`）
```
14,1055 init: Userdata mounted using /vendor/etc/fstab.rg34xxsp result : 0
14,1056 Command 'mount_all /vendor/etc/fstab.rg34xxsp' action=fs took 445ms and succeeded
14,1061 starting service 'exec 3 (/system/bin/vdc checkpoint markBootAttempt)'
14,1166 Service 'exec 3' (pid 188) exited with status 22 waiting took 51.215000 seconds
14,1214 exec 5 (/system/bin/vdc checkpoint prepareCheckpoint)  → 同样 51.285 s 后 exit 22
14,2509 exec 7 (/system/bin/vdc --wait cryptfs init_user0)     → 一直挂着
```
- `ServiceManager: Waiting for service 'vold' on '/dev/binder'...` 共 **47 次**；`vold` 启动 **33 次**，每次 `exited with status 1`。
- `vdc` 的退出码 22 = `EINVAL`，正是 `system/vold/vdc.cpp` 的 `getServiceAggressive()` 等满 50 秒（5000×10 ms）拿不到 `vold` 后 `exit(EINVAL)`。
- 服务启动全集里**没有 surfaceflinger、没有 zygote**（只 Parsing 了 rc）→ 因为 `class_start hal` 在 `on boot`，而 `post-fs-data` 从未完成。

### 5.2 死因链
1. `vold` 起来后走 `VolumeManager::start()`（会 `unmountAll()` + `Devmapper::destroyAll()`，但只删 `vold:` 前缀的设备，**不会拆 system/vendor**）→ 返回 0 → 走到 `VoldNativeService::start()` → `BinderService<VoldNativeService>::publish()` 失败 → `main.cpp:117` 打 `Unable to start VoldNativeService` 并 `exit(1)`。
2. `publish()` 失败是因为 servicemanager 的 `canAdd` 拒绝了它：`frameworks/native/cmds/servicemanager/Access.cpp:141` 打 `SELinux: No match for vold in service_contexts.`（p01.txt 里 **489 次**；其它名字只有 1–5 次）。
3. `No match` 只可能来自 `selabel_lookup(handle, &tctx, name, SELABEL_CTX_ANDROID_SERVICE) != 0`。而 `plat_service_contexts` 最后一行是通配 `*  u:object_r:default_android_service:s0`，`service_lookup()` 对 `*` 也匹配 → **只要平台表被加载，任何名字都不可能 No match**。
4. → **servicemanager 拿到的只有 vendor 表**（`vendor_service_contexts` 379 B，只有 `vendor.lineage.power.IPower`，没有通配、没有 vold），说明 `access("/system/etc/selinux/plat_service_contexts", R_OK)` 在设备上失败了。

### 5.3 已经排除的（别再查）
| 假设 | 结论 |
|---|---|
| 平台表内容坏了 | **否**。system.img 里 18,876 B / 251 行，`238:vold  u:object_r:vold_service:s0`，LF、无 CR、xattr `u:object_r:service_contexts_file:s0` |
| libselinux 没编 android backend | **否**。`Android.bp` 的 `common_CFLAGS` 无 `-DNO_ANDROID_BACKEND`、srcs 含 `label_backends_android.c`；`.so` 导出 `selinux_android_service_context_handle`、静态库有 `label_backends_android.o` |
| 表 + 后端组合有问题 | **否**。主机侧把 `libselinux/src/*.c` 全编 + 真实表喂进去：`selabel_open` 非 NULL，`lookup("vold") → u:object_r:vold_service:s0`、`lookup("任意不存在名字") → default_android_service`（通配兜底） |
| `selabel_fini` / `selinux_raw_to_trans_context` 失败 | **否**。`-DDISABLE_SETRANS` 下是 `strdup` stub |
| servicemanager 链的是 vendor 版 handle | **否**。`readelf -sW` 的 UND 只有 `selinux_android_service_context_handle`；`vndservicemanager` 根本没装 |
| 表加载报错 | **否**。logcat 里 `Error getting service context handle`、`line N error`、`missing fields`、`Multiple different specifications` 全为 0 |
| `/selinux` 是真实目录 | **否**。镜像根目录没有 `selinux` 条目；它是 `load_policy.c:288` 在 `verify_selinuxmnt` 失败时 `mount(selinuxfs, "/selinux")` 出来的，avc 里的路径只是字符串 |
| vold 的域转换有问题 | **否**。avc 里 `comm="vold" … scontext=u:r:vold:s0`，域是对的 |
| 缺 `/system/bin/vold` | **否**。system.img 里有（1,034,248 B）；后期的 `cannot execv … ENOENT` 是 init 反复重启后的次生现象 |

### 5.4 待验证的（sd49 的 v8 补丁就是为此写的）
v8 会把**平台表合并进 vendor 表**（vendor 路径整段启动期可达），并在收据里逐个记录 8 条候选路径的 `access()` + `stat()` 结果：

```
ctx: probe /system/etc/selinux/plat_service_contexts: rc=… errno=… size=… mode=…
ctx: probe /vendor/etc/selinux/vendor_service_contexts: …
ctx: overlay /vendor/etc/selinux ok
ctx: /vendor/etc/selinux/vendor_service_contexts: keeping N platform lines
ctx: /vendor/etc/selinux/vendor_service_contexts: wrote M bytes (ok)
```
收据落在 `/metadata/first-stage-publish.txt`，下次启动被 debug-init 拷成 `G:\DEBUG\meta-first-stage-publish.txt`。

- 若 probe 显示 plat 路径 `rc=-1 errno=13`（EACCES）或 `errno=2`（ENOENT）→ 直接知道是**权限/路径**问题，按 errno 修。
- 若 probe 全部成功、vendor 表也写成功，但 `No match` 依旧 → 说明 servicemanager 读的不是这两个文件，转查它自己的 `selinux_status_open` / `getSehandle()` 缓存重建路径。

### 5.5 【2026-11-11 晚】`system.img` 根上真有一个 `/system` 目录（旧猜想已推翻）

一直有个矛盾：staging 的 `out/target/product/rg34xxsp/system/` 顶层**没有** `system` 条目，可设备上
`execv("/system/bin/init")` 又确实成功，于是怀疑构建时偷加了 `system -> /` 的 symlink。

**实测推翻**：把 `system.img`（Android sparse v1.0）纯 python 解成 raw ext4
（`/root/artifacts/system.raw`，744,665,088 B）后用 `debugfs` 看：

```
inode 53   40755  /system        <- 真实目录，Links 14，不是 symlink
inode 24  120644  /etc    -> /system/etc
inode 14  120644  /bin    -> /system/bin
inode 25  120750  /init   -> /system/bin/init
inode 22  120600  /default.prop -> system/etc/prop.default
```

`/system` 里是完整一套（`addon.d apex app bin build.prop etc framework lib lib64 media priv-app usr
xbin` + `product/vendor/system_ext` 三个 symlink）。→ **chroot 到 system 分区根之后 `/system/bin/init`
天然存在**，不需要任何 `system -> /` 魔法。

**顺带的推论（对 v8 有利）**：`/etc/selinux/plat_service_contexts`（inode 834）与
`/system/etc/selinux/plat_service_contexts` 是**同一个 inode**，18,876 B、`-rw-r--r--`、含
`vold u:object_r:vold_service:s0`（:238）与通配 `* u:object_r:default_android_service:s0`（:251）。
所以 v8 里 plat 组第一条候选**应该** `access()` 成功。若上机后 probe 显示 `errno=0` 而 `No match`
依旧，方向就只剩「servicemanager 的 `/` 不是 system 分区根（mount namespace 不同）」或「拿 handle
的时机早于 publish」，别再回去查表内容和 libselinux 后端（§5.3 已经排除过）。

> 仍未解：sd48 日志里 servicemanager 打开的是 `/selinux/vendor_service_contexts`，这个路径**不在**
> libselinux 的任何候选里（候选只有 `/vendor/etc/selinux/vendor_service_contexts` 和
> `/vendor_service_contexts`）。`/selinux` 是 `policy.h:25` 的 `OLDSELINUXMNT`，只由 `load_policy.c`
> 在找不到 selinuxfs 时挂成 selinuxfs。留到上机看 probe 输出再判。

---

## 6. 下一步（按顺序做）

### 6.0 【2026-11-11 晚新增 / 11-12 修好】不编内核也能上 sd49：external initrd

> ⚠️ **11-12 教训（先读这段）**：这条路第一次上机时「看起来成功」——`dmesg` 有
> `Unpacking initramfs...` 和 `Freeing initrd memory: 904K`、没有 `Initramfs unpacking failed`，
> 但 `/init.android` 的 size 仍是 sd48 的 2,098,312。原因是 `make-initrd.py` 把 cpio 头的
> `c_mode` 写成了 `000755` 而**漏了类型位 `S_IFREG`**；`init/initramfs.c` 的 `do_name()` 只按
> `S_ISREG/S_ISDIR/S_ISBLK/S_ISCHR/S_ISFIFO/S_ISSOCK` 分派，四条分支全不匹配就静默
> `return 0`——archive 解包「成功」，一个字节也没落盘。**标准 cpio 的 c_mode 必须是
> `0100755` 这种带类型位的形式**（对比内建 ramdisk：`init` 条目就是 `mode=100755`）。
> `make-initrd.py` 现在会强制补 `S_IFREG`，并且 `verify()` 会对无类型位的条目直接报错退出。

**先看这一节，再考虑 6.1/6.2 的重编内核路线。** sd49 的 `init.android` 早就编好了
（`/root/initramfs-embed/init.android`，2,106,832 B，sha256 `004cffa1d4cf48eb809f282a9bea5bea28c941b6d607fac3443eb086ad253b30`），
缺的只是「把它送进卡上的 Image」。内核允许这么做，所以**不需要重编 Image**：

- `init/initramfs.c` 的 `do_populate_rootfs()` **先解内建 ramdisk、再把 external initrd 解到同一个
  rootfs**；`do_name()` 里是 `filp_open(..., O_WRONLY|O_CREAT|O_TRUNC, mode)` → 同名文件被**覆盖**。
  不是「替换整个 ramdisk」。
- external initrd 解包失败**只打一条日志、不 panic**，内建 ramdisk 完好 → 坏 initrd 也是安全降级。
- `.config` 有 `CONFIG_BLK_DEV_INITRD=y` + `CONFIG_RD_GZIP=y`、**没有** `CONFIG_INITRAMFS_FORCE`，
  所以 U-Boot 交来的 initrd 一定被采用；`wait_for_initramfs()` 在 `run_init_process()` 之前。
- 时序：`kernel_init_freeable()` → `wait_for_initramfs()`（`init/main.c:1707`）→ `kernel_init` 执行 `/init`。

> ⚠️ **`tools/mk-sdcard.sh` 里「external initrd 会 replace 内建 initrd」的注释（第 11-14、140-146、
> 578-580 行）是错的**，别被它挡住。`--embed-initramfs` 仍然更省事，但不是唯一可行路。

卡上已经铺好（2026-11-12 中午重打，可直接上机）：

```bash
# 生成（在 Windows 上跑，不碰 WSL）
python tools/make-initrd.py --out G:\initrd.img --gzip \
    --add 99_临时与截图/_tmp/sd49/init.android:init.android:0755
# 结果：926,934 B，sha256 15bee539c016234fd506202428da96390d1f3e77de13438adca6ac113409f891
# 条目 /init.android c_mode=100755 size=2,106,832（sha256 004cffa1…253b30）
# 旧版（11-11 夜）926,935 B / sha256 3d863e39…fb349 的 c_mode 是 000755，**已作废**
```

`G:\extlinux\extlinux.conf` 的 `android` 与 `android-v2` 两个 label 各加一行 `initrd /initrd.img`
（备份在 `G:\extlinux\extlinux.conf.bak-sd48`）。**`G:\Image` 没换，还是 sd48。** 下次开机就是
「sd48 内核 + sd49 init」。

判读第一件事：`G:\DEBUG\ran-<新boot>.txt` 里 `stage 8: init.android ... size=` 必须从
`2098312` 变成 **`2106832`** —— 这一步才算证明 initrd 真的落盘（只看 dmesg 的
`Unpacking initramfs...` / `Freeing initrd memory` 会被上面那个 mode 坑骗过去）。第二步看 publish 段
有没有 `ctx: probe ...` 行。详细判读表见
`rg34xxsp开发日志/2026-11-12-sd49上机与cpio-mode根因.md` §4。

**如果 initrd 路线也不通**，再回到 6.1/6.2 重编内核 —— 那条路仍然有效，只是要等 WSL 空出来。

### 6.1 先恢复内核构建树（原「当前唯一的硬阻塞」，现降级为备用路线）
`/tmp/lk-rocknix` 已被 WSL 清理，现存 `/root/rg34xxsp-kernel/linux` 的 `.config` 是 11-06 的旧版（缺 quota、`INITRAMFS_SOURCE=""`）。做法：

```bash
cd /root/rg34xxsp-kernel/linux
# 1) 打 ROCKNIX 补丁（/root/rg34xxsp-kernel/patches/*，0002 需要按 ^From [0-9a-f]{40} 切子补丁）
# 2) 配置：base.config + 仓库 kernel/fragment.config（后者已含 quota 四项）
#    cat base.config fragment.config > .config && make ARCH=arm64 olddefconfig
# 3) 打开内建 initramfs
#    scripts/config --file .config --set-str INITRAMFS_SOURCE "/root/initramfs-embed"
# 4) 断言（缺一即停）
#    CONFIG_QFMT_V2=y  CONFIG_QUOTA_TREE=y  CONFIG_QUOTACTL=y  CONFIG_QUOTA=y
#    CONFIG_DRM_PANFROST=y  CONFIG_DRM_SUN4I=y  CONFIG_INITRAMFS_SOURCE="/root/initramfs-embed"
# 5) 编
rm -f usr/initramfs_data.cpio usr/initramfs_data.o usr/built-in.a arch/arm64/boot/Image
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j4 Image
```

仓库里的 `tools/build-kernel.sh` 是这条流程的脚本化版本（含 3d/3e/3f 三节处理孤儿文件 + 断言），优先用它。

> ⚠️ **`build-kernel.sh` 第 509 行会 `--set-str INITRAMFS_SOURCE ''`**（它的设计是「内核 + DTB 从头编」，initramfs 交给别处）。**我们的启动镜像不是这样**：sd31 之后的 Image 全部内建 `/root/initramfs-embed`（`init`=debug-init、`init.android`=真 init、`vendor-overlay/`=图形模块）。照原样跑 `build-kernel.sh` 会编出一个**没有 initramfs** 的 Image，卡上一启动就是黑的。跑完必须补：
> ```bash
> ./scripts/config --file .config --set-str INITRAMFS_SOURCE "/root/initramfs-embed"
> make ARCH=arm64 olddefconfig
> rm -f usr/initramfs_data.cpio usr/initramfs_data.o usr/built-in.a arch/arm64/boot/Image
> make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j4 Image
> ```
> 验证：`ls -l arch/arm64/boot/Image` 应 ≈ 35.5 MB（35,551,240 B = sd48）；只有 32 MB 左右说明 initramfs 没进（sd46 及以前是 35,158,024 B，加图形模块后变大）。

### 6.2 编 sd49 Image
`/root/initramfs-embed/init.android` **已经换成 sd49 的 init**（2,106,832 B，sha256 `004cffa1d4cf48eb809f282a9bea5bea28c941b6d607fac3443eb086ad253b30`，含 v8 的 5 个标记串）。所以内核树一恢复，跑：

```bash
VERSION=sd49 tools/build-image.sh
```

它做四件事：断言 `.config`（`INITRAMFS_SOURCE` + quota 四项 + panfrost/sun4i + SELinux + binderfs）、清掉旧 initramfs 产物强制重打、编 `Image`、**断言 6 个标记串**（`stage 9: about to execl`、`wdt: wrote 0x16aa0000`、`console: framebuffer console unbound`、`pmap.txt`、`publish: `、`sun4i-drm`），最后落到 `/root/artifacts/Image.sd49` 与 `C:\Users\LinOcean\Downloads\Image.sd49`。
它还会拦一个坑：**Image 小于 34 MB 就报错**（说明 initramfs 没打进去——`tools/build-kernel.sh` 第 509 行会清空 `INITRAMFS_SOURCE`）。

然后写卡：`G:\Image` 用**Windows 侧** `Copy-Item` + `Write-VolumeCache` + sha256 核对（别用 WSL `/mnt/g` 直写）。

### 6.3 上机判读
| 观察 | 结论 | 下一步 |
|---|---|---|
| 出现 `ServiceManager: Waiting for service 'vold'` 之后 vold 不再 exit 1 | contexts 修好了 | 看 `class_start hal` → `render_node_scan: using /dev/dri/card0` → `Gralloc4` → `drm_atomic_commit` |
| vold 仍 exit 1，`No match for vold` 仍在 | v8 没覆盖到 | 看 `meta-first-stage-publish.txt` 里 8 条 probe 的 errno |
| 出现 `ctx: probe … errno=13` | `/system` 的 SELinux/DAC 拒绝 | 改走「一阶 init 直接 chmod/chcon」或换表路径 |
| 出现 `class_start hal` 且 `render_node_scan: using /dev/dri/card0` | 图形链打通了 | 看 SurfaceFlinger 是否 abort、`drm_atomic_commit` 是否 >0 |

---

## 7. 工作流与坑（照做能省几小时）

### 7.1 编译（**注意并发**）
- **用户在另一个会话里编类原生时会明确说「不要过分使用 wsl」**，此时不要跑 `m`/`make`；只读源码 + 单文件 `gcc -fsyntax-only`。
- 必须编时用 `-j2`/`-j4`，别用 `-j12`（会抢满 12 核）。
- `source build/envsetup.sh` **不能配 `set -u`**（`envsetup.sh:375 ZSH_VERSION: unbound variable`）。
- **PowerShell 里不要内联带引号/管道的 bash**，一律写成 `_tmp/*.sh` 再 `wsl -u root -e bash /mnt/c/.../x.sh`。嵌套引号会被 PowerShell 拆坏（`unexpected EOF while looking for matching '`）。
- **日志看不清时先降噪，再判断**（sd55/sd56 的血泪）：`drm.debug=0x1f` 让一颗 7.1.2 内核给每个 ioctl 都打一行，单次开机 2917 行 DRM 垃圾把 init 自己的话挤出 printk 限速窗口 —— 前面三轮的 mremap/常量/zygote 猜测全是被噪声带偏的。正确顺序是 `drm.debug=0x0` + `log_buf_len=4M`（已在 `mk-sdcard.sh` 里），把 init 的叙事拿全再推断。

### 7.2 一阶 init 的补丁怎么重放
`system/core/init/first_stage_init.cpp` 现在 **989 行**，全部改动用一条命令重放：

```bash
bash patches/apply.sh /root/android/lineage
```

它先 `git apply patches/init-first-stage-publish-ramdisk-fstab.patch`（基础版），再按顺序跑 10 个**幂等**脚本：
`patch-mount-data-rc.py`、`patch-framework-logs-rc.py`、`patch-graphics-device-rc.py`、`patch-logcat-to-file.py`、
`patch-public-libraries.py`、`patch-public-libs-filter.py`、`patch-vendor-graphics.py`、`patch-selinux-contexts.py`、
`patch-devprobe.py`、`patch-service-rc.py`。

**已验证**：从 `git checkout -- init/first_stage_init.cpp`（337 行）开始跑 `apply.sh`，产出与 sd46 编译所用的文件**逐字节相同**（sha256 `448c99b3…`，1203 行代码）。改任何补丁后请重跑这条验证。

**坑**：patch 脚本若用 `re.sub()` 拼 C 字面量，替换串会吃掉反斜杠 → 生成带真换行的字符串字面量 → `-Winvalid-pp-token` 编译失败。改用字符串切片重建整段 literal。

### 7.3 写卡
- 裸写 `\\.\PhysicalDriveN` **彻底不可用**（+1 扇区位移、每 64 KiB 首扇区丢失、且一律返回成功）。整盘写只能 Rufus（DD 模式）。
- 日常换内核只走**已挂载 FAT 的文件级** `Copy-Item` + `Write-VolumeCache` + sha256 核对。WSL 的 `/mnt/g` 与 Windows 的 `G:` 会不同步，写完必须从 Windows 侧核。
- 卡上文本用 UTF-8 **无 BOM**；`extlinux.conf` 必须 **LF**（CRLF 会让 U-Boot sysboot 失败）。
- **弹卡前先把 `/mnt/g` 卸掉**（`umount /mnt/g`）。WSL 的 drvfs 会一直占着这个卷，
  Windows 弹卡就报「该设备正在使用中」；凡是在脚本里 `mount -t drvfs 'G:' /mnt/g` 过，收尾必须卸。
  第二个常见拦路虎是**资源管理器窗口停在 `G:`**（拷完 `DEBUG\` 之后几乎必然），先把窗口关掉再弹。

### 7.4 读卡上日志
- 卡挂成 `G:`（盘符会漂）。读不出来先 `cmd /c "chkdsk G: /F"`。
- 日志里有转义序列，**必须 `grep -a`**。
- 每次启动的文件名带 boot_id：`ran-<id>.txt`、`kmsg-<id>.txt`、`dmesg-<id>.txt`；`pmap.txt` 说明 `p01…p12.txt` 分别来自哪个源文件。

### 7.5 git 提交
- 仓库无 local config → `git -c user.name=dsh -c user.email=dsh@local commit`。
- **commit message 写进文件再 `git commit -F`**（PowerShell here-string 会被拆成 pathspec）。

### 7.6 诊断证据要「不会丢」
- `printk.devkmsg=on` 已写进 bootargs（否则 init 的日志会被 printk ratelimit 吞掉——sd32 就这样白丢过一轮）。
- 关键结论走 `/metadata/<file>` + `fsync` + `sync()`（**只 fsync 不刷目录项**，sd46 曾因此丢掉整批 `meta-*`）。
- `pstore`/ramoops 虽然 bind 成功，但**从未抢救到过 panic 文本**，不要指望它。

---

## 8. 文件索引

| 路径 | 作用 |
|---|---|
| `tools/mk-sdcard.sh` | 出 TF 卡镜像（hybrid MBR + GPT + super + boot 分区 + boot.scr + extlinux.conf） |
| `tools/build-kernel.sh` | 打补丁 + 配置断言 + 编 Image（**注意它会清空 `INITRAMFS_SOURCE`**） |
| `tools/build-image.sh` | 把 `initramfs-embed` 打进 Image：断言 `.config` → 重打 initramfs → 编 → 断言标记串 → 落地 |
| `tools/debug-init/debug-init.c` | 包装真 init：心跳 / MMIO / 显示探针 / 看门狗控制 / 采集 `/metadata` 与 `/data` 证据 / `execl("/init.android")` |
| `tools/audit-patches.py` | 检查补丁新增行是否真进了树（`git apply --reject` 会静默丢 hunk） |
| `patches/apply.sh` | 一条命令重放全部 init / minigbm 改动（幂等） |
| `patches/minigbm/` | `sun4i.c`、`minigbm-graphics.patch`、`README.md`（为什么是 sun4i 不是 panfrost） |
| `patches/patch-*.py` | 一阶 init 的各项发布能力（fstab / mount_all / 日志 / 图形 / 公共库 / vendor 图形模块 / **selinux contexts**） |
| `kernel/fragment.config` | 内核配置增量（含 quota 四项；**必须以它为准**） |
| `device/anbernic/rg34xxsp/` | 设备树、`device.mk`、`BoardConfig.mk`、`vendor.prop`、`sepolicy/vendor/file_contexts`、`manifest.xml` |
| `docs/DISPLAY-THEORY.md` | 显示链路的已排除嫌疑表（历史资料，已解决） |

---

## 9. 还没做的（排在当前阻塞之后）

1. ~~v8 补丁收进 `patches/`~~ → **已完成**：`patches/patch-selinux-contexts.py`，并接进 `patches/apply.sh`（幂等，已用「从 337 行重放到 989 行逐字节相同」验证）。
2. `allocator@4.0-service.minigbm` / `gatekeeper@1.0-service.software` 的标签目前靠运行时 overlay，标签仍是 `vendor_file`、进程留在 `u:r:init:s0`（permissive 下不致命）。**要真正干净必须重编 vendor.img**（file_contexts 编进 `precompiled_sepolicy`，不能运行时改）。
3. `system_suspend`（`Unable to register suspend_control service: -1`）、`vendor.keymaster-4-0`（`Could not register service (-2147483648)`）同样在等 contexts 修复。
4. ~~`health 2.1` HAL：`Cannot find passthrough implementation`~~ → **已完成**：`android.hardware.health@2.1-impl` 既进了 `PRODUCT_PACKAGES`，也随 initrd 的 `/vendor-overlay/lib64/hw` 走 `kVendorGraphicsFiles` 发布，服务不再崩。
5. `logd: Cannot read /system/etc/event-log-tags`。
6. 1 GiB 内存优化：`PRODUCT_DISABLE_SCUDO` / `TARGET_VNDK_USE_CORE_VARIANT` / `PRODUCT_MINIMIZE_JAVA_DEBUG_INFO`。
7. 8 GiB 镜像写到 32 GB 卡会触发 GPT 警告（**不影响启动**）。
8. `panel_mipi_get_modes()` 每次都调 `drm_connector_set_panel_orientation()` → 属性泄漏 + 双 WARN。
9. 补打 `0155`（RGB connector 改 DSI）、`0162` 的 `pinctrl-sun50i-h616.c` 部分（消 56 条 `io_disabled` 报错，不影响功能）。
10. panfork-mesa 接 18.1（需 libdrm ≥ 2.4.109 + Bifrost；本机是 G31 MP2）——**当前用 SwiftShader，不急**。
11. **★ 内核缺 netfilter 一整套**（7.1.2 `.config`）：`NETFILTER_NETLINK`/`NFLOG`、
    `IP_NF_FILTER`（iptables `filter` 表）、`XFRM` netlink（family 6）、`xt_quota2`。
    证据：netd 启动即死
    `Unable to create NFLogListener: Status[code: 93, msg: "[Protocol not supported] : socket() failed"]`
    + `iptables-restore: unable to initialize table 'filter'`。
    **在补之前这台机器没有网络**（wifi/ril 都谈不上）。用户的绕法是
    `patches/patch-service-rc.py` 把 `netd.rc` 的两条 `onrestart restart zygote[_secondary]` 盖掉，
    让 netd 死得安静、不再连坐 zygote —— 那是保开机的权宜，不是修好网络。
12. `memtrack` 的 legacy HAL 模块 `hardware/libhardware/modules/memtrack` **这棵树里不存在**，
    所以 `android.hardware.memtrack@1.0-service` 永远 `exit 1`。服务已用 rc 覆盖停掉；
    干净的收尾是补一个 stub 模块，或者把 device manifest 里的 memtrack 声明删掉（免得 VINTF 说"声明了却没人注册"）。

---

## 10. 建议的接手顺序

1. 读 §5，理解「vold 起不来 → vdc 等 50 秒 → post-fs-data 卡死」这条链。
2. 按 §6.1 恢复内核树（**注意 quota 四项和 `INITRAMFS_SOURCE`**）。
3. 跑 `VERSION=sd49 tools/build-image.sh` 出 `Image.sd49`，写 `G:\Image`。
4. 上机，读 `G:\DEBUG\meta-first-stage-publish.txt` 里的 8 条 `ctx: probe` 结果，按 §6.3 的判读表分流。
5. contexts 通了之后，重点看 `render_node_scan: using /dev/dri/card0`、`Gralloc4`、`drm_atomic_commit` 三个标记——那是 sd48 已经铺好、但还没机会跑到的一段路。

---

## 11. 工作日志（按天）

### 11-09：显示 + Android 一阶 init
- 定死面板是 **v2**（`android-v2` DTB + v2 固件），`BLEND_ROUTE` 要**层号**不要通道号（sd14 亮线 / sd15 纯黑构成对照）。
- 卡改用 **hybrid MBR**（U-Boot 只认 dos，内核要 GPT 的 PARTNAME）。
- `androidboot.boot_devices=soc/4020000.mmc,4020000.mmc` 修好 `/dev/block/by-name/*`。
- 内核编入 SELinux、一阶 init 交棒前 `umount2(MNT_DETACH)` 四处 → 第一次看到 `init first stage started!`。
- 踩坑：`/dev/mem` 读 no-map ramoops → SIGBUS；`realpath()` 诊断让 PID 1 abort 并循环重启 → 改 readlink + FAULTCOUNT 限三次。

### 11-10：`/data`、看门狗、zygote、图形栈
- 上午（sd32–sd35）：一阶 init 发布 `fstab`（`/vendor` remount 被 EACCES 拒 → 改 overlayfs）；发现设备 rc 里**没有 `mount_all`** → 发布 `99-mount-data.rc`；`mount_all` 仍 EINVAL → 内核补 `QUOTA/QUOTA_TREE/QFMT_V2/QUOTACTL` → **`/data` 挂上**。
- 下午（sd36–sd41）：debug-init 采集 `/data` 证据；查明 **H616 看门狗缺 `0x16aa0000` 钥匙**（28 秒必复位）→ 补 match/key + 手写钥匙；DRM 把 panfrost 分到 `card0` 导致 HWC `SET_CLIENT_CAP` 返回 `-95` → 内核改成只有 `DRIVER_MODESET` 拿 PRIMARY。
- 晚上（sd42–sd46）：解绑 fbcon；kmsg 跟随进程 `EPIPE` 后无法重开 → 用同一 fd 重试；logcat 落 `/data/local/tmp`；**zygote 崩溃真因 #1 = 镜像里没有 `/system/etc/public.libraries.txt`** → 一阶 init 发布 + `early-init` 安装；**真因 #2 = 清单含本产品没有的 `libwebviewchromium_plat_support.so`** → 安装前过滤。sd46 后 WebView 崩溃归零。

### 11-11：图形栈真因 + contexts
- 上午–下午（sd47–sd48）：查明 **minigbm 没有 sun4i 后端**（且 `cros_gralloc` 只扫 `renderD*`，而 sun4i 只有 `card0`），且 **`gralloc.minigbm` 没装进 `hw/`** → HWC 静默回退 AOSP 空壳 `gralloc.default.so`（只操作 `/dev/fb0`）。写 `sun4i.c` + card 回退 + `hw/` 安装 + sepolicy 标签 + 一阶 init overlay 三个模块到 `/vendor`。sd48 实测：**发布链全部成功**，但卡在 `post-fs-data`。
- 晚上（sd49，未上机）：定位 `vold` 每 5 秒 exit 1 → `No match for vold in service_contexts` ×489 → 主机侧用真实表复现证明**表与 libselinux 后端都没问题**（`lookup("vold") → vold_service`、未知名字走 `*` 兜底）→ 结论：设备上 servicemanager 只拿到 vendor 表。写 v8 补丁（把平台表合并进 vendor 表 + 逐路径记录 `access/stat` errno），**编译通过**（`ramdisk/init` 2,106,832 B，sha256 `004cffa1…253b30`）。
- 收尾：`patches/apply.sh` 扩成一条命令重放 8 个幂等脚本，并验证「337 行 → 989 行」与 sd49 编译产物**逐字节相同**。
- **深夜（不占 WSL 的一轮）**：证明内核会把 external initrd 解进同一个 rootfs 并覆盖同名文件（`do_name()` 的 `O_TRUNC` + `do_populate_rootfs()` 两次 unpack）→ 于是**不用重编 Image**：`tools/make-initrd.py` 打了一个只含 `/init.android` 的 gzip initrd（`G:\initrd.img`，926,935 B，sha256 `3d863e39…fb349`），`G:\extlinux\extlinux.conf` 两个 android label 加 `initrd /initrd.img`，刷盘缓存并回读校验完成。**sd49 现在可以直接上机。**
- 同夜解开两个老疑问：① staging 顶层没有 `system` 不是 bug —— `system.img` 根上真有 `/system` 目录（inode 53），根上的 `/etc` `/bin` `/init` 是指向它的 symlink（见 §5.5）；② 因此 `/etc/selinux/plat_service_contexts`（inode 834，18,876 B，含 `vold` 行与 `*` 兜底）**确实存在且可读**，v8 的探针一上机就能给出答案。
- 发现 `tools/mk-sdcard.sh` 里「external initrd 会 replace 内建 initrd」的注释是错的（三处），已在 §6.0 标注。

### 11-12：sd49 首次上机 → 根因是 cpio 的 `c_mode`
- 两次启动（boot id `05638543` @4:10:02、`490c4f0b` @4:10:30）：`dmesg` 都有 `Unpacking initramfs...` +
  `Freeing initrd memory: 904K`（926,935 B 向下页对齐，正好对上），**但** `ran-*.txt` 的
  `stage 8: init.android xok=1 size=2098312` 两次都是 sd48 的大小，且 publish 段没有任何 `ctx: ...` 行。
- 定案：`make-initrd.py` 写出的 `c_mode=000755` 少了 `S_IFREG`；`do_name()` 的
  `S_ISREG/S_ISDIR/S_ISBLK/S_ISCHR/S_ISFIFO/S_ISSOCK` 全不匹配 → 静默 `return 0`。解包「成功」、零字节落盘。
  对比内建条目 `mode=100755` 一眼可见。
- 修：`parse_spec()` 补 `| stat.S_IFREG`，`verify()` 对无类型位条目直接 `SystemExit`。重打 initrd
  926,934 B / sha256 `15bee539…f891`，`/init.android` c_mode=100755、size=2,106,832，刷卡并回读校验。
- 顺带确认：`p01`–`p08` 与 `pmap.txt` 都是 `/olddata/...` 的**上一轮**数据（`pmap.txt` 自己写着映射表），
  `p01/p02` 里的 489/477 条 `No match for vold` 属于 11-11；今天这轮 logcat 要等下次启动才会被拷出。
- 顺带看到：`post-fs-data` 里 `vdc checkpoint` 超时 50 s 后 init **继续往下走**，`class_start hal` 之后
  `vendor.keymaster-4-0`(pid 379) 与 `system_suspend`(pid 373) 都以 signal 6 崩，
  `updatable process '...' exited 4 times before boot completed` —— 这些是 vold 修好之后才该碰的欠账。

---

## 12. 提交索引（最近 20 条，新→旧）

| commit | 内容 |
|---|---|
| `19c5ad1` | 一阶 init 携带重编的图形模块并 overlay 到 `/vendor`（sd48） |
| `4ff95bd` | minigbm 加 sun4i-drm 后端 + card 节点回退 + `hw/` 安装 + sepolicy 标签（sd47） |
| `3c039f6` | 只把本 build 真有的库交给 nativeloader（sd46） |
| `0087bff` | 补回镜像里从来没有的 `public.libraries.txt`（sd45） |
| `2f13542` | 问清 zygote 为什么 abort，并停止淹没日志（sd44） |
| `0ca3c8f` | 跟随进程存活 + 给 Android 日志一个文件（sd43） |
| `876859b` | 跟随进程存活 + 把面板交给 composer（sd43） |
| `dd70956` | 看门狗彻底停掉而不是 re-arm（sd41） |
| `093afc5` | 无 Android 的启动开关，用来观察谁先死（sd40） |
| `cf3b7f7` | composer 之前打开的是 GPU 的 card，所以没人能画（sd39） |
| `b5d215a` | 问 DRAM 是否熬过复位（sd38） |
| `cb4b4cd` | 给框架自己的日志一条离开机器的路（sd37） |
| `28ad5af` | 采集框架留在 `/data` 的东西（sd36） |
| `1ba917b` | 能读 fs_mgr 打开的 quota 的内核（sd35） |
| `9ad96ee` | 发布设备 rc 从来没有的 `mount_all`（sd34） |
| `eeb7be2` | 把收据留在日志行丢不掉的地方（sd33） |
| `624d56a` | 把 ramdisk 的 fstab 发布到第二阶段会找的地方（sd32） |
| `bbe3fd5` | 不让一次坏探针把机器无限重启（sd31） |
| `6605916` | 告诉 init 它真正在找的设备名（sd30） |
| `fbddeed` | 给 init 一个干净的 root 和 selinuxfs（sd29） |
