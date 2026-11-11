# 交接文档（2026-11-11 20:30）

给接手的同事。目标：**读完就知道现在卡在哪、已经排除过什么、下一步该动哪一行**，不用重走弯路。

上一份 `docs/HANDOVER.md`（2026-11-09）描述的是「只剩显示」的阶段，那部分已经全部解决。**以本文为准。**

---

## 0. 一句话现状

LineageOS 18.1 在 RG34XXSP 上已经**跑到 Android 第二阶段 init 的 `post-fs-data`**：SELinux 策略加载、`/data` 挂载、`/metadata` 挂载、apexd、ueventd、logd、servicemanager、hwservicemanager 全部正常，**卡在 `vold` 起不来**（`Unable to start VoldNativeService`，exit 1）→ `vdc checkpoint` 等 50 秒超时 → `post-fs-data` 永远走不完 → **到不了 `class_start hal`，SurfaceFlinger 和 zygote 从未启动**。

根因已定位到：**servicemanager 的 `service_contexts` 查不到 `vold`**（`No match for vold in service_contexts` × 489）。平台表内容与 libselinux 后端都已在主机上验证无误（见 §5），所以问题在「设备上 servicemanager 读表那一刻」。修复补丁（v8）已写好并编译通过，**只差重编内核 Image 落卡实测**——而内核构建树被 WSL 的 `/tmp` 清理掉了，需要先重建（见 §6.1）。

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

---

## 6. 下一步（按顺序做）

### 6.1 先恢复内核构建树（**这是当前唯一的硬阻塞**）
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

### 7.2 一阶 init 的补丁怎么重放
`system/core/init/first_stage_init.cpp` 现在 **989 行**，全部改动用一条命令重放：

```bash
bash patches/apply.sh /root/android/lineage
```

它先 `git apply patches/init-first-stage-publish-ramdisk-fstab.patch`（基础版），再按顺序跑 8 个**幂等**脚本：
`patch-mount-data-rc.py`、`patch-framework-logs-rc.py`、`patch-graphics-device-rc.py`、`patch-logcat-to-file.py`、
`patch-public-libraries.py`、`patch-public-libs-filter.py`、`patch-vendor-graphics.py`、`patch-selinux-contexts.py`。

**已验证**：从 `git checkout -- init/first_stage_init.cpp`（337 行）开始跑 `apply.sh`，产出与 sd49 编译所用的文件**逐字节相同**（989 行）。改任何补丁后请重跑这条验证。

**坑**：patch 脚本若用 `re.sub()` 拼 C 字面量，替换串会吃掉反斜杠 → 生成带真换行的字符串字面量 → `-Winvalid-pp-token` 编译失败。改用字符串切片重建整段 literal。

### 7.3 写卡
- 裸写 `\\.\PhysicalDriveN` **彻底不可用**（+1 扇区位移、每 64 KiB 首扇区丢失、且一律返回成功）。整盘写只能 Rufus（DD 模式）。
- 日常换内核只走**已挂载 FAT 的文件级** `Copy-Item` + `Write-VolumeCache` + sha256 核对。WSL 的 `/mnt/g` 与 Windows 的 `G:` 会不同步，写完必须从 Windows 侧核。
- 卡上文本用 UTF-8 **无 BOM**；`extlinux.conf` 必须 **LF**（CRLF 会让 U-Boot sysboot 失败）。

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
4. `health 2.1` HAL：`Cannot find passthrough implementation of health 2.1 HAL`（服务反复 abort，不阻塞框架）。
5. `logd: Cannot read /system/etc/event-log-tags`。
6. 1 GiB 内存优化：`PRODUCT_DISABLE_SCUDO` / `TARGET_VNDK_USE_CORE_VARIANT` / `PRODUCT_MINIMIZE_JAVA_DEBUG_INFO`。
7. 8 GiB 镜像写到 32 GB 卡会触发 GPT 警告（**不影响启动**）。
8. `panel_mipi_get_modes()` 每次都调 `drm_connector_set_panel_orientation()` → 属性泄漏 + 双 WARN。
9. 补打 `0155`（RGB connector 改 DSI）、`0162` 的 `pinctrl-sun50i-h616.c` 部分（消 56 条 `io_disabled` 报错，不影响功能）。
10. panfork-mesa 接 18.1（需 libdrm ≥ 2.4.109 + Bifrost；本机是 G31 MP2）——**当前用 SwiftShader，不急**。

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
