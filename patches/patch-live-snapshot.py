#!/usr/bin/env python3
"""Copy this boot's logcat and receipts onto the card, so a dump is not stale.

Everything debug-init harvests at startup is by definition the PREVIOUS boot's:
harvest_metadata() and harvest_data() run before this boot has written anything,
and the only filesystems Windows can read afterwards are the FAT partitions on
the card. That mismatch cost several rounds of analysis on stale evidence.

A class-core oneshot sleeps until the boot has had a minute to fail, mounts the
card's boot partition read-write, writes DEBUG/live-*, and unmounts. class core
rather than late_start on purpose: late_start waits for vold.decrypt and this
device never gets that far.

Boot 9ac27bf6 showed two defects in the first version, both fixed here:

  * `logcat -d`, not the /data/local/tmp file. That file is written by a logcat
    service a later patch replaced, so it has been stale since boot 702a1e18 --
    the snapshot copied 1.5 MiB of it and every line was an old boot.
  * it now also copies /system/etc/seccomp_policy/mediacodec.policy and
    /system/etc/init/netd.rc, the two files whose state kept being guessed at,
    so one dump proves whether the early-init fix and the rc override landed.
    (Boot 04213c6b is why both paths are written down twice in comments: the
    first version asked /vendor/etc/init/netd.rc, which never exists, and
    /metadata/dsh/first-stage-publish.txt, which lives one level up.)

  * later still, DEBUG/live-preload.txt records the three numbers that decide
    whether zygote can survive its preload: renderscript lines left in
    preloaded-classes (must be 0), onrestart lines in netd.rc, and the seccomp
    policy's line/include counts.

This script can REGENERATE its literals: an insert-only patch silently keeps the
first version forever once its marker is in the file, which is how the stale
`tail -c` line survived. Both literals are re-sliced on every run.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"
MARKER = "PublishLiveSnapshot"

LIVE_SH = """#!/system/bin/sh
# One shot, about a minute in: copy THIS boot's logcat and receipts to the card
# under DEBUG/live-*. Everything else in DEBUG/ is the previous boot.
LOG=/metadata/dsh/snapshot.log
CARD=/mnt/dshcard
mkdir -p $CARD /metadata/dsh 2>/dev/null
echo "snapshot: start" > $LOG
# sd92: SystemUI declares dangerous (runtime) permissions and a fresh /data has
# granted none of them. Boot 01900a61 ends like this, twelve times:
#   FATAL EXCEPTION: main
#   Process: com.android.systemui
#   Unable to create service com.android.systemui.keyguard.KeyguardService:
#     InflateException: Binary XML file line #64 in
#     com.android.systemui:layout/super_notification_shade: uid=10046 needs
#     permission android.permission.READ_CONTACTS to read
#     lock_screen_owner_info_enabled for user 0
# No privapp allowlist can fix that one -- it is a dangerous permission, not a
# privileged one -- so grant the set SystemUI plausibly reads. The grant is
# persistent in /data; this matters only until /data is next wiped, which is
# what the sd91-shaped default-permissions XML is for.
for perm in READ_CONTACTS READ_PROFILE READ_EXTERNAL_STORAGE GET_ACCOUNTS ACCESS_COARSE_LOCATION; do
    i=0
    while [ $i -lt 40 ]; do
        if pm grant com.android.systemui android.permission.$perm >/dev/null 2>&1; then
            echo "snapshot: granted $perm" >> $LOG
            break
        fi
        i=$((i+1))
        sleep 2
    done
    if [ $i -ge 40 ]; then
        echo "snapshot: could NOT grant $perm" >> $LOG
    fi
done
sleep 45
if ! mount -t vfat -o rw /dev/block/mmcblk0p1 $CARD 2>>$LOG; then
    echo "snapshot: cannot mount the boot partition" >> $LOG
    exit 0
fi
mkdir -p $CARD/DEBUG 2>>$LOG
for f in fix.log probe.log public.libraries.txt snapshot.log; do
    [ -f /metadata/dsh/$f ] && cp /metadata/dsh/$f $CARD/DEBUG/live-$f 2>>$LOG
