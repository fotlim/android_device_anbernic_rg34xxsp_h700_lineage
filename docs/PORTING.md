# 移植路线图：还差什么

坦白讲：**设备树和内核是这条路上最容易的部分**。Allwinner 从来不给 H700 出 Android BSP，
所以下面这些 HAL 全部要自己拼。本仓库把它们的位置、可用的上游项目、以及验收方法写清楚，
避免你重复造轮子。

## 现状

| 模块 | 状态 | 说明 |
|---|---|---|
| 内核 DTS | ✅ 编译通过 | `kernel/dts/sun50i-h700-anbernic-rg34xx-sp.dts`（7.1.2+ROCKNIX 补丁树，dtc rc=0） |
| 硬件事实 | ✅ 真机实测 | 见 `docs/HARDWARE.md`（引脚/时序/PMIC/分区/无线全部读过原厂固件） |
| Android 设备树骨架 | ✅ 完成 | 分区、fstab、overlay、按键、init、sepolicy |
| 内核 Android config | ✅ 提供 fragment | binder/dma-heap/zram 等 |
| 面板固件 | ✅ 已入库 | `kernel/firmware/panels/*.panel`（从 ROCKNIX 镜像提取，内建进内核，见 §1） |
| 显示（HWC + gralloc） | ❌ 待做 | 见 §1，最大工作量 |
| 手柄输入驱动 | ✅ 已入库 | `kernel/joypad/`（`rocknix-singleadc-joypad` 收编为内建，需 0004 补丁提供 `joypad_input_g`） |
| GLES / Vulkan | ⚠️ 可复用 Mesa | panfrost，见 §1 |
| 音频 | ❌ 待做 | tinyalsa AIDL HAL，见 §2 |
| Wi-Fi | ❌ 待做 | wpa_supplicant + AIDL wifi HAL，见 §3 |
| 蓝牙 | ❌ 待做 | hciattach + AIDL BT HAL，见 §4 |
| 输入 | ✅ 基本白送 | 内核 evdev + `.kl` 反过来就是 Android 输入 |
| 震动 | ⚠️ 待接 | PWM2 已在 DTS，写个 vibrator HAL |
| 休眠/唤醒 | ⚠️ 待调 | H700 suspend 在 Linux 上也不完美，见 §5 |
| SELinux enforcing | ❌ 待做 | 先 permissive 跑通 |
| 真机验证 | ⏳ 未验证 | 硬件已实测，Android 镜像尚未在真机上跑起来 |

## 1. 图形：整条 DRM 栈

这是能不能"点亮"的关键。

### 前置条件（已解决）

驱动 `panel-mipi.c` 不在源码里写死时序，而是在 probe 时按 DTS **第一个** `compatible`
拼出文件名 `panels/<compatible>.panel`，缺文件就 `dev_err_probe(... "Failed to load firmware")`
直接 probe 失败 —— 表现是**背光亮、屏黑**。

本机需要 `panels/anbernic,rg34xx-sp-panel.panel`，已从 ROCKNIX H700 发行镜像
（`SYSTEM` 分区里的 `usr/lib/kernel-overlays/base/lib/firmware/panels/`）提取，入库在
`kernel/firmware/panels/`，并由 `tools/build-kernel.sh` 通过 `CONFIG_EXTRA_FIRMWARE`
**内建进内核**（不能放 `/vendor`：probe 发生在挂载 vendor 之前）。
文件名必须严格等于 compatible 字符串 —— 逗号和短横线都不能改。

本机面板是 **v1** 批次（实测 `lcd_driver_name = rg34xxsp_v1`），所以主 DTS 用的就是这一份；
`anbernic,rg34xx-sp-v2-panel.panel` 一并入库备用。细节见 `kernel/README.md` §7。

### 已经落地的接线（对着 18.1 树逐条验过）

好消息是 **drm_hwcomposer 和 minigbm 本来就在 LineageOS 18.1 的源码树里**
（`external/drm_hwcomposer`、`external/minigbm`），不需要移植，只需要接对线。
cuttlefish 用的就是同一组模块，可以直接当范本。

| 角色 | 树里真实存在的模块 | 说明 |
|---|---|---|
| HWC | `hwcomposer.drm_minigbm` + `android.hardware.graphics.composer@2.2-service` | drm_hwcomposer 走 DRM/KMS atomic，minigbm 负责 import |
| 分配器 | `android.hardware.graphics.allocator@4.0-service.minigbm` | cros_gralloc 的 HIDL 外壳，自带 gralloc4 |
| Mapper | `android.hardware.graphics.mapper@4.0-impl.minigbm` | passthrough，manifest 里要写 `arch="32+64"` |

