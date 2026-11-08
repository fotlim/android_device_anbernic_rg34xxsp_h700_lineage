#!/usr/bin/env python3
"""Expected register values for a working 720x480 sun4i-drm pipeline on H700/DE33.

Compare against the MMIO dump that debug-init writes to DEBUG/dmesg.txt.
Usage: python3 expect-regs.py [dmesg.txt]
"""
import sys

W, H = 720, 480
# SUN8I_MIXER_SIZE(w, h) = ((h - 1) << 16) | (w - 1)
SIZE = ((H - 1) << 16) | (W - 1)

DE2_BLD_BASE = 0x1000      # DE33 falls into the DE2 branch of sun8i_blender_base()
DE33_CH_BASE = 0x1000
DE33_CH_SIZE = 0x20000
PLANES = 0x1100000
MIXER0 = 0x1280000
TCON_TOP = 0x6510000
TCON0 = 0x6511000
PWM = 0x300a000

def ch(n):
    return PLANES + DE33_CH_BASE + n * DE33_CH_SIZE

BLD = MIXER0 + DE2_BLD_BASE

print(f"# 720x480 expected pipeline state")
print(f"# SUN8I_MIXER_SIZE = 0x{SIZE:08x}")
print()
print(f"{'register':<34} {'addr':<12} {'want':<12} note")
print("-" * 92)

def row(name, addr, want, note):
    shown = "     ?      " if want is None else f"0x{want:08x}"
    print(f"{name:<34} 0x{addr:08x}   {shown}   {note}")

# ---- mixer globals ----
row("mixer0 GLOBAL_CTL", MIXER0 + 0x00, 0x1,
    "bit0 RT_EN must be 1 (sun8i_mixer_init)")
row("mixer0 GLOBAL_SIZE (DE33!)", MIXER0 + 0x08, SIZE,
    "SUN50I_MIXER_GLOBAL_SIZE=0x8, written by mode_set")
row("mixer0 GLOBAL_CLK (DE33!)", MIXER0 + 0x0c, 0x1,
    "SUN50I_MIXER_GLOBAL_CLK=0xc; DE33 must write 1 here")

# ---- blender (DE33 -> DE2_BLD_BASE 0x1000) ----
row("blend PIPE_CTL", BLD + 0x00, 0x1, "FC_EN(0)=1")
row("blend BKCOLOR", BLD + 0x88, 0xff000000, "set in mixer_init")
row("blend OUTSIZE", BLD + 0x8c, SIZE, "written by mode_set")
row("blend ROUTE", BLD + 0x80, 0x0, "layer->index<<shift per pipe")
row("blend MODE(0)", BLD + 0x90, 0x0, "mixer_init only writes when vi_num+ui_num>0")

# ---- UI layers 6 and 7 ----
for n in (6, 7):
    b = ch(n)
    row(f"ui ch{n} LAYER_ATTR", b + 0x00, 0x1, "bit0 EN must be 1")
    row(f"ui ch{n} LAYER_SIZE", b + 0x04, SIZE, "SUN8I_MIXER_SIZE")
    row(f"ui ch{n} LAYER_COORD", b + 0x08, 0x0, "COORD(0,0)")
    row(f"ui ch{n} LAYER_PITCH", b + 0x0c, W * 4, "720*4 = 2880")
    row(f"ui ch{n} LAYER_TOP_LADDR", b + 0x10, None,
        "MUST equal the fb0 DMA address (fb->pitches * y + x)")

# ---- VI layer 0 ----
b = ch(0)
row("vi ch0 LAYER_ATTR", b + 0x00, 0x1, "bit0 EN; RGB_MODE bit15 for RGB")
row("vi ch0 LAYER_SIZE", b + 0x04, SIZE, "")

# ---- tcon-top ----
row("tcon-top PORT_SEL", TCON_TOP + 0x1c, 0x20,
    "bind() writes 0x20 (0003 hack); de_config ORs tcon id into bits[1:0]")
row("tcon-top GATE_SRC", TCON_TOP + 0x20, 0x0, "cleared in bind")

# ---- tcon0 ----
row("tcon0 GCTL", TCON0 + 0x00, None,
    "bit31 TCON_ENABLE=1 (sun4i_tcon_set_status), bit28 IOMAP_TCON0")
row("tcon0 CTL", TCON0 + 0x40, None,
    "bit31 TCON_ENABLE=1 (channel_set_status)")
row("tcon0 BASIC0", TCON0 + 0x48, ((H - 1) << 16) | (W - 1), "X=719 Y=479")
row("tcon0 BASIC1", TCON0 + 0x4c, None, "H_TOTAL | H_BACKPORCH(htotal-hsync_start)")
row("tcon0 BASIC2", TCON0 + 0x50, None, "V_TOTAL*2 | V_BACKPORCH")
row("tcon0 BASIC3", TCON0 + 0x54, None, "V_SYNC | H_SYNC")
row("tcon0 IO_TRI", TCON0 + 0x8c, 0x0, "0 = all outputs enabled")

print()
print("# Reading the dump:")
print("#  1. If LAYER_TOP_LADDR is 0 or does not match the fb address -> the")
print("#     mixer scans nothing/wrong memory. That alone explains 'painting")
print("#     fb0 changes nothing on screen'.")
print("#  2. If GLOBAL_CTL bit0 or tcon0 GCTL bit31 / IO_TRI are wrong -> the")
print("#     pipeline is configured but never switched on.")
print("#  3. If everything above matches, the fault is downstream of tcon:")
print("#     panel init sequence, PD pin mux, or the RGB bus itself.")
