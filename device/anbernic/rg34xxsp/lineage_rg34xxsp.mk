#
# SPDX-License-Identifier: Apache-2.0
#
# LineageOS product definition for the Anbernic RG34XXSP.
#

$(call inherit-product, device/anbernic/rg34xxsp/device.mk)

# Lineage's Wi-Fi-only tablet configuration: this is a handheld console with no
# modem and no camera. (For a plain AOSP tree, replace this with
#   $(call inherit-product, $(SRC_TARGET_DIR)/product/full_base.mk)
# and drop the vendor/lineage inherit. If vendor/lineage/config/ has no
# common_full_tablet_wifionly.mk on your LineageOS branch, use
# common_mini_tablet_wifionly.mk instead -- it only changes the bundled apps.)
$(call inherit-product, vendor/lineage/config/common_full_tablet_wifionly.mk)

PRODUCT_NAME := lineage_rg34xxsp
PRODUCT_DEVICE := rg34xxsp
PRODUCT_BRAND := Anbernic
PRODUCT_MODEL := RG34XXSP
PRODUCT_MANUFACTURER := Anbernic

PRODUCT_GMS_CLIENTID_BASE := android-anbernic

# Fingerprint: keep the stock-style one for Play certification later on.
PRODUCT_BUILD_PROP_OVERRIDES += \
    PRODUCT_NAME=RG34XXSP \
    TARGET_DEVICE=rg34xxsp