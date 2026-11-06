#
# SPDX-License-Identifier: Apache-2.0
#
# device.mk -- Anbernic RG34XXSP
#

$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/updatable_apex.mk)

DEVICE_PATH := device/anbernic/rg34xxsp

# ---------------------------------------------------------------------------
# Kernel (prebuilt, built out of tree -- see kernel/README.md)
# ---------------------------------------------------------------------------
TARGET_KERNEL_DIR ?= device/anbernic/rg34xxsp-kernel
LOCAL_KERNEL := $(TARGET_KERNEL_DIR)/Image

PRODUCT_COPY_FILES += \
    $(LOCAL_KERNEL):kernel

# The kernel is monolithic; nothing is loaded from vendor_dlkm.
# (If you later split drivers out, set BOARD_VENDOR_KERNEL_MODULES and use the
#  vendor_dlkm partition instead of adding to PRODUCT_COPY_FILES here.)

# ---------------------------------------------------------------------------
# Overlays / configuration
# ---------------------------------------------------------------------------
DEVICE_PACKAGE_OVERLAYS += $(DEVICE_PATH)/overlay

PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/init/init.rg34xxsp.rc:$(TARGET_COPY_OUT_VENDOR)/etc/init/init.rg34xxsp.rc \
    $(DEVICE_PATH)/init/ueventd.rg34xxsp.rc:$(TARGET_COPY_OUT_VENDOR)/etc/ueventd.rc \
    $(DEVICE_PATH)/keylayout/Vendor_484b_Product_14df.kl:$(TARGET_COPY_OUT_VENDOR)/usr/keylayout/Vendor_484b_Product_14df.kl \
    $(DEVICE_PATH)/fstab.rg34xxsp:$(TARGET_COPY_OUT_VENDOR)/etc/fstab.rg34xxsp

# ---------------------------------------------------------------------------
# Product shape: handheld console, 720x480 @ ~240 dpi, no camera, no modem
# ---------------------------------------------------------------------------
PRODUCT_CHARACTERISTICS := tablet
PRODUCT_AAPT_CONFIG := normal
PRODUCT_AAPT_PREF_CONFIG := hdpi
PRODUCT_LOCALES := en-US

PRODUCT_SYSTEM_PROPERTIES += \
    ro.sf.lcd_density=240 \
    ro.zygote=zygote64_32 \
    ro.hardware.egl=mesa \
    ro.hardware.gralloc=gbm \
    ro.opengles.version=196610 \
    ro.hardware.vulkan=panfrost \
    ro.vendor.hwcomposer=drm \
    ro.boot.qemu=0 \
    persist.sys.zram_enabled=1

PRODUCT_VENDOR_PROPERTIES += \
    vendor.zram.size=75% \
    ro.vendor.product.rg34xxsp=true

# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
PRODUCT_PACKAGES += \
    libdrm

# Graphics / media / audio HALs are not part of AOSP for Allwinner, they have to
# be added by this port. Uncomment each one as it becomes available:
#
#   android.hardware.graphics.composer@2.4-service.drm    # drm_hwcomposer
#   android.hardware.graphics.allocator@4.0-service.gbm   # gbm gralloc
#   android.hardware.audio.service                        # tinyalsa AIDL HAL
#   android.hardware.wifi@1.6-service                     # wpa_supplicant HAL
#   android.hardware.bluetooth@1.1-service.rtk            # UART/H5 bluetooth
#
# See docs/PORTING.md for the exact upstream projects they come from.

PRODUCT_USE_DYNAMIC_PARTITIONS := true
PRODUCT_BUILD_SUPER_PARTITION := true
PRODUCT_SHIPPING_API_LEVEL := 34

# 32-bit apps are supported, keep the 32-bit zygote.
TARGET_SUPPORTS_32_BIT_APPS := true

# Recovery UI defaults that suit a 720x480 panel.
TARGET_RECOVERY_UI_MARGIN_HEIGHT := 56