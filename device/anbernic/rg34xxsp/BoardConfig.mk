#
# SPDX-License-Identifier: Apache-2.0
#
# BoardConfig.mk -- Anbernic RG34XXSP (Allwinner H700 / sun50i-h700)
#
# Hardware summary (see docs/HARDWARE.md for sources / confidence):
#   SoC      : Allwinner H700, 4x Cortex-A53 @ 0.48-1.416 GHz, Mali-G31 MP2
#   Panel    : 720x480 landscape 3:2, MIPI-DPI-over-SPI ("panel-mipi-dpi-spi"),
#              24-bit RGB, PWM0 (PD28) backlight, VDD on PI15, reset PI14
#   RAM      : LPDDR4 (1 GiB on the units seen so far -- verify your board)
#   Input    : "H700 Gamepad" 0019:484b:14df:0100, 2 analog sticks over a
#              2-bit GPIO ADC mux, 17 buttons, PE7 lid switch, PWM2 rumble
#   WiFi/BT  : RTL8821CS on MMC1 + UART1 (inherited from RG35XX Plus wiring)
#   PMIC     : AXP717 on R_I2C (0x34), RTC PCF8563 on R_I2C (0x51)
#   Storage  : OS from SD/eMMC on MMC0 (mmcblk0), second SD card on MMC2
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
TARGET_CPU_SMP := true

TARGET_BOARD_PLATFORM := sun50i_h700
TARGET_BOOTLOADER_BOARD_NAME := sun50i_h700

# No vendor bootloader lives in a partition image: U-Boot (sunxi SPL + TF-A)
# is written raw at 8 KiB into the boot medium, exactly like Anbernic's stock
# Linux image and ROCKNIX do it.
TARGET_NO_BOOTLOADER := true
TARGET_NO_RADIOIMAGE := true

# This product has no cellular modem and no camera.
BOARD_WITHOUT_RADIO := true
USE_CAMERA_STUB := false

# ---------------------------------------------------------------------------
# Boot image
# ---------------------------------------------------------------------------
# Deliberately NOT the GKI / vendor_boot scheme. U-Boot for sunxi is most
# reliable with a classic header-v0 Android boot image (CONFIG_ANDROID_BOOT_IMAGE
# in U-Boot parses it and hands over to booti). The DTB is loaded separately
# from the FAT boot partition via extlinux.conf, see docs/BUILD.md.
BOARD_BOOT_HEADER_VERSION := 0
BOARD_KERNEL_IMAGE_NAME := Image
BOARD_KERNEL_BASE := 0x40080000
BOARD_KERNEL_PAGESIZE := 4096

# console on the debug UART (UART0 / PH) so that a UART-USB adapter can be
# used during bring-up. androidboot.selinux=permissive: flip to "enforcing"
# only once the device actually boots and permissive denials are collected.
BOARD_KERNEL_CMDLINE := \
    console=ttyS0,115200 \
    earlycon \
    no_console_suspend \
    coherent_pool=1M \
    androidboot.hardware=rg34xxsp \
    androidboot.selinux=permissive \
    androidboot.boot_devices=mmcblk0

BOARD_MKBOOTIMG_ARGS += --board sun50i_h700
BOARD_BOOTIMAGE_PARTITION_SIZE := 67108864   # 64 MiB

# No separate recovery partition: recovery lives inside boot.img.
BOARD_USES_RECOVERY_AS_BOOT := true
TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/fstab.rg34xxsp

# A/B is not used; the handheld boots one slot from removable media.
AB_OTA_UPDATER := false
BOARD_AVB_ENABLE := false

# ---------------------------------------------------------------------------
# Partitions
# ---------------------------------------------------------------------------
# Layout produced by tools/mk-sdcard.sh (GPT, so Android gets /dev/block/by-name):
#   p1 boot       64 MiB   FAT32  -> u-boot distro boot: extlinux.conf + Image + dtb
#   p2 misc        4 MiB
#   p3 metadata   16 MiB
#   p4 super       4 GiB   dynamic: system system_ext product vendor
#   p5 userdata   remainder
BOARD_USES_METADATA_PARTITION := true
BOARD_METADATAIMAGE_PARTITION_SIZE := 16777216

TARGET_USERIMAGES_USE_EXT4 := true
BOARD_USERDATAIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_USERDATAIMAGE_PARTITION_SIZE := 2147483648  # only used for "make userdataimage"

BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_PRODUCTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEM_EXTIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE := ext4

TARGET_COPY_OUT_PRODUCT := product
TARGET_COPY_OUT_SYSTEM_EXT := system_ext
TARGET_COPY_OUT_VENDOR := vendor

TARGET_USE_DYNAMIC_PARTITIONS := true
BOARD_BUILD_SUPER_IMAGE_BY_DEFAULT := true
BOARD_SUPER_IMAGE_IN_UPDATE_PACKAGE := true
BOARD_SUPER_PARTITION_METADATA_DEVICE := super
BOARD_SUPER_PARTITION_SIZE := 4294967296             # 4 GiB
BOARD_SUPER_PARTITION_GROUPS := rg34xxsp
BOARD_RG34XXSP_DYNAMIC_PARTITIONS_PARTITION_LIST := \
    system system_ext product vendor
BOARD_RG34XXSP_DYNAMIC_PARTITIONS_SIZE := 4290772992 # 4 GiB - 4 MiB slot metadata

# No vendor_dlkm / system_dlkm: the kernel is monolithic (all drivers built
# in), which keeps the boot chain simple on this class of device.

# ---------------------------------------------------------------------------
# Graphics / media
# ---------------------------------------------------------------------------
# There is no Allwinner HWC for mainline DRM. The working community recipe is
#   hwcomposer  : drm_hwcomposer (DRM/KMS atomic)
#   gralloc     : gbm-based gralloc (libgralloc_drm)
#   GLES        : Mesa panfrost (Mali-G31 MP2)
# See docs/PORTING.md -- this is the part of the port that needs the most work.
BOARD_USES_ALSA_AUDIO := true

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
TARGET_USES_ION := false          # dma-buf heaps (CONFIG_DMABUF_HEAPS) instead