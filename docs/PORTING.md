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
| 显示（HWC + gralloc） | ❌ 待做 | 见 §1，最大工作量 |
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

这是能不能"点亮"的关键。**M1 的前置条件只有一个**：拿到
`firmware/panels/anbernic,rg34xx-sp-panel.panel`（面板初始化的命令序列 + 时序，驱动
`panel-mipi.c` 缺它就 probe 失败 → 背光亮、屏黑）。细节见 `kernel/README.md` §7；
最省事的来源是从一份 ROCKNIX H700 镜像的 `/usr/lib/firmware/panels/` 里拷。

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
3. **GLES**：Mesa **panfrost**。Android 需要一个 `libGLES_mesa` + EGL 平台实现
   （`gralloc_handle_t` 的 `EGL_NATIVE_BUFFER_ANDROID` 路径）。社区最成熟的参考是
   Android-x86 的 `mesa-android` 构建。`ro.hardware.egl=mesa`（已写）。

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