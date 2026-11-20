# GPU / Mesa 升级移植笔记（GPU-PORT）
> 目标：把 Mesa 从 20.3.4 升到能正确驱动 Mali-G31 的版本，修复 `panfrost ... js fault DATA_INVALID_FAULT`。
> 状态：P0-1 GPU 的**构建工程勘察**（2026-11-20）。结论：可行但属"多天级"工程。

## 已确认的根因
- 内核 Linux 7.1.2 的 panfrost（driver 1.6.0，`select DRM_GEM_SHMEM_HELPER`，作业 uapi 含 out_sync/requirements/jm_ctx_handle）。
- 我们的 Mesa 仅 **20.3.4**（2020）。补 `jm_ctx_handle/pad`（sd128）**无效**（fault 151→489）→ 不是结构错位，是 **Mesa 为 G31 生成的作业数据本身非法**（G31/Bifrost 支持在旧 Mesa 不成熟）。
- 真修 = 升级 Mesa。

## 源与工具（本机现有）
- `/root/avium/external/mesa3d` = **Mesa 25.3.0-devel**，但 avium 是**小米 thor / Android 16(SDK36)**，构建是 Android15/16 新机制，**不能直接用**。
- 已下载：`F:\rg34xxsp\mesa-src\mesa-23.3.6.tar.xz`（archive.mesa3d.org，19MB）。解包在 `/root/mesa-src/mesa-23.3.6`。
- **重要**：mesa 22+ **没有 Android.bp/Android.mk**，改用 `android/` 目录的 **meson 交叉编译**（Waydroid/ChromeOS 那种 mesa-for-Android 预编译路线）→ 不是"换源码目录即可"，要自己用 meson 交叉编出 `gallium_dri.so` / `libEGL` / `libGLESv2`。
- 工具：meson 1.12 + ninja 有；AOSP clang `prebuilts/clang/host/linux-x86/clang-r353983c`（clang 9.0.3）+ lld 有；NDK r21 是**残缺老式** platforms 布局（无 clang toolchain、无 libc++、无 features.h）。

## 交叉编译进度（用 AOSP clang9 + NDK android-26 arm64 sysroot）
- 交叉文件 `/root/mesa-cross.txt`；wrappers `/root/ccwrap.sh`、`/root/cxxwrap.sh`：
  `clang --target=aarch64-linux-android26 --sysroot=<ndk android-26 arm64 usr> -fuse-ld=lld -rtlib=compiler-rt`
- ✅ C 编译器检测通过、能链（需 `-fuse-ld=lld` 强制 lld，`-rtlib=compiler-rt` 避免找 libgcc）。
- ❌ C++ 失败：libc++ 头找不到 bionic `features.h`；链接缺 `-lc++`。→ 需要**完整 NDK sysroot**（带 libc++ 与 features.h）。

## 还缺（构建 mesa android 平台所需）
1. **完整 Android NDK**（libc++/features.h/clang toolchain）——可 `dl.google.com` 下载，但代理约 220KB/s，~1GB 要 1+ 小时。
2. **Android 私有库**：libcutils / libsync / libhardware / libnativewindow / libui / liblog / libutils（mesa WSI 需要）——需从本树编或做桩。
3. **Android 版 libdrm**（mesa 23 需 libdrm ≥ ~2.4.109；本树 libdrm 2.4.99，需升级并按 Android 交叉编）。

## 两条可行路线（择一）
A. **meson-for-Android 交叉构建**：补齐 NDK/libc++/私有库/libdrm 后，`meson setup --cross-file ... -Dplatforms=android -Dgallium-drivers=panfrost -Dglx=disabled -Dgles2=enabled`，产出 `gallium_dri.so` 等，按现有 initrd 覆盖 `vendor-overlay/mesa_*.so`，写卡验证 `kmsg` 的 `panfrost js fault` 是否归零。
B. **换一个仍带 Android.bp(soong)、能编在 Android 11 上的中间版本 Mesa**（如 22.3/21.3）——但 archive 上这些版本已 404，需另找源；且 soong 兼容仍要试。

## 下一步（明天/续）
- 若继续 GPU：先取**完整 NDK**（优先）、再解决 libc++/私有库/libdrm，跑通 A 路线的 `meson setup` → `ninja`。
- 现实预期：多轮、1~2 天级。其间建议并行把 P1（音频/存储/WiFi）做掉，让设备先"好用"。

