#!/system/bin/sh
# Samples init's own service states once a second, because the 5-second restart
# loop left no trace we could read: init's "starting service 'zygote'" lines are
# eaten by the DRM debug flood, and p01..p05 on the card are always the previous
# boot's logcat. init.svc.<name> flipping to "restarting" names the service that
# is actually cycling, which is what decides whether media.codec is the whole
# story or whether something in class main is dying on its own.
#
# Output is small (about 12 KiB for two minutes) and goes to
# /metadata/dsh/timeline.log, which harvest_metadata() now really copies to the
# card as DEBUG/meta-dsh_timeline.log.
LOG=/metadata/dsh/timeline.log
SVCS="zygote zygote_secondary surfaceflinger servicemanager netd media media.codec audioserver"

: > $LOG
echo "# boot_completed then init.svc.* once a second, 120 samples" >> $LOG

i=0
while [ "$i" -lt 120 ]; do
	printf 'bc=%s' "$(getprop sys.boot_completed)" >> $LOG
	for s in $SVCS; do
		printf ' %s=%s' "$s" "$(getprop init.svc.$s)" >> $LOG
	done
	printf '\n' >> $LOG
	i=$((i + 1))
	sleep 1
done
exit 0