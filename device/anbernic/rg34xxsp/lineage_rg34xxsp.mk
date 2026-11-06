#
# SPDX-License-Identifier: Apache-2.0
#
# LineageOS product definition for the Anbernic RG34XXSP.
# Target: LineageOS 18.1 (Android 11) -- chosen over 22.2 because this unit has
# only 1 GiB of LPDDR3. Android 11 + low-ram mode is the heaviest release that
# still behaves with 1 GiB; Android 13/15 leaves single-digit MiB of free RAM
# after a bare boot on this class of hardware.
#

$(call inherit-product, device/anbernic/rg34xxsp/device.mk)

# Lineage's Wi-Fi-only tablet configuration: this is a handheld console with no
# modem and no camera. (For a plain AOSP tree, replace this with
#   $(call inherit-product, $(SRC_TARGET_DIR)/product/full_base.mk)
# and drop the vendor/lineage inherit. Verified to exist on lineage-18.1.)
$(call inherit-product, vendor/lineage/config/common_full_tablet_wifionly.mk)

PRODUCT_NAME := lineage_rg34xxsp
PRODUCT_DEVICE := rg34xxsp
PRODUCT_BRAND := Anbernic
PRODUCT_MODEL := RG34XXSP
PRODUCT_MANUFACTURER := Anbernic

# Android 11 / API 30: this device never ran a Google release, everything is
# pure AOSP/Lineage, so no shipping-level tricks are needed.
PRODUCT_SHIPPING_API_LEVEL := 30

# Dynamic partitions (super). Android 11 uses the PRODUCT_* variable, not the
# TARGET_USE_DYNAMIC_PARTITIONS one from 18.1-era tutorials.
PRODUCT_USE_DYNAMIC_PARTITIONS := true

# 1 GiB RAM: turn on low-ram/go behaviour (aggressive LMK, fewer cached apps,
# 1 background process). Without this Android 11 sits at ~40 MiB free after
# boot, which makes the home screen visibly stutter.
PRODUCT_PROPERTY_OVERRIDES += \
    ro.config.low_ram=true \
    ro.config.low_ram.reconfigurable=false \
    dalvik.vm.heapstartsize=8m \
    dalvik.vm.heapgrowthlimit=96m \
    dalvik.vm.heapsize=256m \
    dalvik.vm.heaptargetutilization=0.75 \
    dalvik.vm.heapminfree=2m \
    dalvik.vm.heapmaxfree=8m

# Fingerprint: keep the stock-style one for Play certification later on.
PRODUCT_BUILD_PROP_OVERRIDES += \
    PRODUCT_NAME=RG34XXSP \
    TARGET_DEVICE=rg34xxsp