## 约束
- 只读 `/root/avium`（不要 build / 不要改）；写卡后**安全弹出**再拔；`-j4`。

---

# ✅ SUCCESS（2026-11-20）：mesa 23.3.6 交叉编成、GPU 不再 fault

**做法（脚本 `tools/build-mesa-android.sh`）**：
1. 下 `mesa-23.3.6.tar.xz`（archive.mesa3d.org）→ `/root/mesa-src/mesa-23.3.6`；下 `libdrm-2.4.120`（dri.freedesktop.org）→ `/root/libdrm-src`。
2. 下**完整 NDK r25c**（dl.google.com，经系统代理 `127.0.0.1:12147` 5.3MB/s）→ `/root/ndk/android-ndk-r25c`。
3. 交叉文件 `/root/ndk-aarch64.cross`：`aarch64-linux-android30-clang(++)` + llvm-ar/strip + NDK sysroot，host=aarch64/android。
4. **libdrm 2.4.120**（`-Dintel=disabled … -Dtests=false`）编好装 `/tmp/libdrm-install`（提供 `drmSyncobj*`，2.4.99 没有）。
5. **mesa**：`meson setup /tmp/mb-ndk --cross-file … -Dplatforms=android -Dandroid-stub=true -Dplatform-sdk-version=30 -Dgallium-drivers=panfrost -Dvulkan-drivers= -Dglx=disabled -Dgles1=disabled -Dgles2=enabled -Degl=enabled -Dbuild-tests=false -Dtools= -Dandroid-libbacktrace=disabled`，配 **target zlib.pc**（NDK 的 libz，否则链到宿主 x86 libz）→ `ninja` **1155/1155 成功**。
   - 产物：`src/egl/libEGL.so`、`src/mapi/es2api/libGLESv2.so`、`src/gallium/targets/dri/panfrost_dri.so`（78MB megadriver，strip 后 14.6MB）、`src/mapi/shared-glapi/libglapi.so`。

**部署（沿用 patch-mesa-publish 的路径，在 /vendor/lib64/hw + /vendor/lib64/egl）**：
- `mesa_egl.so`←libEGL.so → `/vendor/lib64/egl/libEGL_swiftshader.so`
- `mesa_glesv2.so`←libGLESv2.so → `/vendor/lib64/egl/libGLESv2_swiftshader.so`
- `mesa_panfrost_dri.so`←panfrost_dri.so(strip) → `/vendor/lib64/hw/panfrost_dri.so`
- `libglapi.so` → `/vendor/lib64/hw/libglapi.so`
- 额外（patch-mesa-publish 新增）：`libc++_shared.so`(NDK) + `libdrm.so`(2.4.120) → `/vendor/lib64/hw/`（panfrost_dri 的 DT_NEEDED）
- 环境仍 `LIBGL_DRIVERS_PATH=/vendor/lib64/hw`。

**结果（sd129 / boot 87ac2b9f）**：`panfrost js fault = 0`（此前几十~几百）、suspend=0、无 SystemUI FATAL；SystemUI 的 RenderThread 在 GPU 上跑。**GPU 渲染修好了。**

## 遗留（下一步）
1. **"内部问题"弹窗**：日志里唯一崩的是 MediaProvider `ExternalStorageServiceImpl` ANR（persistent 核心应用崩→弹框）。即 **P0-2 模拟存储 FUSE 问题**，不是 GPU。修 P0-2 即可去弹窗 + 让 sdcard 可用。
2. **`RenderEngine: failed to create EGLImage ... fmt=1(RGBA) usage=0x933` + `Failed to bind framebuffer → Aborting GPU composition`**：SF 用 GPU 合成某些 RGBA 层时，mesa23 从 minigbm gralloc 建 EGLImage 失败（device 合成会兜底，但会掉帧/screencap 坏）。属 gralloc↔mesa23 的 EGLImage 导入格式问题，可后续调 minigbm format/usage。
3. 之后可把桌面/Quickstep 恢复**硬件加速**提速（现在仍 CPU 渲染），并修 screencap（走 GPU 失败）。