两个容易踩的坑：

1. **`hw_get_module` 认的是 `ro.hardware.hwcomposer`**，不是 `ro.vendor.hwcomposer`。
   它拿这个属性的值去找 `/vendor/lib64/hw/hwcomposer.<值>.so`，所以值必须等于
   模块名的后半段（这里是 `drm_minigbm`）。写错不会报错，只会"找不到 HWC"。
2. **`manifest.xml` 缺了 HAL 也不会注册**。没有 `DEVICE_MANIFEST_FILE`，服务编出来
   也白搭，SurfaceFlinger 一直等 composer —— 表现就是开机动画不动。图形三件的
   fqname 必须与上表的版本号严格一致（2.2 / 4.0 / 4.0）。

模块名不能照抄 13/15 时代的设备树（composer 上限在 11 是 2.4，且
`composer@2.1-service.drm`、`allocator@4.0-service.gbm` 这类名字在这棵树里
**不存在**）。仓库里带了个体检脚本：

```bash
tools/verify-modules.py <aosp-root> [模块名...]
```

它对着全树的 `Android.bp` / `Android.mk` 核对模块名，写错一个就会列表报 MISSING。

内核侧已经 `CONFIG_DRM_SUN4I` + panfrost，用户态要三件：

1. **hwcomposer**：`drm_hwcomposer`（LineageOS 的 `android_external_drm_hwcomposer`），
   走 DRM/KMS atomic。需要 `BOARD_USES_DRM_HWCOMPSER`（本仓库 BoardConfig 已留好），
   并提供 `android.hardware.graphics.composer@2.1-service.drm`（Android 11 的 composer 版本上限是 2.1；
   不要照抄 13/15 时代的 `@2.4`，18.1 树里没有那个 HIDL 版本）。
2. **gralloc**：没有 ION，用 **dma-buf heaps + gbm**。上游可用：
   - Mesa 的 `minigbm`（`gralloc_gbm` 通用后端）
   - Android-x86 的 `gbm_gralloc`
   填 `android.hardware.graphics.allocator@4.0-service.gbm`，
   并把 `ro.hardware.gralloc=gbm`（本仓库已写）。
3. **GLES**：两条路，M1 先用前者，后者是性能目标。**这是目前唯一的缺口。**
   Android 需要 `libEGL_*` + `libGLESv2_*` 和 EGL 平台实现
   （走 `gralloc_handle_t` 的 `EGL_NATIVE_BUFFER_ANDROID` 路径）。

   **路线 A（已接好，M1 用这个）：SwiftShader 软件渲染。**
   18.1 树里 `external/swiftshader` 直接产出 `libEGL_swiftshader` /
   `libGLESv2_swiftshader`，已加进 `PRODUCT_PACKAGES`，`vendor.prop` 里
   `ro.hardware.egl=swiftshader`（取值照抄 `device/generic/goldfish/fvp.mk:89`）。
   720×480 软件光栅很慢，但整条链（内核 → init → SF → 桌面）能验证，
   而且不引入任何外部依赖。

   **路线 B（真正吃 Mali-G31 MP2，待做）：panfork-mesa。**
   它带 `android/` 交叉构建（`mesa3d_cross.mk`，GlobalLogic/Roman Stratiienko 那套，
   就是 drm_hwcomposer 作者），机制是：`BOARD_MESA3D_USES_MESON_BUILD := true` +
   `BOARD_MESA3D_GALLIUM_DRIVERS := panfrost`，用 meson 交叉文件把 Mesa 编成
   `libgallium_dri.so` / `libEGL_mesa.so.1` / `libGLESv2_mesa.so.2` / `libglapi.so`
   （模块名 `libgallium_dri` `libEGL_mesa` `libGLESv1_CM_mesa` `libGLESv2_mesa` `libglapi`，
   装机路径 `/vendor/lib64/egl/*` 与 `/vendor/lib64/dri/*`）。

   **动手前必须先解决三件事（都是实测出来的硬约束）**：
   - **libdrm 版本不够**：Mesa 的 `meson.build` 里 `_drm_ver = '2.4.109'`，
     而 18.1 树 `external/libdrm` 是 **2.4.99**，而且**缺 `include/drm/panfrost_drm.h`**
     （panfrost 驱动必需）。要么把 `external/libdrm` 升到 2.4.109+（可参考
     AOSP android-12/13 那一份），要么单独补头文件并谎报版本号。
   - **分支要选对**：`leangangchen/panfork-mesa` 默认停在 **`csf`** 分支
     （CSF 是 G78+/Mali-G610 之后的命令流前端），本机是 **G31 MP2 = Bifrost**，
     必须切到 Bifrost 分支（仓库里有个 `(test): Add biforst patch` 提交可参考）。
   - **宿主工具**：meson 交叉构建靠 `PATH` 里的 `meson` 和 `ninja`，本机已装
     （`meson 1.12.1` / `ninja 1.13.2` / `mako 1.4.3`，`pip3 install`）。

   **没有 EGL 之前 SurfaceFlinger 起不来**（SF 开局就要 EGL 建 RenderEngine）。