done
# The first stage writes this one at the metadata root, not under dsh/ (boot
# 04213c6b is missing it precisely because the old line looked in dsh/).
[ -f /metadata/first-stage-publish.txt ] && cp /metadata/first-stage-publish.txt $CARD/DEBUG/live-first-stage-publish.txt 2>>$LOG
# The three files the last rounds kept getting wrong, in the state this boot
# actually used: the policy must show 61 lines / 0 includes, netd.rc must be the
# rewritten one (no 'onrestart restart zygote'), and the preload list must have
# dropped its android.renderscript lines or zygote will die again.
cp /system/etc/seccomp_policy/mediacodec.policy $CARD/DEBUG/live-mediacodec.policy 2>>$LOG
cp /system/etc/init/netd.rc $CARD/DEBUG/live-netd.rc 2>>$LOG
{
    echo "preloaded-classes: $(wc -l < /system/etc/preloaded-classes) lines, $(grep -c android.renderscript /system/etc/preloaded-classes) renderscript lines"
    echo "netd.rc onrestart-restart-zygote: $(grep -c onrestart /system/etc/init/netd.rc) lines"
    echo "mediacodec.policy: $(wc -l < /system/etc/seccomp_policy/mediacodec.policy) lines, $(grep -c @include /system/etc/seccomp_policy/mediacodec.policy) includes"
    echo "systemserver-classpath rc files: $(grep -l SYSTEMSERVERCLASSPATH /vendor/etc/init/*.rc 2>/dev/null | wc -l)"
    echo "memtrack stub module: $(ls /vendor/lib64/hw/memtrack.default.so 2>/dev/null | wc -l)"
    echo "memtrack service rc: $(grep -c 'IMemtrack default' /vendor/etc/init/android.hardware.memtrack@1.0-service.rc 2>/dev/null)"
    echo "prop.default memfd: $(grep -c sys.use_memfd /system/etc/prop.default 2>/dev/null)"
    echo "services odex shadowed: $(grep -c 'stale preopt' /system/framework/oat/arm64/services.odex 2>/dev/null)"
} > $CARD/DEBUG/live-preload.txt 2>>$LOG
# -d, and not /data/local/tmp/dsh-android.log: logcat's own ring holds THIS boot,
# while that file has been stale since boot 702a1e18 (the service writing it was
# replaced by a later patch). 1.5 MiB keeps a 64 MiB card safe.
logcat -d -b main -b system -b crash -v threadtime 2>/dev/null | tail -c 1572864 > $CARD/DEBUG/live-logcat.txt 2>>$LOG
# 45 s is too early to see the interesting half. Boot 18c6c7be reached
# "SystemServer: Entered the Android system server!" at 47 s and the snapshot
# stopped exactly there, so the failure (or the stall) after it was invisible.
# Wait another 75 s and take a second sample: services that started, a hang or a
# watchdog kill all show up in this window, and the property table answers
# "which of them is actually running" even when logcat has lost the lines.
sleep 75
logcat -d -b main -b system -b crash -b events -v threadtime 2>/dev/null | tail -c 1048576 > $CARD/DEBUG/live-logcat-late.txt 2>>$LOG
getprop 2>/dev/null | grep -E 'init\\.svc\\.|boot_completed|dev\\.bootcomplete|sys\\.use_memfd|vndk\\.version' > $CARD/DEBUG/live-props.txt 2>>$LOG
ps -A 2>/dev/null | grep -E 'zygote|system_server|surfaceflinger|bootanim|netd|installd|audioserver|media' > $CARD/DEBUG/live-ps.txt 2>>$LOG
# 已注册的 binder 服务：system_server 卡在哪个 startService，这张表比日志直接
service list 2>/dev/null > $CARD/DEBUG/live-services.txt 2>>$LOG
# sd84: boot 9300494a has 163 services and a long-lived system_server, but the
# panel still shows debug-init's last text. `bootanim=running` only proves the
# process exists, not that a frame was presented. Capture the framework's actual
# display state instead of guessing from logcat (whose early SF lines rotate out).
timeout 12 dumpsys SurfaceFlinger > $CARD/DEBUG/live-sf-dump.txt 2>&1
timeout 12 dumpsys display > $CARD/DEBUG/live-display-dump.txt 2>&1
timeout 12 dumpsys activity top > $CARD/DEBUG/live-activity-top.txt 2>&1
timeout 12 dumpsys window > $CARD/DEBUG/live-window-dump.txt 2>&1
getprop > $CARD/DEBUG/live-allprops.txt 2>>$LOG
{
  echo "===== DRM connectors ====="
  for d in /sys/class/drm/card*-*; do
      [ -d "$d" ] || continue
      echo "--- $d"
      for f in status enabled dpms modes; do
          [ -f "$d/$f" ] && { echo "[$f]"; cat "$d/$f"; }
      done
  done
  echo "===== /proc/fb ====="; cat /proc/fb 2>&1
  echo "===== boot animation media ====="
  ls -l /system/media /system/media/bootanimation.zip \
        /product/media/bootanimation.zip 2>&1
  echo "===== boot/display properties ====="
  getprop | grep -E 'boot|display|surfaceflinger|hwc|gralloc|sf\.'
} > $CARD/DEBUG/live-display-state.txt 2>>$LOG
# sd85: the boot deadlock. WindowManagerService.checkBootAnimationCompleteLocked()
# polls SystemService.isRunning("bootanim") every 50 ms and refuses to continue
# while it answers running; in boot 9300494a bootanimation (pid 452) sat in
# binder_thread_read forever -- its animation thread had already exited (0 layers,
# FramebufferSurface frame-counter 0, flips 0) while main() stayed in
# joinThreadPool(), which Thread::run() cannot detect. So enableScreenAfterBoot
# never ran, sys.boot_completed was never set, and SystemUI/Launcher never started.
#
# These captures answer *why* the animation thread died, and whether a home
# activity exists at all, without another round of guessing.
{
  echo "===== debug.sf.nobootanimation ====="; getprop debug.sf.nobootanimation
  echo "===== init.svc.bootanim ====="; getprop init.svc.bootanim
  echo "===== resolve HOME ====="
  cmd package resolve-activity --brief -a android.intent.action.MAIN \
      -c android.intent.category.HOME 2>&1
  echo "===== query HOME ====="
  cmd package query-activities --brief -a android.intent.action.MAIN \
      -c android.intent.category.HOME 2>&1 | head -20
  echo "===== launcher packages ====="
  pm list packages 2>/dev/null | grep -iE 'launcher|systemui|setupwizard'
  echo "===== dumpsys activity activities (head) ====="
  timeout 10 dumpsys activity activities 2>&1 | head -60
} > $CARD/DEBUG/live-home-state.txt 2>>$LOG
# Run the animation by hand and keep its own logs: readyToRun() failures are only
# visible as ALOG output, and the in-boot ones have already rotated out of the
# ring buffer by snapshot time.
timeout 8 /system/bin/bootanimation > $CARD/DEBUG/live-bootanim-run.txt 2>&1
echo "exit=$?" >> $CARD/DEBUG/live-bootanim-run.txt
logcat -d -b main -b system -s BootAnimation -v threadtime 2>/dev/null \
    | tail -c 65536 > $CARD/DEBUG/live-bootanim-log.txt 2>>$LOG
