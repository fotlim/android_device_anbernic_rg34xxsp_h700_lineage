#
# SPDX-License-Identifier: Apache-2.0
#
# rootdir/Android.mk -- RG34XXSP 的 rootdir 文件如何打包
#
# 两个位置很容易搞错，而搞错的后果不是编译失败，是开机第一屏就停住：
#
#   * fstab 必须在 **boot 分区的 ramdisk** 里（TARGET_RAMDISK_OUT）。
#     first-stage init 找的是 fstab.<ro.hardware> / fstab.<ro.product.device>，
#     它跑的时候 super/data 都还没挂载，根本看不到 /vendor。
#     把 fstab 只放进 /vendor/etc 是很多移植"卡在开机 logo"的直接原因。
#
#   * ueventd 规则要叫 `ueventd.rc` 放在 /vendor 根上，init 才会读。文件名不能带
#     设备后缀，所以用 LOCAL_MODULE_STEM 改目标文件名。
#
LOCAL_PATH := $(call my-dir)

# fstab -> ramdisk（文件名 fstab.rg34xxsp，与 ro.product.device 对齐）
include $(CLEAR_VARS)
LOCAL_MODULE       := fstab.rg34xxsp_ramdisk
LOCAL_MODULE_STEM  := fstab.rg34xxsp
LOCAL_MODULE_TAGS  := optional
LOCAL_MODULE_CLASS := ETC
LOCAL_SRC_FILES    := ../fstab.rg34xxsp
LOCAL_MODULE_PATH  := $(TARGET_RAMDISK_OUT)
include $(BUILD_PREBUILT)

# ueventd 规则 -> /vendor/ueventd.rc
include $(CLEAR_VARS)
LOCAL_MODULE       := ueventd.rg34xxsp.rc
LOCAL_MODULE_STEM  := ueventd.rc
LOCAL_MODULE_TAGS  := optional
LOCAL_MODULE_CLASS := ETC
LOCAL_SRC_FILES    := ../init/ueventd.rg34xxsp.rc
LOCAL_MODULE_PATH  := $(TARGET_OUT_VENDOR)
include $(BUILD_PREBUILT)