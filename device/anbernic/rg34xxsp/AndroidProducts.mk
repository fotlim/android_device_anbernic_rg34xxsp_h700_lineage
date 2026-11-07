#
# SPDX-License-Identifier: Apache-2.0
#
# Product definitions for the Anbernic RG34XXSP (Allwinner H700).
#

PRODUCT_MAKEFILES := \
    $(LOCAL_DIR)/lineage_rg34xxsp.mk

COMMON_LUNCH_CHOICES := \
    lineage_rg34xxsp-userdebug \
    lineage_rg34xxsp-user \
    lineage_rg34xxsp-eng