# sd86: bootanim no longer deadlocks, but the framework still refuses to start
# Home: RootWindowContainer.resolveHomeActivity() -> resolveIntent(HOME, user 0)
# returns null, and ActivityThread already complains "(user not unlocked)".
# The launcher itself is fine (system_ext/priv-app/TrebuchetQuickStep, package
# com.android.launcher3, QuickstepLauncher enabled=true with MAIN/HOME/DEFAULT),
# so the gate is the user state: StorageManagerService.isUserKeyUnlocked() only
# consults mLocalUnlockedUsers, which is appended only when unlockUserKey()
# succeeds. Ask the system directly instead of inferring from logs, and try the
# unlock so its stderr names the reason.
{
  echo "===== am get-current-user ====="; am get-current-user 2>&1
  echo "===== cmd user (help) ====="; cmd user 2>&1 | head -25
  echo "===== dumpsys user ====="; timeout 10 dumpsys user 2>&1 | head -60
  echo "===== ce_available / user props ====="
  getprop | grep -E 'ce_available|sys\.user|vold\.' 
  echo "===== /data/user + data_ce ====="
  ls -la /data/user 2>&1 | head -10
  ls -la /data_mirror/data_ce 2>&1 | head -10
  echo "===== am unlock-user 0 (stderr says why) ====="
  am unlock-user 0 2>&1
  echo "rc=$?"
  echo "===== cmd user start-user 0 ====="
  cmd user start-user 0 2>&1
  echo "rc=$?"
  echo "===== resolve HOME as user 0 ====="
  cmd package resolve-activity --brief --user 0 \
      -a android.intent.action.MAIN -c android.intent.category.HOME 2>&1
} > $CARD/DEBUG/live-user-state.txt 2>>$LOG
# vold / storage / user plumbing logs, from every buffer (they scrolled out of
# the trimmed main ring before the snapshot ran).
logcat -d -b all -s vold -s Vold -s StorageManagerService -s UserController \
       -s StorageUserManager -s vold_storage -v threadtime 2>/dev/null \
    | tail -c 131072 > $CARD/DEBUG/live-vold-log.txt 2>>$LOG
