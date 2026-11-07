#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# verify-modules.py -- 检查 device.mk 里列的 PRODUCT_PACKAGES 在这个 AOSP/Lineage
# 树里到底存不存在。
#
# 为什么需要它：
#   PRODUCT_PACKAGES 里写错一个模块名，`mka bacon` 会在几分钟后以一个很难读的
#   "package X not found" 失败；而 Android 11 与 13/15 的 HAL 模块名差别很大
#   （composer 上限 2.1 vs 2.4、allocator 2.0/3.0/4.0 混用、service 后缀不同），
#   照着网上 13 时代的设备树抄，十有八九前半段就编不过。
#
# 用法：
#   tools/verify-modules.py <aosp-root> [--from device.mk ...]
#   不给 --from 时检查脚本里列举的默认清单。
#
# 判据：模块名出现在某个 Android.bp 的 `name: "X"`（或 cc_*_defaults 等），
# 或某个 Android.mk 的 `LOCAL_MODULE := X`（=、?= 都算）。

import os
import re
import sys

BP_NAME = re.compile(r'\bname\s*:\s*"([^"]+)"')
MK_MODULE = re.compile(r'^\s*LOCAL_MODULE\s*(?::=|\?=|=)\s*([^\s#]+)', re.M)


def collect(root, skipped_dirs=('.repo', 'out', '.git')):
    names = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skipped_dirs]
        for fn in filenames:
            if fn == 'Android.bp':
                try:
                    with open(os.path.join(dirpath, fn), encoding='utf-8',
                              errors='replace') as f:
                        names.update(BP_NAME.findall(f.read()))
                except OSError:
                    pass
            elif fn == 'Android.mk':
                try:
                    with open(os.path.join(dirpath, fn), encoding='utf-8',
                              errors='replace') as f:
                        names.update(MK_MODULE.findall(f.read()))
                except OSError:
                    pass
    return names


DEFAULT = [
    # graphics
    'android.hardware.graphics.composer@2.2-impl',
    'android.hardware.graphics.composer@2.2-service',
    'hwcomposer.drm_minigbm',
    'android.hardware.graphics.allocator@4.0-service.minigbm',
    'android.hardware.graphics.mapper@4.0-impl.minigbm',
    # audio / wifi / bt (仅列候选，是否可用以此脚本输出为准)
    'android.hardware.audio.service',
    'android.hardware.audio@5.0-impl',
    'android.hardware.audio.effect@5.0-impl',
    'android.hardware.wifi@1.0-service',
    'android.hardware.bluetooth@1.1-service',
    # 基础
    'libdrm',
    'libtinyalsa',
    'zramd',
]


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: verify-modules.py <aosp-root> [module ...]')
    root = os.path.abspath(sys.argv[1])
    wanted = sys.argv[2:] or DEFAULT
    have = collect(root)
    missing = [m for m in wanted if m not in have]
    for m in wanted:
        print('  %-58s %s' % (m, 'OK' if m in have else 'MISSING'))
    print('\n  %d/%d 存在' % (len(wanted) - len(missing), len(wanted)))
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
