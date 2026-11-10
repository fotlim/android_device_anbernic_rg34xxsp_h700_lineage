#!/bin/sh
# Apply the first-stage fstab patch to a Lineage tree.
#   apply.sh /root/android/lineage
set -e
TREE=${1:-/root/android/lineage}
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$TREE/system/core"
if grep -q 'PublishRamdiskFstab' init/first_stage_init.cpp; then
	echo "already applied"
	exit 0
fi
git apply "$HERE/init-first-stage-publish-ramdisk-fstab.patch"
echo "applied"