# sd70 起 vendor.hwcomposer-2-2 每轮退出 status 1，而且它自己的 log 完全没进
# logcat。sd74：先记环境（设备节点、库、ro.hardware*、vndk 目录），再**前台**
# 跑一次这个 HAL（timeout 3 s），把 stdout/stderr 与退出码一起落盘：
#   exit=124 → 它在 joinRpcThreadpool 里活着（手动能跑通，问题在 init 的环境）
#   exit=1   → 它自己也失败，stderr 里通常有 linker 或模块的错
{ echo "===== ls -l /dev/dri ====="; ls -l /dev/dri 2>&1
  echo "===== ls -l /dev/graphics ====="; ls -l /dev/graphics 2>&1
  echo "===== /vendor/lib64/hw ====="; ls /vendor/lib64/hw 2>&1
  echo "===== /vendor/bin/hw ====="; ls /vendor/bin/hw 2>&1
  echo "===== composer rc files ====="
  for f in /vendor/etc/init/android.hardware.graphics.composer@2.1-service.rc \
           /vendor/etc/init/android.hardware.graphics.composer@2.2-service.rc; do
      echo "--- $f"
      cat "$f" 2>&1 | head -12
  done
  echo "===== composer service states ====="
  getprop init.svc.vendor.hwcomposer-2-1; getprop init.svc.vendor.hwcomposer-2-2
  echo "===== props ====="; getprop ro.hardware; getprop ro.hardware.hwcomposer; getprop ro.hardware.gralloc
  echo "===== vndk ====="; ls -d /system/lib64/vndk* 2>&1; } > $CARD/DEBUG/live-hwc.txt 2>>$LOG
# sd75: the probe walks hw_get_module -> methods->open -> getCapabilities ->
# getFunction and prints each step, because none of the HAL's own ALOG output
# reaches logcat on this device. Then run both composer versions in the
# foreground: 124 means it stayed alive in joinRpcThreadpool, 1 means it failed
# the same way init saw, and its stderr is the only place a linker error shows.
timeout 5 /vendor/bin/hw/dsh-hwcprobe > $CARD/DEBUG/live-hwc-probe.txt 2>&1
echo "exit=$?" >> $CARD/DEBUG/live-hwc-probe.txt
# sd93: SwiftShader vs minigbm. SurfaceFlinger dies inside
# sw::Renderer::executeTask(int)+324 the first time it composites a layer, and
# the probe walks the stack in the order that separates the two causes. 20 s:
# the JIT compile of the two shaders is the slow part on a Cortex-A53.
timeout 25 /vendor/bin/hw/dsh-glprobe > $CARD/DEBUG/live-gl-probe.txt 2>&1
echo "exit=$?" >> $CARD/DEBUG/live-gl-probe.txt
# sd95: the gralloc import test. /system/bin because AHardwareBuffer is in
# libandroid and this tree has no vendor variant of it. This is the one step the
# vendor probe could not reach, and the only remaining suspect for SF's crashes.
timeout 25 /system/bin/dsh-glprobe2 > $CARD/DEBUG/live-ahb-probe.txt 2>&1
echo "exit=$?" >> $CARD/DEBUG/live-ahb-probe.txt
# sd96: capture logcat AGAIN, after the probes. sd95's AHB probe died with
# exit=139 and left no backtrace anywhere, because the logcat above was copied
# before the probe ran. This slice is where the tombstone lands -- and if it
# shows sw::Renderer::executeTask(int) like SurfaceFlinger's own crash, the
# attribution is airtight.
logcat -d -b main -b system -b crash -v threadtime 2>/dev/null | tail -c 262144 > $CARD/DEBUG/live-logcat-after.txt 2>>$LOG
timeout 5 /vendor/bin/hw/android.hardware.graphics.composer@2.1-service > $CARD/DEBUG/live-hwc21-run.txt 2>&1
echo "exit=$?" >> $CARD/DEBUG/live-hwc21-run.txt
timeout 3 /vendor/bin/hw/android.hardware.graphics.composer@2.2-service > $CARD/DEBUG/live-hwc-run.txt 2>&1
echo "exit=$?" >> $CARD/DEBUG/live-hwc-run.txt
# sd100: panfork's first frames are slow on a Cortex-A53 -- SystemUI logged
# "OpenGLRenderer: Davey! duration=900ms / 1271ms" -- so a 2-3 minute window can
# simply be too early. Sample again at ~5 minutes: if sys.boot_completed appears
# here, the boot works and was only slow; if it is still absent, enable_screen is
# genuinely stuck and the window/sf dumps below say what it waits for.
sleep 170
getprop > $CARD/DEBUG/live-allprops-late.txt 2>>$LOG
ps -A 2>/dev/null | grep -E 'systemui|launcher|system_server|surfaceflinger' > $CARD/DEBUG/live-ps-late.txt 2>>$LOG
timeout 12 dumpsys window > $CARD/DEBUG/live-window-late.txt 2>&1
timeout 12 dumpsys SurfaceFlinger > $CARD/DEBUG/live-sf-late.txt 2>&1
# sd101: the boot never finishes even though the display is ready and the decor
# windows are drawn. DisplayContent.shouldWaitForSystemDecorWindowsOnBoot() has
#     if (!mWmService.mSystemBooted && !haveBootMsg) return true;
# so a TYPE_BOOT_PROGRESS window must exist while mSystemBooted is still false.
# `debug.sf.nobootanimation=1` may have removed exactly that window. Capture the
# wakefulness and the policy state so the next card answers it directly.
timeout 12 dumpsys power > $CARD/DEBUG/live-power-late.txt 2>&1
{ echo "===== boot-progress / animation windows ====="
  timeout 12 dumpsys window windows 2>/dev/null | grep -E 'Window #|Boot|boot|mSystemBooted|mAwake|mShowingBootMessages|Splash'
  echo "===== window policy wakefulness ====="
  timeout 12 dumpsys window policy 2>/dev/null | grep -E 'mAwake|mScreenOn|mSystemBooted|mShowingBootMessages|mKeyguardDrawComplete|mWindowManagerDrawComplete'
} > $CARD/DEBUG/live-bootwin-late.txt 2>&1
sync
echo "snapshot: wrote $(ls $CARD/DEBUG/live-* 2>/dev/null | wc -l) files" >> $LOG
umount $CARD 2>>$LOG
exit 0
"""

LIVE_RC = """# class core, not late_start: late_start waits for vold.decrypt and nothing on
# this device reaches that far yet, so the snapshot would never run.
service dsh-live-snapshot /system/bin/sh /metadata/dsh/live-snapshot.sh
    class core
    oneshot
    user root
    group root
