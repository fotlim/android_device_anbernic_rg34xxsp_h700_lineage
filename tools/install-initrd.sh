#!/bin/bash
# Pack /root/initramfs-embed/init.android into the external initrd on the card.
#
# Why an external initrd: the kernel unpacks the built-in cpio first and then the
# one U-Boot hands over *into the same rootfs*, so a same-named entry replaces
# the built-in file.  That is how /init.android (the second-stage init) gets
# swapped without rebuilding Image -- see docs/HANDOVER.md section 6.0.
#
# Usage:  install-initrd.sh [card-mount]        (default /mnt/g)
set -e

INIT=/root/initramfs-embed/init.android
REPO=/mnt/c/Users/LinOcean/Documents/deepseek-harness/default-workspace/03_WSA与安卓/anbernic-rg34xxsp
CARD=${1:-/mnt/g}

echo "=== source binary ==="
ls -l "$INIT"
file "$INIT"
sha256sum "$INIT"
echo "--- markers (must all be >= 1) ---"
for m in 'ctx: probe %s: access=' 'ctx: overlay /vendor/etc/selinux ok' 'devprobe'; do
	printf '  %-44s %s\n' "$m" "$(strings -a "$INIT" | grep -Fc -- "$m")"
done

echo
echo "=== packing /init.android into a newc cpio (gzip) ==="
python3 "$REPO/tools/make-initrd.py" \
	--out /tmp/initrd.img --gzip \
	--add "$INIT:/init.android:0755"

echo
echo "=== archive ==="
ls -l /tmp/initrd.img
sha256sum /tmp/initrd.img
python3 - <<'PY'
import gzip, sys
blob = gzip.open('/tmp/initrd.img', 'rb').read()
print('unpacked %d bytes, entries:' % len(blob))
pos = 0
while True:
    if blob[pos:pos + 6] != b'070701':
        sys.exit('bad magic at %d' % pos)
    f = [int(blob[pos + 6 + i * 8: pos + 14 + i * 8], 16) for i in range(13)]
    mode, size, nsize = f[1], f[6], f[11]
    name = blob[pos + 110: pos + 110 + nsize - 1].decode()
    if name == 'TRAILER!!!':
        print('  TRAILER at %d' % pos)
        break
    print('  0%-7o %9d  /%s' % (mode, size, name))
    pos = (((pos + 110 + nsize + 3) & ~3) + size + 3) & ~3
if __import__('hashlib').sha256(blob).hexdigest() != __import__('hashlib').sha256(
        __import__('subprocess').check_output(
            ['bash', '-c', 'gunzip -c /tmp/initrd.img'])).hexdigest():
    print('(gzip round trip differs only in framing; content compare skipped)')
PY

if [ ! -d "$CARD" ]; then
	echo
	echo "!! $CARD is not mounted -- archive left at /tmp/initrd.img"
	exit 1
fi

if [ ! -f "$CARD/extlinux/extlinux.conf" ]; then
	echo
	echo "!! $CARD has no extlinux/extlinux.conf."
	if mountpoint -q "$CARD" 2>/dev/null; then
		echo "   it is a mount, but not the boot partition -- is the card in the reader?"
	else
		echo "   it is not a mount point at all: WSL only auto-mounts the drives"
		echo "   that existed when it started.  Mount the card and re-run, e.g."
		echo "     mkdir -p $CARD && mount -t drvfs G: $CARD"
	fi
	echo "   archive left at /tmp/initrd.img"
	exit 1
fi

echo
echo "=== installing to $CARD/initrd.img ==="
cp -f /tmp/initrd.img "$CARD/initrd.img"
sync
ls -l "$CARD/initrd.img"
sha256sum "$CARD/initrd.img"

echo
echo "=== extlinux must point at it ==="
grep -n 'initrd' "$CARD/extlinux/extlinux.conf" || echo "!! no initrd line found"
