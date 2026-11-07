# SPDX-License-Identifier: Apache-2.0
#
# Sourced by build/envsetup.sh -- this is a SHELL script, not a makefile.
# (Boards the maintainer writes by habit: `:=` belongs in .mk files only; here
# it is a syntax error that envsetup prints and then carries on.)
#
# Current trees discover the product through COMMON_LUNCH_CHOICES in
# AndroidProducts.mk; these exports are only conveniences for the shell.
#

export DEVICE=rg34xxsp
export DEVICE_PATH=device/anbernic/rg34xxsp

# 手边没有 lunch 环境时也能直接调：`source vendorsetup.sh && m -j` 之前先 lunch。
# 这里只导出变量，不做 add_lunch_combo（Android 11 已移除那个接口）。