验收：`adb shell dumpsys SurfaceFlinger` 能看到 720×480 的 `DisplayDeviceInfo`，
`dumpsys gfxinfo` 有帧；`/sys/kernel/debug/dri/0/state` 有 plane 活动。

## 2. 音频：tinyalsa AIDL HAL

- 内核：`CONFIG_SND_SUN4I_CODEC` + `CONFIG_SND_SUN50I_CODEC_ANALOG`
- 用户态：Android 11 的 `android.hardware.audio.service`（HIDL 5.0）+ `libtinyalsa`，
  按 `/vendor/etc/mixer_paths.xml` 那样的 mixer 表配线圈：
  - 功放：PI5 拉高才出声（DTS 里是 `allwinner,pa-gpios`）
  - 耳机：PI3 检测，74HC4052D 切换
- `audio_policy_configuration.xml` 里只保留 speaker / headphone / 无麦克风

验收：`tinyplay` 能出声；`adb shell dumpsys media.audio_flinger` 有 output stream。

## 3. Wi-Fi：RTW88 + wpa_supplicant

- 内核：`rtw88` / `rtw88_sdio` / `rtw88_8821c(s)`（`kernel/fragment.config` 已列）
  需要固件 `rtw88/rtw8821c_fw.bin` → `/vendor/firmware/` 或 `/lib/firmware/`
- 用户态：AOSP `hardware/interfaces/wifi/aidl/default`（包 wpa_supplicant）+ nl80211
- `wpa_supplicant.conf` 里 `driver=nl80211`
- ✅ 已确认型号：**RTL8821CS**（stock 的 `8821cs` 驱动，SDIO，MAC `68:8f:c9:ca:29:40`）；
  老 `rtw88` 的 8821CS 支持较新（7.1.x 里是 `rtw88_8821cs`），若 probe 失败可退回 ROCKNIX 用的
  厂商 RTW 驱动

验收：`iw dev` 有 `wlan0`，`cmd wifi status` 能扫到 AP。

## 4. 蓝牙：UART H5 + AIDL HAL

- 内核：`CONFIG_BT_HCIUART` + `CONFIG_BT_HCIUART_RTL`
- U-Boot 阶段没有 BT 固件下载，必须靠内核期 `hciattach`：`rtk_h5` 走 UART1
  （PG16/17/19 已接好）
- HAL：`android.hardware.bluetooth` AIDL default 实现，把 `/dev/hci0` 交出去

验收：`adb shell cmd bluetooth_manager enable` 后 `hciconfig -a` 有设备。

## 5. 休眠、电源

- H700 的 `suspend` 在主线里**不完整**（ROCKNIX 也是靠补丁+用户态策略绕）。
- 现实做法：用 `s2idle`（`mem_sleep_default=s2idle`）或干脆只做屏幕关断 + CPU idle。
- 盖子上盖 = `SW_LID`，Android 侧映射为关屏 + 挂起（需要 `PowerManager` 的 lid 支持或
  一个小的 `InputManager` 钩子）。
- 唤醒源：电源键（**AXP2202** PEK）、盖子打开（PE7，DTS 里 `wakeup-source`）。

## 6. SELinux

先 `androidboot.selinux=permissive` 跑通，收集：

```sh
adb shell dmesg | grep avc
adb shell cat /sys/fs/selinux/policy   # 确认策略加载
```

再按 `sepolicy/vendor/*` 补规则，最后改成 `enforcing`。

## 7. 里程碑

- **M0**：U-Boot → 内核 → `init` 起来，能 `adb shell`（屏幕可以黑）
  - 先试**原厂 U-Boot 直接吃 boot.img**（它的 bootargs 带 `androidboot.mode=normal` /
    `boot_type=1`，是全志 Android 系引导器），省掉换 U-Boot 的风险
  - 退路：换 sunxi U-Boot + `extlinux.conf`，见 `docs/BUILD.md` §4
- **M1**：SurfaceFlinger 出画面（哪怕只有开机动画）
- **M2**：输入可用（能用摇杆操作 recovery / 桌面）
- **M3**：声音
- **M4**：Wi-Fi / 蓝牙
- **M5**：休眠与盖子
- **M6**：enforcing + 稳定性
