#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# dedupe-patch-artifacts.py -- 清理补丁重复落地造成的同义块。
#
# 为什么需要它：
#   ROCKNIX 的 0002-rg35xx-enable-HDMI-LCD.patch 里，同一段新增内容出现了两次
#   （很可能是从「改了两次再 diff」的树上导出来的）。`git apply` 逐个 hunk 套用，
#   第一个 hunk 把块加进去之后，第二个 hunk 的上下文反而还匹配得上 —— 于是同一个
#   `static const struct ... = { ... };` 被塞进了文件两遍，编译直接
#   `error: redefinition of 'xxx'`。
#
#   逐个打补丁去改不现实（换内核版本、换补丁集就会重来），所以这里做通用清理：
#   对补丁改动过的文件，找出「顶格定义、名字相同、内容逐字节相同」的重复块，
#   只保留最前面那一份，并打印出丢了什么，方便事后核对。
#
# 用法：
#   tools/dedupe-patch-artifacts.py <kernel-src>
#
# 它只删**完全相同的重复定义**（同名 + 同内容），这类重复在任何 C 文件里都是
# 编译错误，因此删除不会改变语义。

import io
import os
import re
import subprocess
import sys

# 顶格定义 + 名字（可带数组维度）+ 初始化列表：
# 覆盖 static const struct X y = {...}; / const u32 z[] = {...};
BLOCK = re.compile(
    r'(?m)^(?:static\s+)?(?:const\s+)?'
    r'(?:struct|union|enum)?\s*[\w\s\*]*?\b(\w+)\s*'
    r'(?:\[[^\]]*\])?\s*=\s*\{'
    r'.*?^\};[ \t]*$',
    re.S,
)


def modified_files(src):
    """补丁改过的文件（相对路径）。工作区可能是脏的，用 diff 名字列表。"""
    try:
        out = subprocess.run(
            ['git', '-C', src, 'diff', '--name-only', 'HEAD'],
            capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return []
    return [f for f in out.split('\n') if f.endswith(('.c', '.h'))]


def dedupe(path):
    s = io.open(path, encoding='utf-8', errors='surrogateescape').read()
    seen, out, pos, dropped = {}, [], 0, []
    for m in BLOCK.finditer(s):
        name, body = m.group(1), m.group(0)
        out.append(s[pos:m.start()])
        pos = m.end()
        if name in seen and seen[name] == body:
            dropped.append(name)
            continue
        seen[name] = body
        out.append(body)
    out.append(s[pos:])
    if not dropped:
        return []
    io.open(path, 'w', encoding='utf-8', errors='surrogateescape',
            newline='').write(''.join(out))
    return dropped


def main():
    if len(sys.argv) != 2:
        sys.exit('usage: dedupe-patch-artifacts.py <kernel-src>')
    src = os.path.abspath(sys.argv[1])
    total = 0
    for rel in modified_files(src):
        dropped = dedupe(os.path.join(src, rel))
        if dropped:
            total += len(dropped)
            print('  %-60s 去重 %d 处: %s' % (rel, len(dropped), ', '.join(dropped)))
    if total:
        print('  合计去掉 %d 个重复块' % total)
    else:
        print('  没有重复块')
    return 0


if __name__ == '__main__':
    sys.exit(main())