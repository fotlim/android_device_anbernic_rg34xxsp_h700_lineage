#
# SPDX-License-Identifier: Apache-2.0
#
# BoardConfig.mk -- Anbernic RG34XXSP (Allwinner H700 / sun50i-h700)
# Target: LineageOS 18.1 (Android 11).  See docs/HARDWARE.md + docs/BUILD.md.
#
# Everything in here that looks "measured" comes from the stock Ubuntu 22.04
# firmware this unit shipped with (BSP kernel 4.9.170) -- probe scripts and the
# decompiled DT live in docs/HARDWARE.md:
#   SoC      : Allwinner H700, 4x Cortex-A53 @ 0.48-1.416 GHz, Mali-G31 MP2
#   RAM      : 1 GiB LPDDR3 (axp2202-dcdc3 = 1.2 V -> DDR3 U-Boot variant)
#   Panel    : 720x480 landscape 3:2, RGB 24-bit @26 MHz, "panel-mipi" driver
#              (SPI: PI9 SCK / PI10 MOSI / PI8 CS, PI14 reset, PI15 power,
#               PWM0 backlight on PD28).  Needs panels/<compatible>.panel fw.
#   Input    : "H700 Gamepad" 0019:484b:14df:0100, 2 analog sticks (GPADC mux),
#              17 buttons, PE7 lid switch, PWM2 rumble
#   WiFi/BT  : RTL8821CS on SDIO (MMC1) + BT over UART1 (PG16/17/19)
#   PMIC     : AXP2202 on R_I2C (0x34) -- NOT AXP717, needs the ROCKNIX kernel
#              patch set (see kernel/README.md); RTC PCF8563 on R_I2C (0x51)
#   Storage  : eMMC (MMC0) + TF slot (MMC2, PE22 card-detect, PE4 power)
#

DEVICE_PATH := device/anbernic/rg34xxsp

# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------
TARGET_ARCH := arm64
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_VARIANT := cortex-a53
TARGET_CPU_VARIANT_RUNTIME := cortex-a53

TARGET_2ND_ARCH := arm
TARGET_2ND_ARCH_VARIANT := armv8-a
TARGET_2ND_CPU_ABI := armeabi-v7a
TARGET_2ND_CPU_ABI2 := armeabi
TARGET_2ND_CPU_VARIANT := cortex-a53
TARGET_2ND_CPU_VARIANT_RUNTIME := cortex-a53

TARGET_USES_64_BIT_BINDER := true
TARGET_SUPPORTS_64_BIT_APPS := true
TARGET_SUPPORTS_32_BIT_APPS := true

TARGET_BOARD_PLATFORM := sun50i_h700
TARGET_BOOTLOADER_BOARD_NAME := sun50i_h700

# ---------------------------------------------------------------------------
# 图形后端：mesa + panfrost（2026-11-18，sd97）
# ---------------------------------------------------------------------------
# 背景：SwiftShader 这条临时后端在 boot bbb58a26 上被证明**采样不了 minigbm 的
# dma-buf 缓冲区**（自建探针：往 gralloc 里渲染正确，采样同一块纹理读回全黑，
# CPU 写过的那个直接 SIGSEGV），而 SurfaceFlinger 每帧都要采样每一层 —— 所以
# 屏幕永远黑，SF 每次合成必崩。见 docs/ISSUES.md 的 I-图形-01/02/03。
#
# 这棵树里已经带了 **mesa 20.3.4**（external/mesa3d）和 src/gallium/drivers/panfrost，
# 而且它的 Android.mk 只要 BOARD_GPU_DRIVERS 非空就会产出 libGLES_mesa。
# 关键：mesa 20.3 对 libdrm 的要求是 **2.4.89**（meson.build 里按驱动取最高值），
# 本树的 libdrm 是 2.4.99 —— 满足。之前 HANDOVER 里写的"必须先升到 2.4.109"
# 是 **mesa 23** 的要求，20.3 不需要，panfrost_drm.h 也已经在
# bionic/libc/kernel/uapi/drm/ 和 external/mesa3d/include/drm-uapi/ 里各有一份。
#
# 注意：mesa 的 gallium DRI 默认目录被写死成 /vendor/lib64/dri（见
# external/mesa3d/Android.bp 的 mesa_common_defaults），所以驱动模块要落在
# /vendor/lib64/dri，EGL 要能找到 "mesa" 后端（ro.hardware.egl=mesa）。
BOARD_GPU_DRIVERS := panfrost
# mesa 的 Android 胶水按这个值挑 egl/mapi 的实现（11 = Android 11）。
MESA_ANDROID_MAJOR_VERSION := 11

