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

# AOSP base, mandatory and easy to forget.
#
# Lineage's vendor/lineage/config/*.mk chain (common_full_tablet_wifionly ->
# common_full -> common_mobile -> common) contains NO inherit of the AOSP base
# product, because on a normal Lineage device the device makefile brings it in.
# Without these two lines the product has no base_system/base_vendor, so:
#   * PRODUCT_PACKAGES never gets "init_vendor" (build/make/target/product/
#     base_vendor.mk), which is what pulls "init_first_stage" into
#     TARGET_RAMDISK_OUT -> ramdisk.img ended up 777 bytes with only
#     /ramdisk/fstab.rg34xxsp inside, i.e. an unbootable boot.img;
#   * no boot classpath / PRODUCT_USES_DEFAULT_ART_CONFIG is configured, so
#     Soong's dexpreopt bootjars singleton never emits
#     out/soong/dexpreopt_soong.config, and the make-side dexpreopt rule runs
#     dexpreopt_gen with an empty --global config and dies with
#     "--global configuration file is required".
#
# core_64_bit.mk is documentation-only in 18.1 but kept for convention.
# base.mk = base_system + base_system_ext + base_vendor + base_product, i.e.
# every partition gets its base and nothing else is dragged in (we deliberately
# avoid full_base.mk: its sounds/languages packages are dead weight on 1 GiB).
$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/base.mk)

# Lineage's Wi-Fi-only tablet configuration: this is a handheld console with no
# modem and no camera.
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

# ---------------------------------------------------------------------------
# Skip the kernel half of the VINTF compatibility check.
#
# The check has two halves and only one of them fails here. The HAL half runs
# against our manifest and now passes completely (gatekeeper@1.0 and keymaster
# 4.0 were the missing links). The kernel half is what breaks:
#
#   Runtime info and framework compatibility matrix are incompatible:
#   No kernel entry found for kernel version 7.1
#   Minimum LTS: 4.9.165, kernel FCM version: 4
#
# build/make/core/Makefile:3920 only adds "--kernel <ver>:<configs>" to
# checkvintf when PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS is true, and
# product_config.mk:336 defaults it to true whenever it is empty. Setting it to
# false removes the argument, so checkvintf skips the kernel row lookup and the
# HAL checks still run -- which is exactly what we want: keep the safety net
# that catches missing HALs, drop the table lookup that cannot succeed.
#
# The lookup cannot succeed by design: Android 11's compatibility matrix was
# frozen in 2020 when mainline was 5.4, and it has no 7.x kernel row at all.
# This is a missing row in a table, not an incompatible kernel. (AOSP documents
# this exact escape hatch at build/make/core/Makefile:3947 as option 3.)
#
# Do NOT try PRODUCT_ENFORCE_VINTF_MANIFEST := false instead: build/make/core/
# config.mk:712-716 force-overwrites it from PRODUCT_FULL_TREBLE (unless you
# also set the deprecated _OVERRIDE form), and the override would drag
# PRODUCT_FULL_TREBLE down to false, killing VNDK and the linker namespaces.
#
# Revisit when moving to a newer Lineage whose matrix knows modern kernels.
PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS := false
