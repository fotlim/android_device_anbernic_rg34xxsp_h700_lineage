#!/bin/bash
# Prove that patches/apply.sh reproduces exactly the first_stage_init.cpp the
# card is running, starting from the file git has (337 lines, nothing applied).
#
# HANDOVER 7.2 asks for this every time a patch script changes: the scripts are
# insert-only, so a change that is not picked up leaves the binary byte-identical
# to the previous build and the card quietly tests the old code. That happened
# twice (the health entry, and the empty .so list), so make it a command.
#
#   verify-patch-chain.sh [tree]      default /root/android/lineage
set -e

A=${1:-/root/android/lineage}
REPO=${REPO:-/root/repo}
F=init/first_stage_init.cpp

cd "$A/system/core"
cp "$F" /tmp/fsi.current
sha256sum /tmp/fsi.current | sed 's|/tmp/fsi.current|in the tree now|'

echo "=== reset to the version git has and replay every patch ==="
git checkout -- "$F"
bash "$REPO/patches/apply.sh" "$A" >/tmp/apply.out 2>&1 || {
	echo "!! apply.sh failed:"; tail -5 /tmp/apply.out; exit 1
}
grep -E 'patched|added to|inserted|already' /tmp/apply.out | sed 's/^/   /'

echo
echo "=== compare ==="
sha256sum "$F" /tmp/fsi.current
if cmp -s "$F" /tmp/fsi.current; then
	echo "OK: the patch chain reproduces the tree byte for byte ($(wc -l < "$F") lines)"
	exit 0
fi
echo "!! DIFFERS -- a patch is not picking something up:"
diff /tmp/fsi.current "$F" | head -20
exit 1