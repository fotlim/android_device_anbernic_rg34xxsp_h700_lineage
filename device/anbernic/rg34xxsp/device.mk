#
# SPDX-License-Identifier: Apache-2.0
#
# device.mk -- Anbernic RG34XX-SP (Allwinner H700, 1 GiB) on LineageOS 18.1 / Android 11
#
# 这个文件里每一个 PRODUCT_PACKAGES 的模块名都必须在 18.1 树里真实存在，
# 否则 mka bacon 会在很久以后才报 "package X not found"。用
#     tools/verify-modules.py <aosp-root>
# 可以先全部体检一遍。HAL 命名在各代 Android 之间差别很大，别照抄 13/15 时代的
# 设备树（composer 的上限在 11 是 2.1/2.2，不是 2.4）。
#

$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/updatable_apex.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/developer_gsi_keys.mk)

# 1 GiB 内存的堆参数（AOSP 现成的 7" 平板档，比手搓 vm.heapsize 靠谱）
$(call inherit-product, frameworks/native/build/tablet-7in-hdpi-1024-dalvik-heap.mk)

DEVICE_PATH := device/anbernic/rg34xxsp

# ---------------------------------------------------------------------------
# 内核：树外编译的 prebuilt
# ---------------------------------------------------------------------------
# lineage-18.1 的 kernel.mk（vendor/lineage/build/tasks/kernel.mk）在
# TARGET_KERNEL_SOURCE 为空时会走 prebuilt 分支，把 TARGET_PREBUILT_KERNEL
# 拷到 $(PRODUCT_OUT)/kernel，boot.img 就从那里取内核（BoardConfig 里已经设了）。
#
# 所以这里**不要**再放 `PRODUCT_COPY_FILES += $(LOCAL_KERNEL):kernel`：
# 两个规则写同一个目标文件，kati 会报 overriding recipe。
#
# 内核是单体的（所有驱动内建），不加载 vendor_dlkm。

# ---------------------------------------------------------------------------
# rootdir / 配置
# ---------------------------------------------------------------------------
# fstab 进 ramdisk、ueventd.rc 进 /vendor —— 为什么不能用 PRODUCT_COPY_FILES
# 扔到 /vendor/etc，见 rootdir/Android.mk 顶部注释。
PRODUCT_PACKAGES += \
    fstab.rg34xxsp_ramdisk \
    ueventd.rg34xxsp.rc

PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/init/init.rg34xxsp.rc:$(TARGET_COPY_OUT_VENDOR)/etc/init/init.rg34xxsp.rc \
    $(DEVICE_PATH)/keylayout/Vendor_484b_Product_14df.kl:$(TARGET_COPY_OUT_VENDOR)/usr/keylayout/Vendor_484b_Product_14df.kl

DEVICE_PACKAGE_OVERLAYS += $(DEVICE_PATH)/overlay

# HAL 声明。没有 DEVICE_MANIFEST_FILE，这些 HAL 即使编出来也不会被注册，
# SurfaceFlinger 会一直等 composer，表现就是开机动画不动。
DEVICE_MANIFEST_FILE += $(DEVICE_PATH)/manifest.xml

# ---------------------------------------------------------------------------
# 产品形状：掌机，720x480 @ 240dpi，无相机、无 modem、无 GPS
# ---------------------------------------------------------------------------
PRODUCT_CHARACTERISTICS := tablet
PRODUCT_AAPT_CONFIG := normal
PRODUCT_AAPT_PREF_CONFIG := hdpi
PRODUCT_LOCALES := en-US

# ---------------------------------------------------------------------------
# 图形栈：drm_hwcomposer + cros_gralloc(minigbm)，两者 18.1 树里本来就有
# ---------------------------------------------------------------------------
# 组合是从 18.1 树里现成能跑的那套抄来的（cuttlefish 用同一组模块）：
#   composer@2.2-service  -> HwcLoader 走 hw_get_module("hwcomposer")
#                            -> 由 ro.hardware.hwcomposer 决定加载哪个 .so
#                            -> hwcomposer.drm_minigbm.so（drm_hwcomposer + minigbm importer）
#   allocator@4.0-service.minigbm   自己带 cros_gralloc 驱动，是真正的分配器
#   mapper@4.0-impl.minigbm         给 SurfaceFlinger 用的 passthrough mapper
# 注意 composer 只到 2.2：Android 11 的 HWC HIDL 上限就是 2.4，但 drm_hwcomposer
# 这一支实现的是 HWC2，配 2.2 是官方示例里验证过的组合。
PRODUCT_PACKAGES += \
    hwcomposer.drm_minigbm \
    android.hardware.graphics.composer@2.2-service \
    android.hardware.graphics.allocator@4.0-service.minigbm \
    android.hardware.graphics.mapper@4.0-impl.minigbm \
    libdrm

# EGL/GLES 先用 SwiftShader 顶上，把 M1（出画面）解锁。
#
# 为什么不是 Mesa：18.1 树里的 external/mesa3d 只有源码 + 给 host 用的
# Android.gen.bp，编不出设备端 libEGL_mesa。真正的 GPU 路线是 panfork-mesa
# （23.0.0-devel，带 android/ 的 meson 交叉构建），但它要求 libdrm >= 2.4.109
# （mesa meson.build 里 _drm_ver = '2.4.109'），而本树 external/libdrm 是
# 2.4.99 且**缺 include/drm/panfrost_drm.h** —— 得先把 libdrm 升上去。
# 这一步不该卡住开机验证，所以先上 SwiftShader：Android 11 自带的
# libEGL_swiftshader / libGLESv2_swiftshader（external/swiftshader），
# goldfish 的 fvp.mk 就是这么用的（ro.hardware.egl=swiftshader）。
# 720x480 的软件渲染很慢，但足够验证「内核 → init → SF → 桌面」整条链。
PRODUCT_PACKAGES += \
    libEGL_swiftshader \
    libGLESv2_swiftshader

# ---------------------------------------------------------------------------
# 其它 HAL
# ---------------------------------------------------------------------------
PRODUCT_PACKAGES += \
    android.hardware.health@2.1-service \
    android.hardware.memtrack@1.0-service \
    android.hardware.usb@1.0-service \
    android.hardware.drm@1.3-service-lazy.clearkey \
    disable_configstore

# 音频：Android 11 的 HIDL 5.0 服务 + tinyalsa。设备侧的 audio_hw 实现还没写
# （见 docs/PORTING.md §2），先放服务本体，HAL 缺失只会让 AudioService 降级，
# 不挡开机。
PRODUCT_PACKAGES += \
    android.hardware.audio.service \
    android.hardware.audio@5.0-impl \
    android.hardware.audio.effect@5.0-impl \
    libtinyalsa

# ---------------------------------------------------------------------------
# 分区与构建开关
# ---------------------------------------------------------------------------
PRODUCT_USE_DYNAMIC_PARTITIONS := true
PRODUCT_BUILD_SUPER_PARTITION := true

# 恢复界面：720x480 上留一点边距
TARGET_RECOVERY_UI_MARGIN_HEIGHT := 56