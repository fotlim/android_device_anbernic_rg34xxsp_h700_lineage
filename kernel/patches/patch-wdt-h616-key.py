#!/usr/bin/env python3
"""
sd41: let the driver really stop the H616 watchdog.

Every boot since sd25 died 16.2 s after the last thing our initramfs printed,
and it died with no panic, no oops, no init shutdown message, no pstore record
and a fresh first frame on the panel -- the signature of an external reset.
sd40 removed Android from the picture entirely (/mnt/NOSTART keeps PID 1 in the
initramfs) and the machine still died:

    hb: [ 12.084645] watchdog: timer re-armed for handover
    hb: [ 28.25] nostart: t=15s alive, uptime=28s (no Android)     <- last line

12.08 + 16 = 28.08. The 16 s is the only 16 s in the system: WDT_MAX_TIMEOUT.

The watchdog node in our dtb is

    compatible = "allwinner,sun50i-h616-wdt", "allwinner,sun6i-a31-wdt";

and sunxi_wdt_dt_ids[] has no h616 entry, so it binds through the fallback to
sun6i_wdt_reg -- same offsets as the H616, but .wdt_key_val = 0. The H616 needs
0x16AA0000 in bits 31:16 of WDT_MODE (and WDT_CFG) or the write is ignored, so
every sunxi_wdt_stop() and sunxi_wdt_start() we ever issued was a no-op, and the
driver cheerfully logged "timer disabled for the probe" regardless. The kernel
printed the proof itself one line later:

    [   13.510404] watchdog: watchdog0: watchdog did not stop!

Give the H616 its own reg entry with the key, so that WDIOS_DISABLECARD writes
something the SoC listens to.
"""
import sys

SRC = "/tmp/lk-rocknix/drivers/watchdog/sunxi_wdt.c"

ANCHOR = """static const struct sunxi_wdt_reg sun20i_wdt_reg = {"""

NEW_REG = """/*
 * H616/H700: same register layout as sun6i-a31, but the mode and config
 * registers only accept a write that carries the 0x16AA key in the top half.
 * Without it every stop/start is silently dropped -- which is how this board
 * ended up resetting itself 16 s into Android.
 */
static const struct sunxi_wdt_reg sun50i_h616_wdt_reg = {
	.wdt_ctrl = 0x10,
	.wdt_cfg = 0x14,
	.wdt_mode = 0x18,
	.wdt_timeout_shift = 4,
	.wdt_reset_mask = 0x03,
	.wdt_reset_val = 0x01,
	.wdt_key_val = 0x16aa0000,
};
static const struct sunxi_wdt_reg sun20i_wdt_reg = {"""

MATCH_ANCHOR = """	{ .compatible = "allwinner,sun20i-d1-wdt", .data = &sun20i_wdt_reg },"""
MATCH_NEW = """	{ .compatible = "allwinner,sun50i-h616-wdt", .data = &sun50i_h616_wdt_reg },
	{ .compatible = "allwinner,sun20i-d1-wdt", .data = &sun20i_wdt_reg },"""

with open(SRC, encoding="utf-8") as f:
    text = f.read()

if "sun50i_h616_wdt_reg" in text:
    print("already patched, nothing to do")
    sys.exit(0)

if text.count(ANCHOR) != 1:
    sys.exit("reg anchor appears %d times, refusing" % text.count(ANCHOR))
if text.count(MATCH_ANCHOR) != 1:
    sys.exit("match anchor appears %d times, refusing" % text.count(MATCH_ANCHOR))

text = text.replace(ANCHOR, NEW_REG, 1)
text = text.replace(MATCH_ANCHOR, MATCH_NEW, 1)

with open(SRC, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", SRC)