# No vendor bootloader lives in a partition image: U-Boot (sunxi SPL + TF-A
# bl31) is written raw at 8 KiB into the boot medium, exactly like Anbernic's
# stock image and ROCKNIX do it.
TARGET_NO_BOOTLOADER := true
TARGET_NO_RADIOIMAGE := true

# ---------------------------------------------------------------------------
# Boot image
# ---------------------------------------------------------------------------
# Deliberately NOT the GKI / vendor_boot scheme: U-Boot parses a classic
# header-v0 Android boot image and booti's into it.
#
# The geometry below is copied byte-for-byte from the stock boot image that
# ships in /dev/mmcblk0p4 on this unit (verified by dd + parsing the header,
# see docs/BUILD.md "实测：原厂 p4 就是标准 Android boot.img"):
#
#   magic         ANDROID!
#   kernel_size   17686536   kernel_addr   0x40080000
#   ramdisk_size   2606339   ramdisk_addr  0x42000000
#   second_size          0   second_addr   0x40f00000
#   tags_addr     0x40000100 page_size     2048
#   name          sun50i_arm64
#
# Those addresses decompose into base 0x40000000 plus per-component offsets,
# which is exactly how mkbootimg composes them:
#   kernel  = base + 0x00080000 = 0x40080000
#   ramdisk = base + 0x03000000 = 0x43000000
#   tags    = base + 0x00000100 = 0x40000100
#   second  = base + 0x00f00000 = 0x40f00000
#
# The ramdisk offset is NOT the vendor's 0x02000000. Our Image is 32,020,488
# bytes and starts at 0x40080000, so it ends at 0x41F0A0C8; with the original
# offset only 983 KiB of headroom was left before the ramdisk load address.
# 0x03000000 buys 16 MiB. Everything stays below 0x45000000, which matters:
# the stock U-Boot's boot_normal does "sunxi_flash read 45000000 boot", so it
# stages the whole boot.img at 0x45000000 first.
BOARD_BOOT_HEADER_VERSION := 0
BOARD_KERNEL_BASE := 0x40000000
BOARD_KERNEL_PAGESIZE := 2048

# 坑：Android 11 的 build/make 只认 BOARD_KERNEL_BASE 和 BOARD_KERNEL_PAGESIZE。
# build/make/core/Makefile:1237-1241 只把这两个塞进 INTERNAL_BOOTIMAGE_ARGS，
# 而 BOARD_KERNEL_OFFSET / BOARD_RAMDISK_OFFSET / BOARD_TAGS_OFFSET /
# BOARD_KERNEL_SECOND_OFFSET 这四个变量在 11 的构建系统里**根本没有引用**
# （全树 grep 只命中我们自己的 BoardConfig）—— 那是老 Android 的写法。
# 只写它们的话 mkbootimg 会静默套用自带默认值：
#   kernel 0x00008000 / ramdisk 0x01000000 / tags 0x00000100 / second 0x00f00000
# 实测第一版 boot.img 就是这么出的：kernel_addr=0x40008000（应为 0x40080000）、
# ramdisk_addr=0x41000000（应为 0x43000000）。内核会被搬到错 15 倍的地方，
# 上机必然不启动。所以必须显式写进 BOARD_MKBOOTIMG_ARGS。
BOARD_MKBOOTIMG_ARGS += --kernel_offset 0x00080000
BOARD_MKBOOTIMG_ARGS += --ramdisk_offset 0x03000000
BOARD_MKBOOTIMG_ARGS += --tags_offset 0x00000100
BOARD_MKBOOTIMG_ARGS += --second_offset 0x00f00000

