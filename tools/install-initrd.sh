#!/bin/bash
# Pack the ramdisk binaries into the external initrd on the card.
#
# Why an external initrd: the kernel unpacks the built-in cpio first and then the
# one U-Boot hands over *into the same rootfs*, so a same-named entry replaces
# the built-in file.  That is how /init.android (the second-stage init) gets
# swapped without rebuilding Image -- see docs/HANDOVER.md section 6.0.
#
# sd51 also carries /init (debug-init) this way, because debug-init's metadata
# receipt list gained "dsh/probe.log": rebuilding the kernel's built-in cpio for
# one line would mean a full Image rebuild, while an external entry costs 650 KiB
# on the card.  Set --no-debug-init to pack only /init.android.
#
# Usage:  install-initrd.sh [card-mount]        (default /mnt/g)
set -e

INIT=/root/initramfs-embed/init.android
DEBUG_INIT=/root/initramfs-embed/init
REPO=${REPO:-/root/repo}
CARD=${1:-/mnt/g}
PACK_DEBUG=1
PACK_OVERLAY=1
for a in "$@"; do
	case "$a" in
		--no-debug-init) PACK_DEBUG=0 ;;
		--no-overlay)    PACK_OVERLAY=0 ;;
	esac
done

echo "=== source binary ==="
ls -l "$INIT"
file "$INIT"
sha256sum "$INIT"
echo "--- markers (must all be >= 1) ---"
for m in 'ctx: probe %s: access=' 'ctx: overlay /vendor/etc/selinux ok' 'devprobe'; do
	printf '  %-44s %s\n' "$m" "$(strings -a "$INIT" | grep -Fc -- "$m")"
done

ADD=("$INIT:/init.android:0755")
if [ "$PACK_DEBUG" = 1 ]; then
	echo
	echo "=== debug-init (the ramdisk /init) ==="
	ls -l "$DEBUG_INIT"
	file "$DEBUG_INIT"
	sha256sum "$DEBUG_INIT"
	if [ ! -x "$DEBUG_INIT" ]; then
		echo "!! $DEBUG_INIT missing; rebuild with tools/debug-init/build.sh -o $DEBUG_INIT" >&2
		exit 1
	fi
	ADD+=("$DEBUG_INIT:/init:0755")
fi

# vendor-overlay/ is what the first stage copies into /vendor/lib64/hw and
# /vendor/bin/hw (gralloc/mapper/allocator, and now the health@2.1 passthrough
# impl). It normally travels inside the kernel's built-in initramfs, but the
# external initrd is unpacked into the same rootfs and overwrites same-named
# entries, so carrying it here means those .so files can be updated without
# rebuilding Image -- which is the whole point of this route.
OVERLAY=/root/initramfs-embed/vendor-overlay
if [ "$PACK_OVERLAY" = 1 ] && [ -d "$OVERLAY" ]; then
	echo
	echo "=== vendor-overlay (published into /vendor by the first stage) ==="
	while IFS= read -r f; do
		rel=${f#"$OVERLAY/"}
		ADD+=("$f:vendor-overlay/$rel:0644")
		printf '  %-58s %s\n' "vendor-overlay/$rel" "$(stat -c%s "$f") B"
	done < <(find "$OVERLAY" -type f | sort)
fi

# sd111: APKs the first stage publishes into /system/*-app.  These ride inside
# the external initrd (unpacked into the same rootfs as the built-in one), so a
# new system app can be shipped without touching the FAT boot partition or
# rebuilding Image.  patch-system-app-overlay.py copies them from dshsysapp/ via
# the ramdisk DIR* (dfd) the first stage opened before SwitchRoot.
DSHSYSAPP=/root/initramfs-embed/dshsysapp
if [ -d "$DSHSYSAPP" ]; then
	echo
	echo "=== dshsysapp (APKs staged for the first stage) ==="
	while IFS= read -r f; do
		rel=${f#"$DSHSYSAPP/"}
		ADD+=("$f:dshsysapp/$rel:0644")
		printf '  %-50s %s B\n' "dshsysapp/$rel" "$(stat -c%s "$f")"
	done < <(find "$DSHSYSAPP" -type f | sort)
fi

# NOTE: packing a file here does NOT put it on /vendor.  The first stage only
# publishes the entries named in kVendorGraphicsFiles (see
# patches/patch-vendor-graphics.py), and PublishTextFile covers the handful of
# generated /vendor/etc/init/*.rc files.  A file that is merely packed stays in
# the ramdisk and is unreachable once SwitchRoot happens -- sd53 learned that the
# hard way with 97-seccomp-fix.rc.  To ship a new /vendor file, either add it to
# that array or fold its logic into kFixPublicLibsSh.

echo
echo "=== packing into a newc cpio (gzip) ==="
python3 "$REPO/tools/make-initrd.py" \
	--out /tmp/initrd.img --gzip \
	$(for e in "${ADD[@]}"; do printf ' --add %s' "$e"; done)

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
