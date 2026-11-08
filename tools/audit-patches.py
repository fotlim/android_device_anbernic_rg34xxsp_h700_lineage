#!/usr/bin/env python3
"""Audit whether each ROCKNIX patch actually landed in the kernel tree.

Why: tools/build-kernel.sh applies the series with `git apply --reject` and
then `patch -p1 -F3`. Both silently drop hunks whose context drifted, and the
result still builds, so nothing complains. That already hid two real bugs --
sun50i_planes.o and pwm-sun8i.o were missing from their Makefiles, so the
drivers existed in the tree but were never compiled.

The reliable signal is the ADDED lines: if a hunk landed, its new lines are in
the file. Removed lines are not a usable signal (a line may legitimately
survive elsewhere, and context lines show up as bare "-").

Scope: driver code only. DTS patches target sun50i-h700-anbernic-rg35xx-*.dts,
which this port does not use -- the rg34xx-sp device tree comes from ROCKNIX's
own build -- so their state is irrelevant here.
"""
import os
import re
import sys

PATCHDIR = "/root/rg34xxsp-kernel/patches"
TREE = "/tmp/lk-rocknix"

DIFF = re.compile(r"^diff --git a/(\S+) b/(\S+)")

# Only files that end up in the kernel image or a compiled module.
RELEVANT = ("drivers/", "sound/", "include/linux/", "include/dt-bindings/")


def norm(line):
    """Whitespace-insensitive: hunks land with fuzz and line offsets."""
    return re.sub(r"\s+", " ", line).strip()


def read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def added_lines(patch_text):
    """Map file -> list of added lines, ignoring pure-context markers."""
    files, cur = {}, None
    for line in patch_text.splitlines():
        m = DIFF.match(line)
        if m:
            cur = m.group(2)
            files.setdefault(cur, [])
            continue
        if cur is None or line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            body = line[1:]
            if body.strip():
                files[cur].append(body)
    return files


def audit(patch):
    text = read(patch)
    if text is None:
        return None
    subject = ""
    for line in text.splitlines():
        if line.startswith("Subject:"):
            subject = line[8:].strip()
            break

    missing = {}
    considered = 0
    for path, adds in added_lines(text).items():
        if not path.startswith(RELEVANT) or not adds:
            continue
        considered += 1
        body = read(os.path.join(TREE, path))
        if body is None:
            missing[path] = (adds, "file does not exist")
            continue
        flat = norm(body)
        gone = [a for a in adds if norm(a) not in flat]
        if gone:
            missing[path] = (gone, f"{len(gone)}/{len(adds)} added lines absent")
    return subject, considered, missing


def main():
    names = sorted(n for n in os.listdir(PATCHDIR) if n.endswith(".patch"))
    rows = []
    for name in names:
        res = audit(os.path.join(PATCHDIR, name))
        if res:
            rows.append((name,) + res)

    broken = [r for r in rows if r[3]]
    print(f"patches: {len(names)}   touching driver code: "
          f"{sum(1 for r in rows if r[2])}   broken: {len(broken)}")
    print()

    if broken:
        print("=" * 78)
        print("DRIVER CODE THAT DID NOT LAND")
        print("=" * 78)
        for name, subject, considered, missing in broken:
            print(f"\n{name}")
            print(f"  {subject[:108]}")
            for path, (lines, why) in missing.items():
                print(f"  ! {path}")
                print(f"      {why}")
                for line in lines[:4]:
                    print(f"        | {line.strip()[:96]}")
    else:
        print("All driver-code additions from the series are present.")

    return 0 if not broken else 1


if __name__ == "__main__":
    sys.exit(main())
