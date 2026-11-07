#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# collect-facts.sh -- 在原厂 / ROCKNIX 系统上跑，把真机事实采集下来。
#
# 用法（两种都行）：
#   1) 拷到卡上，在机器上执行：  sh /path/collect-facts.sh
#   2) adb 已连：                adb push collect-facts.sh /tmp/ && adb shell sh /tmp/collect-facts.sh
#
# 产物：当前目录下 facts-<日期>.txt + fdt.bin（设备树原件）
# 这个脚本**只读**，不改任何配置，正常跑完自动退出。
#
# 目的：把 docs/HARDWARE.md 里所有 ❓ 变成实测值（Wi-Fi 模块型号、面板批次、
# DDR 类型、背光满量程、手柄轴范围），免得 DTS 里留着猜测。

OUT="facts-$(date +%Y%m%d-%H%M).txt"
: > "$OUT"

sec() { printf '\n\n========== %s ==========\n' "$1" >> "$OUT"; }

# 追加执行一条命令（失败也继续）
r() {
    printf '\n$ %s\n' "$*" >> "$OUT"
    "$@" >> "$OUT" 2>&1 || echo "[rc=$?]" >> "$OUT"
}

# 从 sysfs 读一行（不存在就跳过）
rd() {
    [ -e "$1" ] && printf '%s = %s\n' "$1" "$(cat "$1" 2>/dev/null | head -c 200)" >> "$OUT"
}

sec "身份"
r uname -a
rd /etc/os-release
r cat /etc/os-release
rd /proc/cmdline
r cat /proc/cmdline
rd /proc/device-tree/model
rd /proc/device-tree/compatible

sec "设备树里的兼容字符串"
# 把整棵 device-tree 的 compatible 抓出来，面板/手柄/PMIC 一眼就能看到
grep -r -a -l . /proc/device-tree 2>/dev/null | head -0   # 占位：加快后面 grep 的缓存
find /proc/device-tree -name compatible 2>/dev/null | while read -r f; do
    printf '%s = %s\n' "$f" "$(tr '\0' ' ' < "$f" 2>/dev/null)" >> "$OUT"
done

sec "设备树原件（拿回 PC 后用 dtc 反编译）"
if [ -e /sys/firmware/fdt ]; then
    cp /sys/firmware/fdt ./fdt.bin 2>/dev/null && echo "fdt.bin saved ($(wc -c < ./fdt.bin) bytes)" >> "$OUT"
else
    echo "no /sys/firmware/fdt" >> "$OUT"
fi

sec "dmesg 关键行"
r dmesg
# 上面整份 dmesg 很长，这里再单独摘一遍关心的事件，方便我先看
dmesg 2>/dev/null | grep -Ei 'mmc[0-2]|sdio|panel|drm|tcon|disp|codec|axp|pwm|joypad|rumble|rtw|8723|8821|aic|wifi|bt|bluetooth|rtc|pcf8563|volt|regulator|ddr' >> "$OUT" 2>&1

sec "SDIO / Wi-Fi 模块（最关键：确认模块型号）"
r ls -l /sys/bus/sdio/devices/
for d in /sys/bus/sdio/devices/*; do
    [ -e "$d" ] || continue
    printf '\n-- %s\n' "$d" >> "$OUT"
    for f in vendor device class modalias; do
        [ -e "$d/$f" ] && printf '  %s = %s\n' "$f" "$(cat "$d/$f")" >> "$OUT"
    done
done
r cat /sys/kernel/debug/mmc1/ios 2>/dev/null
rd /sys/class/net/wlan0/address
rd /sys/class/net/wlan0/operstate
r ls /sys/class/ieee80211/

sec "输入设备（手柄、音量、盖子）"
r cat /proc/bus/input/devices
r ls /dev/input/
for d in /sys/class/input/event*; do
    [ -e "$d" ] || continue
    printf '\n-- %s\n' "$d" >> "$OUT"
    for f in name phys uniq; do
        [ -e "$d/device/$f" ] && printf '  %s = %s\n' "$f" "$(cat "$d/device/$f")" >> "$OUT"
    done
    [ -e "$d/device/capabilities/abs" ] && printf '  abs = %s\n' "$(cat "$d/device/capabilities/abs")" >> "$OUT"
    [ -e "$d/device/capabilities/key" ] && printf '  key = %s\n' "$(cat "$d/device/capabilities/key")" >> "$OUT"
done

sec "显示 / 面板 / 背光"
for d in /sys/class/drm/*; do
    [ -e "$d/status" ] || continue
    printf '\n-- %s\n' "$d" >> "$OUT"
    rd "$d/status"
    rd "$d/enabled"
    [ -e "$d/modes" ] && printf '  modes = %s\n' "$(cat "$d/modes")" >> "$OUT"
done
r ls /sys/class/graphics/
rd /sys/class/graphics/fb0/modes
rd /sys/class/graphics/fb0/virtual_size
rd /sys/class/graphics/fb0/stride
for d in /sys/class/backlight/*; do
    [ -e "$d" ] || continue
    printf '\n-- %s\n' "$d" >> "$OUT"
    for f in max_brightness brightness type; do rd "$d/$f"; done
done

sec "电源 / PMIC / 电池 / 温度"
for d in /sys/class/power_supply/*; do
    [ -e "$d" ] || continue
    printf '\n-- %s\n' "$d" >> "$OUT"
    r cat "$d/uevent"
done
for z in /sys/class/thermal/thermal_zone*; do
    [ -e "$z/temp" ] && printf '%s = %s\n' "$z" "$(cat "$z/temp")" >> "$OUT"
done
r cat /proc/meminfo

sec "频率表 / 调频器"
rd /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies
rd /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors
rd /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
rd /sys/devices/platform/soc/1800000.gpu/devfreq/1800000.gpu/available_frequencies
rd /sys/devices/platform/soc/1800000.gpu/devfreq/1800000.gpu/governor

sec "存储布局"
r cat /proc/partitions
r ls -l /dev/mmcblk*
r cat /proc/mounts
command -v lsblk >/dev/null && r lsblk

sec "已加载模块 / 驱动"
r lsmod
r ls /sys/module
r ls /sys/devices/platform/soc/
rd /sys/class/misc/rocknix-singleadc-joypad/dev  2>/dev/null
r cat /sys/devices/platform/rocknix-singleadc-joypad/rumble_enable 2>/dev/null

sec "I2C 总线（PMIC / RTC 应答检查）"
command -v i2cdetect >/dev/null && { for b in 0 1 2 3 4 5; do r i2cdetect -y "$b"; done; } || echo "i2cdetect 未安装（可选）" >> "$OUT"

sec "U-Boot / 启动方式线索"
r dmesg | head -1
[ -e /proc/device-tree/chosen/bootargs ] && printf 'bootargs = %s\n' "$(tr '\0' ' ' < /proc/device-tree/chosen/bootargs)" >> "$OUT"
ls /boot 2>/dev/null >> "$OUT" 2>&1

sec "用户态版本（判断是哪套原厂系统）"
r cat /etc/version 2>/dev/null
r cat /etc/issue 2>/dev/null
r ps

printf '\n\n采集完成：%s\n' "$OUT"
echo "把这个文件（和 fdt.bin，如果生成了）拷回 PC 即可。"