# 内核从哪来：树外编好的 prebuilt。
#
# 更正一条早期的错误结论：lineage-18.1 的**设备板**用 vendor/lineage/build/tasks/
# kernel.mk 编内核，它读的是 TARGET_PREBUILT_KERNEL —— 前提是 TARGET_KERNEL_SOURCE
# 为空（KERNEL_SRC := $(TARGET_KERNEL_SOURCE)，为空时 wildcard 不命中，走 prebuilt 分支）。
# 之前说"18.1 不读 TARGET_PREBUILT_KERNEL"只在 build/make 的 board 集合里成立，
# 而 mka 走的正是 vendor/lineage 那一份。它会先把 prebuilt 拷到 $(PRODUCT_OUT)/kernel。
#
# BOARD_KERNEL_IMAGE_NAME 也必填（kernel.mk 拿它拼 arm64/boot/<name>），
# 而且这一支编的是**未压缩** Image（4K pages），不是 Image.gz。
BOARD_KERNEL_IMAGE_NAME := Image
TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/kernel/Image

# console on the debug UART (UART0 / PH) for bring-up. androidboot.selinux=
# permissive: flip to enforcing only once permissive denials have been
# collected on hardware.
BOARD_KERNEL_CMDLINE := \
    console=ttyS0,115200 \
    earlycon \
    no_console_suspend \
    coherent_pool=1M \
    androidboot.hardware=rg34xxsp \
    androidboot.selinux=permissive \
    androidboot.boot_devices=mmcblk0

# --board fills the 16-byte "name" field. Stock writes "sun50i_arm64"; a vendor
# U-Boot of this vintage may well print or even check it, so we match it rather
# than inventing a name of our own.
BOARD_MKBOOTIMG_ARGS += --board sun50i_arm64
BOARD_BOOTIMAGE_PARTITION_SIZE := 67108864   # 64 MiB

# 本机没有 recovery 分区（原厂 GPT 里也没有，原厂 boot_recovery 那条 env 指向的
# 分区根本不存在）。曾经写过 BOARD_USES_RECOVERY_AS_BOOT := true，那会把
# ramdisk-recovery.img 塞进 boot.img —— 实测出的 boot.img 里 ramdisk_size 就是
# 7,753,981（即 7.4 MB 的 recovery ramdisk），而不是 900 KB 的 normal ramdisk。
# 那样启动只会进 recovery，进不了 Android。这里改成"没有 recovery"，
# 并显式跳过 OTA 包：非 A/B 的 OTA 打包脚本会因拿不到 recovery.img 直接抛
# ota_from_target_files.py:1979 "--- target build has specified no recovery ---"。
TARGET_NO_RECOVERY := true
TARGET_SKIP_OTA_PACKAGE := true
TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/fstab.rg34xxsp

# A/B is not used; the handheld boots one slot.
AB_OTA_UPDATER := false
BOARD_AVB_ENABLE := false
# NOTE: PRODUCT_ENFORCE_VINTF_MANIFEST deliberately does NOT live here -- a
# PRODUCT_* assignment in BoardConfig.mk is silently ignored. It sits at the end
# of lineage_rg34xxsp.mk instead; see the long comment there.

# ---------------------------------------------------------------------------
# Partitions
# ---------------------------------------------------------------------------
# Layout produced by tools/mk-sdcard.sh (GPT, so Android gets /dev/block/by-name):
#   p1 boot       64 MiB   FAT32  -> U-Boot distro boot: extlinux.conf + Image + dtb
#   p2 misc        4 MiB
#   p3 metadata   16 MiB
#   p4 super       4 GiB   dynamic: system system_ext product vendor
#   p5 userdata   remainder
BOARD_USES_METADATA_PARTITION := true

TARGET_USERIMAGES_USE_EXT4 := true
BOARD_USERDATAIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_USERDATAIMAGE_PARTITION_SIZE := 2147483648  # only for "make userdataimage"

BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_PRODUCTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEM_EXTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE := ext4

TARGET_COPY_OUT_PRODUCT := product
TARGET_COPY_OUT_SYSTEM_EXT := system_ext
TARGET_COPY_OUT_VENDOR := vendor

# Dynamic partitions are enabled with PRODUCT_USE_DYNAMIC_PARTITIONS := true in
# lineage_rg34xxsp.mk (Android 11 has no TARGET_USE_DYNAMIC_PARTITIONS).
#
# 组名必须自己带上 `_dynamic_partitions` 后缀：Android 11 的 config.mk 是拿
# BOARD_SUPER_PARTITION_GROUPS 里的每一项大写后去找 BOARD_<GROUP>_SIZE 和
# BOARD_<GROUP>_PARTITION_LIST（见 build/make/core/config.mk:953）。
# 组名写成裸的 `rg34xxsp`，它就会去找 BOARD_RG34XXSP_SIZE 而报
# "BOARD_RG34XXSP_SIZE must not be empty"。
# cuttlefish 用 google_dynamic_partitions 也是这个道理。
BOARD_BUILD_SUPER_IMAGE_BY_DEFAULT := true
BOARD_SUPER_PARTITION_METADATA_DEVICE := super
BOARD_SUPER_PARTITION_SIZE := 4294967296             # 4 GiB
BOARD_SUPER_PARTITION_GROUPS := rg34xxsp_dynamic_partitions
BOARD_RG34XXSP_DYNAMIC_PARTITIONS_PARTITION_LIST := \
    system system_ext product vendor
BOARD_RG34XXSP_DYNAMIC_PARTITIONS_SIZE := 4290772992 # 4 GiB - 4 MiB 元数据

# No vendor_dlkm / system_dlkm on Android 11: the kernel is monolithic (all
# drivers built in), which keeps the boot chain simple on this class of device.

# ---------------------------------------------------------------------------
# Graphics / media
# ---------------------------------------------------------------------------
# 没有 Allwinner 的 HWC，走社区这套（18.1 树里本来就有的模块，见 device.mk）：
#   hwcomposer : drm_hwcomposer over DRM/KMS atomic（composer 2.2 + hwcomposer.drm_minigbm）
#   gralloc    : minigbm 的 cros_gralloc（allocator@4.0 + mapper@4.0）
#   GLES       : Mesa panfrost（Mali-G31 MP2）—— 这一块要外部工程，见 docs/PORTING.md §1
BOARD_USES_ALSA_AUDIO := true

# 1 GiB：用 svelte 版 malloc（不用 scudo 的完整分配器），省一块常驻内存
MALLOC_SVELTE := true

# 属性文件：落到 /vendor/build.prop，图形/内存相关的选择都在里面
TARGET_VENDOR_PROP += $(DEVICE_PATH)/vendor.prop

# Widevine is unavailable (no TEE); clearkey only.
TARGET_ENABLE_MEDIADRM_64 := true

# ---------------------------------------------------------------------------
# Connectivity (mainline cfg80211/mac80211 + rtw88, nl80211 wpa_supplicant)
# ---------------------------------------------------------------------------
BOARD_HAVE_BLUETOOTH := true
WPA_SUPPLICANT_VERSION := VER_0_8_X
BOARD_WPA_SUPPLICANT_DRIVER := NL80211
BOARD_HOSTAPD_DRIVER := NL80211
BOARD_WLAN_DEVICE := rtw88_8821cs
WIFI_DRIVER_FW_PATH_PARAM := "/sys/module/rtw88_sdio/parameters/fwp"
WIFI_DRIVER_MODULE_NAME := rtw88_8821cs

# ---------------------------------------------------------------------------
# SELinux
# ---------------------------------------------------------------------------
BOARD_VENDOR_SEPOLICY_DIRS += $(DEVICE_PATH)/sepolicy/vendor

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
BOARD_FLASH_BLOCK_SIZE := 4096
