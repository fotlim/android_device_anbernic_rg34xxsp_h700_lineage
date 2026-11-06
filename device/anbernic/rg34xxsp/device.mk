#
# SPDX-License-Identifier: Apache-2.0
#
# device.mk -- Anbernic RG34XXSP (LineageOS 18.1 / Android 11)
#

$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/updatable_apex.mk)

DEVICE_PATH := device/anbernic/rg34xxsp

# ---------------------------------------------------------------------------
# Kernel (prebuilt, built out of tree -- see kernel/README.md and tools/build.sh)
# ---------------------------------------------------------------------------
# Android 11's build/make does not build kernels and does not read
# TARGET_PREBUILT_KERNEL either (verified on lineage-18.1: only the emulator
# board reads it). The one mechanism that works on this branch is copying the
# Image to $(PRODUCT_OUT)/kernel -- a PRODUCT_COPY_FILES destination without a
# partition prefix lands at the product out root, which is exactly the
# $(INSTALLED_KERNEL_TARGET) path build/make/core/Makefile uses for boot.img.
LOCAL_KERNEL := $(DEVICE_PATH)/kernel/Image

PRODUCT_COPY_FILES += \
    $(LOCAL_KERNEL):kernel

# The kernel is monolithic; nothing is loaded from vendor_dlkm. If drivers are
# split out later, use BOARD_VENDOR_KERNEL_MODULES + vendor_dlkm instead.

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

PRODUCT_PROPERTY_OVERRIDES += \
    ro.sf.lcd_density=240 \
    ro.zygote=zygote64_32 \
    ro.hardware.egl=mesa \
    ro.hardware.gralloc=gbm \
    ro.opengles.version=196610 \
    ro.hardware.vulkan=panfrost \
    ro.vendor.hwcomposer=drm \
    persist.sys.zram_enabled=1

PRODUCT_PROPERTY_OVERRIDES += \
    vendor.zram.size=75% \
    ro.vendor.product.rg34xxsp=true

# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------
PRODUCT_PACKAGES += \
    libdrm

# Graphics / media / audio HALs are not part of AOSP for Allwinner, they have to
# be added by this port. Android 11 service names, uncomment as each one exists:
#
#   android.hardware.graphics.composer@2.1-service.drm   # drm_hwcomposer
#   android.hardware.graphics.allocator@4.0-service.gbm  # gbm gralloc
#   android.hardware.audio.service                       # tinyalsa HIDL 5.0
#   android.hardware.wifi@1.5-service                    # wpa_supplicant HAL
#   android.hardware.bluetooth@1.1-service.rtk           # UART/H5 bluetooth
#
# See docs/PORTING.md for the exact upstream projects they come from.

PRODUCT_USE_DYNAMIC_PARTITIONS := true
PRODUCT_BUILD_SUPER_PARTITION := true

# Recovery UI defaults that suit a 720x480 panel.
TARGET_RECOVERY_UI_MARGIN_HEIGHT := 56