"""

NEW_CODE = r'''// sd57: a snapshot of the boot that is currently running, on the card.
//
// debug-init's harvest runs at the start of a boot, so every receipt it copies
// describes the boot BEFORE; Windows can only read the card, and /metadata plus
// /data are ext4. Without this the newest readable logcat is always one boot
// old, which cost several rounds of analysis on stale evidence.
const char kLiveSnapshotSh[] =
%s
const char kLiveSnapshotRc[] =
%s
void PublishLiveSnapshot() {
    PublishTextFile("/metadata/dsh/live-snapshot.sh", kLiveSnapshotSh, 0755);
    PublishTextFile("/vendor/etc/init/94-live-snapshot.rc", kLiveSnapshotRc, 0644);
}

'''


def c_literal(text):
    """C string literal lines, one source line per shell line."""
    out = []
    for line in text.splitlines():
        esc = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append('    "%s\\n"\n' % esc)
    return "".join(out).rstrip("\n") + ";\n"


def reslice(text, head, tail, body):
    """Replace the literal between `head` and `tail`; None if the anchors are gone."""
    i = text.find(head)
    if i < 0:
        return None
    j = text.find(tail, i + len(head))
    if j < 0:
        return None
    return text[:i] + head + c_literal(body) + text[j:]


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        # Already inserted: regenerate the two literals so edits here take effect.
        sh_head = "const char kLiveSnapshotSh[] =\n"
        sh_tail = "\nconst char kLiveSnapshotRc[] ="
        rc_head = "const char kLiveSnapshotRc[] =\n"
        rc_tail = "\nvoid PublishLiveSnapshot()"

        for head, tail, body, name in ((sh_head, sh_tail, LIVE_SH, "kLiveSnapshotSh"),
                                       (rc_head, rc_tail, LIVE_RC, "kLiveSnapshotRc")):
            new = reslice(text, head, tail, body)
            if new is None:
                sys.exit("anchors for %s not found" % name)
            text = new

        with open(PATH, "w", encoding="utf-8") as f:
            f.write(text)
        print("regenerated kLiveSnapshotSh / kLiveSnapshotRc in", PATH)
        return 0

    anchor = "void PublishRamdiskFstab(DIR* ramdisk) {"
    call = "    PublishServiceRcOverrides();\n"
    if anchor not in text:
        sys.exit("anchor for function insert not found")
    if call not in text:
        sys.exit("anchor for call insert not found")

    text = text.replace(anchor, (NEW_CODE % (c_literal(LIVE_SH), c_literal(LIVE_RC))) + anchor, 1)
    text = text.replace(call, call + "    PublishLiveSnapshot();\n", 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("patched", PATH, "->", text.count("\n") + 1, "lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())