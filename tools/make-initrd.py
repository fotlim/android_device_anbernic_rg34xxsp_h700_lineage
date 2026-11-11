#!/usr/bin/env python3
"""Build a cpio (newc) initrd -- no compiler, no root needed.

The kernel unpacks the built-in initramfs first and then, if U-Boot handed it
one, the external initrd *into the same rootfs*, so any file the initrd carries
replaces the copy that came from the built-in ramdisk.  That makes an external
initrd a cheap way to swap a single ramdisk file (e.g. /init.android) without
rebuilding the kernel Image.

    tools/make-initrd.py --out initrd.img /path/on/host/init.android:/init.android:0755

Prints the entry list and the archive size, and refuses to emit an archive the
kernel would reject (bad name length, non-4-byte padding, missing TRAILER!!!).
"""

import argparse
import gzip
import os
import sys


def newc_entry(ino, name, data, mode, mtime=0, uid=0, gid=0):
    name_b = name.encode() + b"\0"
    fields = [
        ino,            # ino
        mode,           # mode
        uid,
        gid,
        1,              # nlink
        mtime,
        len(data),      # filesize
        0, 0,           # devmajor, devminor
        0, 0,           # rdevmajor, rdevminor
        len(name_b),    # namesize (includes the trailing NUL)
        0,              # check
    ]
    header = b"070701" + b"".join(b"%08X" % f for f in fields)
    assert len(header) == 110, len(header)
    out = header + name_b
    out += b"\0" * (-len(out) % 4)
    out += data
    out += b"\0" * (-len(out) % 4)
    return out


def build(entries):
    """entries: list of (archive_path, data, mode)"""
    blob = b""
    ino = 1
    for path, data, mode in entries:
        blob += newc_entry(ino, path, data, mode)
        ino += 1
    blob += newc_entry(ino, "TRAILER!!!", b"", 0)
    return blob


def parse_spec(spec):
    """local:archive[:mode] -- mode is octal like 0755.

    Splits from the right so that Windows paths such as C:\\tmp\\init.android
    (which contain a colon of their own) still work.
    """
    head, sep, last = spec.rpartition(":")
    if not sep:
        raise SystemExit(f"bad --add value {spec!r}, want local:archive[:mode]")
    if head and all(c in "01234567" for c in last) and 3 <= len(last) <= 4:
        mode = last
        local, sep2, archive = head.rpartition(":")
        if not sep2 or not local or not archive:
            raise SystemExit(f"bad --add value {spec!r}, want local:archive[:mode]")
    else:
        mode = "0644"
        local, archive = head, last
    if not local or not archive:
        raise SystemExit(f"bad --add value {spec!r}, want local:archive[:mode]")
    with open(local, "rb") as f:
        data = f.read()
    return archive.lstrip("/"), data, int(mode, 8)


def verify(blob):
    """Re-read our own archive the way the kernel does, and report."""
    pos = 0
    entries = 0
    while True:
        if blob[pos:pos + 6] != b"070701":
            raise SystemExit(f"bad magic at {pos}")
        f = [int(blob[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        filesize, namesize = f[6], f[11]
        name = blob[pos + 110:pos + 110 + namesize - 1].decode()
        if name == "TRAILER!!!":
            return entries, (pos + 110 + namesize + 3) & ~3
        body = (pos + 110 + namesize + 3) & ~3
        pos = (body + filesize + 3) & ~3
        entries += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gzip", action="store_true", help="gzip the archive")
    ap.add_argument("--add", action="append", default=[], metavar="LOCAL:ARCHIVE[:MODE]")
    args = ap.parse_args()

    if not args.add:
        raise SystemExit("nothing to add")

    entries = [parse_spec(s) for s in args.add]
    blob = build(entries)
    count, end = verify(blob)
    print(f"entries={count} archive={len(blob)} bytes (re-read end={end})")

    payload = gzip.compress(blob, 9) if args.gzip else blob
    with open(args.out, "wb") as f:
        f.write(payload)
    for path, data, mode in entries:
        print(f"  {mode:04o} {len(data):>9}  /{path}")
    print(f"wrote {args.out} ({len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
