// SPDX-License-Identifier: GPL-2.0
/*
 * debug-init.c - a tiny static /init for the RG34XXSP debug initramfs.
 *
 * Why this exists: the handheld has no UART exposed, no USB enumeration, and
 * pstore is not compiled in. When the Android boot loop fails we are blind.
 * This init runs as PID 1 straight out of the initramfs (so it works even when
 * the Android ramdisk cannot mount /system), dumps everything we need to a text
 * file on the FAT boot partition, then powers off. The user pulls the SD card,
 * reads DEBUG/dmesg.txt on a PC.
 *
 * Build: aarch64-linux-gnu-gcc -static -O2 -o init debug-init.c
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>
#include <linux/fb.h>
#include <linux/fs.h>

#ifndef ARRAY_SIZE
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#endif

#ifndef SYSLOG_ACTION_READ_ALL
#define SYSLOG_ACTION_READ_ALL 3
#endif
#ifndef SYSLOG_ACTION_SIZE_BUFFER
#define SYSLOG_ACTION_SIZE_BUFFER 10
#endif

static FILE *out;
static FILE *con;

/*
 * When set, every line is fsync'd as soon as it is written.
 *
 * Needed for the hardware probing at the end of the boot: reading a
 * clock-gated peripheral raises an external abort, which the kernel delivers
 * as SIGBUS and which kills this process outright. Anything still sitting in
 * the page cache is lost with it, and the card then shows a log that simply
 * stops -- which is exactly what made the previous attempt look as if the
 * paint test had never run.
 */
static int durable;

static void say(const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	if (out) {
		vfprintf(out, fmt, ap);
		fflush(out);
		if (durable)
			fsync(fileno(out));
	}
	va_end(ap);

	va_start(ap, fmt);
	if (con) {
		vfprintf(con, fmt, ap);
		fflush(con);
	}
	va_end(ap);
}

static void dump_file(const char *path)
{
	FILE *f;
	char buf[4096];
	size_t n;

	say("\n===== %s =====\n", path);
	f = fopen(path, "r");
	if (!f) {
		say("(open failed: %s)\n", strerror(errno));
		return;
	}
	while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
		fwrite(buf, 1, n, out);
	fclose(f);
	if (n == 0)
		say("\n");
	else
		say("\n");
}

static void dump_dir(const char *path)
{
	DIR *d;
	struct dirent *e;

	say("\n===== ls %s =====\n", path);
	d = opendir(path);
	if (!d) {
		say("(opendir failed: %s)\n", strerror(errno));
		return;
	}
	while ((e = readdir(d)))
		say("%s\n", e->d_name);
	closedir(d);
}

/* Print the whole kernel ring buffer in one shot (no /dev/kmsg races). */
static void dump_kmsg(void)
{
	long len;
	char *buf;
	int n;

	say("\n===== kernel ring buffer =====\n");
	len = syscall(SYS_syslog, SYSLOG_ACTION_SIZE_BUFFER, NULL, 0);
	if (len <= 0) {
		say("(SIZE_BUFFER failed: %s)\n", strerror(errno));
		return;
	}
	buf = malloc((size_t)len + 1);
	if (!buf) {
		say("(oom)\n");
		return;
	}
	n = (int)syscall(SYS_syslog, SYSLOG_ACTION_READ_ALL, buf, (int)len);
	if (n < 0) {
		say("(READ_ALL failed: %s)\n", strerror(errno));
		free(buf);
		return;
	}
	fwrite(buf, 1, (size_t)n, out);
	free(buf);
	say("\n");
}

static void dump_block_sizes(void)
{
	DIR *d;
	struct dirent *e;
	char path[300];
	unsigned long long sz;

	say("\n===== block device sizes =====\n");
	d = opendir("/dev");
	if (!d)
		return;
	while ((e = readdir(d))) {
		int fd;

		if (strncmp(e->d_name, "mmcblk", 6) != 0)
			continue;
		snprintf(path, sizeof(path), "/dev/%s", e->d_name);
		fd = open(path, O_RDONLY);
		if (fd < 0) {
			say("%-20s (open failed: %s)\n", e->d_name, strerror(errno));
			continue;
		}
		sz = 0;
		if (ioctl(fd, BLKGETSIZE64, &sz) == 0)
			say("%-20s %llu bytes (%.2f MiB)\n", e->d_name, sz,
			    (double)sz / 1048576.0);
		else
			say("%-20s (BLKGETSIZE64 failed: %s)\n", e->d_name,
			    strerror(errno));
		close(fd);
	}
	closedir(d);
}

/*
 * The mmc controller -> card slot mapping, and the platform device names that
 * androidboot.boot_devices has to match. init's DeviceHandler::FindPlatformDevice
 * walks up to the first ancestor whose `subsystem` symlink is the platform bus
 * and returns that directory without the /sys prefix, so the expected value is
 * e.g. "4020000.mmc" and NOT "mmcblk0". Dumping /sys/bus/platform/devices here
 * is what tells us which of the two spellings this kernel actually produces.
 */
static void dump_platform_devices(void)
{
	dump_dir("/sys/bus/platform/devices");
	dump_dir("/sys/class/mmc_host");
	dump_file("/proc/device-tree/aliases");

	say("\n===== mmc host -> block device =====\n");
	for (int i = 0; i < 4; i++) {
		char link[128], target[512];
		ssize_t n;

		snprintf(link, sizeof(link), "/sys/class/block/mmcblk%d/device", i);
		n = readlink(link, target, sizeof(target) - 1);
		if (n < 0)
			continue;
		target[n] = '\0';
		say("mmcblk%d -> %s\n", i, target);
	}
}

/* Every mmcblk*p1 candidate, with the first 3 bytes of its boot sector, so we
 * can tell which card is the FAT boot partition and which is the eMMC. */
static void dump_partition_heads(void)
{
	say("\n===== partition heads =====\n");
	for (int i = 0; i < 4; i++) {
		char path[64];
		unsigned char hdr[16];
		int fd;
		ssize_t n;

		snprintf(path, sizeof(path), "/dev/mmcblk%dp1", i);
		fd = open(path, O_RDONLY);
		if (fd < 0)
			continue;
		n = read(fd, hdr, sizeof(hdr));
		if (n > 0) {
			say("%s:", path);
			for (int k = 0; k < n && k < 8; k++)
				say(" %02x", hdr[k]);
			say("   (%s)\n", (hdr[3] == 0xaa) ? "FAT boot sector" : "?");
		}
		close(fd);
	}
}

static int wait_for(const char *path, int tries)
{
	while (tries-- > 0) {
		if (access(path, F_OK) == 0)
			return 0;
		usleep(250000);
	}
	return -1;
}

/*
 * Stream /dev/kmsg into a file on the FAT partition, forever.
 *
 * This runs as a forked child, not as PID 1, so the real Android init can take
 * over PID 1 and boot normally while we keep recording.
 *
 * Both descriptors are opened by the parent *before* the fork, and that is
 * load-bearing rather than stylistic: Android init replaces /dev with a fresh
 * tmpfs and later stacks another tmpfs over /mnt, so anything opened after the
 * handover would point at the wrong filesystem. An fd opened beforehand keeps
 * resolving to the original devtmpfs and the original FAT directory, so the
 * log survives both remounts.
 *
 * fsync() is called every so often because a first-stage failure ends in
 * LOG(FATAL) -> abort -> panic -> reset, and anything still in the page cache
 * at that point is gone.
 */
static void kmsg_logger(int kfd, int lfd)
{
	char buf[8192];
	ssize_t n;
	int quiet = 0, dirty = 0;

	for (;;) {
		n = read(kfd, buf, sizeof(buf));
		if (n > 0) {
			if (write(lfd, buf, (size_t)n) > 0)
				dirty++;
			quiet = 0;
			if (dirty >= 32) {
				fsync(lfd);
				dirty = 0;
			}
			continue;
		}
		if (n < 0 && errno != EAGAIN && errno != EINTR)
			break;
		usleep(100000);
		if (dirty && ++quiet >= 10) {
			fsync(lfd);
			quiet = 0;
		}
	}
	fsync(lfd);
	_exit(0);
}

/*
 * Candidate boot partitions, in the order we try them.
 *
 * We cannot assume the TF card is mmcblk0: the DTB aliases say mmc0 is the SD
 * slot, but the kernel only assigns mmcblkN in probe order, and on a cold boot
 * the eMMC can win the race. The eMMC's p1 is the Allwinner `special`
 * partition, not a filesystem, so mounting the wrong candidate just fails --
 * hence we probe by mounting and looking for extlinux/extlinux.conf rather
 * than trusting the index.
 */
static const char *boot_candidates[] = {
	"/dev/mmcblk0p1", "/dev/mmcblk1p1", "/dev/mmcblk2p1",
	"/dev/mmcblk0p2", "/dev/mmcblk1p2", "/dev/mmcblk2p2", NULL
};

/* Mount each candidate read-write and keep the first that really holds our
 * extlinux.conf. Returns 0 and leaves it mounted on /mnt, or -1. */
static int mount_boot_partition(const char **used)
{
	int j;

	for (j = 0; boot_candidates[j]; j++) {
		if (access(boot_candidates[j], F_OK) != 0)
			continue;
		if (mount(boot_candidates[j], "/mnt", "vfat", 0, NULL) != 0)
			continue;
		if (access("/mnt/extlinux/extlinux.conf", F_OK) == 0) {
			*used = boot_candidates[j];
			return 0;
		}
		umount("/mnt");
	}
	return -1;
}

/*
 * Brute-force the blender route, using the panel as the read-out.
 *
 * The dump proves everything else is right: channel 6 holds a valid, enabled
 * XRGB8888 layer pointing at the CMA buffer we paint, the blender is sized and
 * clocked, the pipe is enabled and the tcon is running. The one register that
 * still looks wrong is BLEND_ROUTE, which sun8i_mixer_commit() fills from
 * layer->index -- and on DE33 that is 1 while the layer physically lives on
 * channel 6. Every other place in the tree addresses hardware by layer->channel.
 *
 * Rather than guess, write each candidate into BLEND_ROUTE and paint the
 * framebuffer a colour unique to that candidate. Whatever shows up on the panel
 * names the correct value in one boot. ROUTE is per-pipe: pipe n occupies bits
 * 4n..4n+3, and we only ever have zpos 0, so the raw value goes in unshifted.
 */
static void route_probe(void)
{
	static const struct {
		unsigned int route;
		const char *label;
		const char *colour;
		unsigned int rgb;
	} tries[] = {
		{ 0x00000001, "layer->index (what the driver writes now)", "red", 0xff0000 },
		{ 0x00000006, "physical channel 6", "green", 0x00ff00 },
		{ 0x00000008, "channel 6 + 2 (PORT02CHN numbering)", "blue", 0x0000ff },
		{ 0x00000002, "layer index 2", "white", 0xffffff },
		{ 0x00000009, "channel 7 + 2", "magenta", 0xff00ff },
		{ 0x00000000, "channel 0 (VI)", "yellow", 0xffff00 },
	};
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	int ffd = open("/dev/fb0", O_RDWR);
	int mfd = open("/dev/mem", O_RDWR);
	unsigned char *fb = MAP_FAILED;
	unsigned char *mix = MAP_FAILED;
	size_t stride = 0, total = 0;
	unsigned int i, x, y;
	unsigned long page, off = 0;
	unsigned int original = 0;

	say("\n===== BLEND_ROUTE probe (watch the panel) =====\n");
	if (ffd < 0 || mfd < 0) {
		say("(open fb0/mem failed: %s)\n", strerror(errno));
		goto out;
	}
	if (ioctl(ffd, FBIOGET_FSCREENINFO, &fix) != 0 ||
	    ioctl(ffd, FBIOGET_VSCREENINFO, &var) != 0) {
		say("(fb ioctl failed: %s)\n", strerror(errno));
		goto out;
	}
	say("fb0: %s smem_len=%u line_length=%u bpp=%u %ux%u\n",
	    fix.id, fix.smem_len, fix.line_length, var.bits_per_pixel,
	    var.xres, var.yres);

	stride = fix.line_length ? fix.line_length : (var.xres_virtual * 4);
	total = stride * var.yres_virtual;
	fb = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, ffd, 0);
	if (fb == MAP_FAILED) {
		say("(mmap fb0 failed: %s)\n", strerror(errno));
		goto out;
	}

	/* BLEND_ROUTE for mixer 0: blender base 0x1000, route at +0x80. */
	page = 0x1281000UL & ~0xfffUL;
	off = 0x1281000UL - page;
	mix = mmap(NULL, off + 0x100, PROT_READ | PROT_WRITE, MAP_SHARED, mfd,
		   (off_t)page);
	if (mix == MAP_FAILED) {
		say("(mmap mixer blend failed: %s)\n", strerror(errno));
		goto out;
	}

	{
		volatile unsigned int *route =
			(volatile unsigned int *)(mix + off + 0x80);

		original = *route;
		say("ROUTE before probe: 0x%08x\n", original);
	}

	/*
	 * Orthogonal test first, because it decides how to read everything
	 * after it: turn the blender background on and disable every pipe.
	 *
	 * If the panel goes green, the whole downstream chain (blender -> DE
	 * top -> tcon-top -> tcon0 -> RGB pins -> panel) works and the only
	 * fault is the route value. If it stays black while we know BKCOLOR
	 * and OUTSIZE are programmed, the break is downstream of the blender
	 * and probing route values is pointless.
	 *
	 * Pipe control bits 8..12 enable pipes; clearing them leaves only
	 * BKCOLOR. Restored right after so the route loop starts clean.
	 */
	{
		volatile unsigned int *pipe =
			(volatile unsigned int *)(mix + off + 0x00);
		volatile unsigned int *bk =
			(volatile unsigned int *)(mix + off + 0x88);
		unsigned int orig_pipe = *pipe;
		unsigned int orig_bk = *bk;

		*bk = 0xff00ff00;	/* solid green (ARGB, alpha in 31:24) */
		*pipe = orig_pipe & ~0x00001f00u;
		__sync_synchronize();
		say("BKCOLOR probe: bk=0x%08x pipe=0x%08x -- panel should be GREEN\n",
		    *bk, *pipe);
		sleep(4);
		*bk = orig_bk;
		*pipe = orig_pipe;
		__sync_synchronize();
		say("BKCOLOR restored: bk=0x%08x pipe=0x%08x\n", *bk, *pipe);
	}

	for (i = 0; i < ARRAY_SIZE(tries); i++) {
		volatile unsigned int *route =
			(volatile unsigned int *)(mix + off + 0x80);

		for (y = 0; y < var.yres_virtual; y++) {
			unsigned int *row = (unsigned int *)(fb + y * stride);

			for (x = 0; x < var.xres_virtual; x++)
				row[x] = tries[i].rgb;
		}
		msync(fb, total, MS_SYNC);
		*route = tries[i].route;
		__sync_synchronize();
		say("ROUTE=0x%08x (%s) -> painted %s, holding 3s, readback 0x%08x\n",
		    tries[i].route, tries[i].label, tries[i].colour,
		    *route);
		sleep(3);
	}

	/*
	 * Put back what the driver wrote. Android re-programs this register on
	 * its first commit anyway, but leaving a probe value behind would make
	 * the next log lie about what the driver actually asked for.
	 */
	*(volatile unsigned int *)(mix + off + 0x80) = original;
	__sync_synchronize();
	say("ROUTE restored to 0x%08x\n",
	    *(volatile unsigned int *)(mix + off + 0x80));

out:
	if (fb != MAP_FAILED)
		munmap(fb, total);
	if (mix != MAP_FAILED)
		munmap(mix, off + 0x100);
	if (ffd >= 0)
		close(ffd);
	if (mfd >= 0)
		close(mfd);
	say("route probe done\n");
}

/*
 * Read the display pipeline's registers straight out of the hardware.
 *
 * Why this is needed: every driver binds, fb0 is registered with exactly the
 * geometry we expect, and painting it solid colours changes nothing on the
 * panel. That can only mean the mixer is not scanning the buffer we are
 * writing -- either it was never enabled, or its layer address points
 * somewhere else. Neither question can be answered from sysfs, so read the
 * actual MMIO. H700 puts DRAM at 0x40000000, so these addresses are device
 * memory and /dev/mem lets us at them.
 *
 * Layout note: for DE33 the mixer's own block (mixer@280000 -> 0x1280000)
 * only holds the global and blender registers; the per-channel blocks live in
 * the "planes" block (planes@100000 -> 0x1100000) at
 * 0x1000 + channel * 0x20000. Mixer 0 maps channels 0 (VI), 6 and 7 (UI).
 */
static void mmio_dump(void)
{
	static const struct {
		const char *name;
		unsigned long base;
		unsigned long size;
	} regions[] = {
		{ "ccu-de2     ", 0x1008000, 0x100 }, /* CHN2CORE 0x24, PORT0/1 0x28/0x2c */
		{ "planes hdr  ", 0x1100000, 0x40 },
		{ "vi ch0      ", 0x1101000, 0x100 },
		{ "ui ch6      ", 0x11c1000, 0x100 },
		{ "ui ch7      ", 0x11e1000, 0x100 },
		{ "mixer0 glob ", 0x1280000, 0x40 },
		/*
		 * DE top block. mixer@280000 declares a second reg window of
		 * 0x40 bytes at 0x8100; sun8i_mixer_bind() takes it as
		 * "top_regs" and DE33 writes GLOBAL_SIZE (0x8) and
		 * GLOBAL_CLK (0xc) there rather than into the mixer block.
		 * Dumping it tells us whether the DE was clocked and sized.
		 */
		{ "mixer0 top  ", 0x1008100, 0x40 },
		{ "mixer1 top  ", 0x1008140, 0x40 },
		{ "mixer0 blend", 0x1281000, 0x100 },
		{ "tcon-top    ", 0x6510000, 0x40 },
		{ "tcon0       ", 0x6511000, 0x100 },
		{ "tcon1       ", 0x6515000, 0x100 },
		{ "pwm         ", 0x300a000, 0x40 },
		/*
		 * PD bank base. pinctrl-sunxi maps one bank per 0x24 bytes
		 * (BANK_MEM_SIZE) and PD starts at pin 96, so bank 3 starts at
		 * 3 * 0x24 = 0x6c -- not at 0x700, which lands in the middle of
		 * a different bank and made the earlier dump unreadable.
		 */
		{ "pinctrl PD  ", 0x300b000 + 0x6c, 0x100 },
	};
	int fd = open("/dev/mem", O_RDONLY);
	unsigned int r, w;

	say("\n===== MMIO dump =====\n");
	if (fd < 0) {
		say("(open /dev/mem failed: %s)\n", strerror(errno));
		return;
	}

	for (r = 0; r < ARRAY_SIZE(regions); r++) {
		unsigned long page = regions[r].base & ~0xfffUL;
		unsigned long off = regions[r].base - page;
		unsigned long span = ((off + regions[r].size + 0xfff) & ~0xfffUL);
		unsigned char *map = mmap(NULL, span, PROT_READ, MAP_SHARED, fd,
					  (off_t)page);

		say("--- %s @ 0x%08lx ---\n", regions[r].name,
		    regions[r].base);
		if (map == MAP_FAILED) {
			say("(mmap failed: %s)\n", strerror(errno));
			continue;
		}
		for (w = 0; w < regions[r].size / 4; w += 4) {
			unsigned int k;

			say("  +%03lx:", w * 4);
			for (k = 0; k < 4 && (w + k) < regions[r].size / 4; k++)
				say(" %08x", *(volatile unsigned int *)
						     (map + off + (w + k) * 4));
			say("\n");
		}
		munmap(map, span);
	}

	close(fd);
}

/*
 * Where does the hardware actually point?
 *
 * The interesting question is not "what is in fb0" -- we know that, we painted
 * it -- but "which address did the layer get told to scan". Two failure modes
 * look identical on screen and are told apart by this scan:
 *
 *   a) the layer register write never happened (driver bound, but the plane
 *      was never made visible), so every LADDR still reads back as whatever
 *      reset left there; and
 *   b) the write landed at a different offset than the one we dump, so the
 *      DE scans address 0 while our pattern sits in CMA.
 *
 * Scanning the whole planes block for words that look like CMA addresses
 * answers both: a hit gives us the real offset, no hit at all means nothing
 * was ever programmed. Mixer 0 maps channels 0 (VI), 6 and 7 (UI); DE33 puts
 * channel n at 0x1000 + n * 0x20000 relative to the planes block.
 */
#define CMA_LO 0x70000000UL
#define CMA_HI 0x80000000UL

static void mmio_scan(const char *name, unsigned long base, unsigned long size)
{
	unsigned long page = base & ~0xfffUL;
	unsigned long off = base - page;
	unsigned long span = ((off + size + 0xfff) & ~0xfffUL);
	int fd = open("/dev/mem", O_RDONLY);
	unsigned long w, hits = 0;
	unsigned char *map;

	say("\n===== scan %s for CMA pointers =====\n", name);
	if (fd < 0) {
		say("(open /dev/mem failed: %s)\n", strerror(errno));
		return;
	}
	map = mmap(NULL, span, PROT_READ, MAP_SHARED, fd, (off_t)page);
	if (map == MAP_FAILED) {
		say("(mmap failed: %s)\n", strerror(errno));
		close(fd);
		return;
	}

	for (w = 0; w < size / 4; w++) {
		unsigned int v = *(volatile unsigned int *)(map + off + w * 4);

		if (v < CMA_LO || v >= CMA_HI)
			continue;
		say("  %s+0x%06lx = 0x%08x\n", name, w * 4, v);
		if (++hits >= 24) {
			say("  ... (stopping after 24 hits)\n");
			break;
		}
	}
	if (!hits)
		say("  no CMA-looking word in %s (%lu bytes scanned)\n",
		    name, size);

	munmap(map, span);
	close(fd);
}

/*
 * One header per DE33 channel: ATTR / SIZE / COORD / PITCH / LADDR.
 *
 * Dumping only the offsets the driver claims to use cannot tell "never
 * written" from "written somewhere else". Printing every channel's first six
 * words covers the whole plausible range in a few lines, and ATTR.bit0 says
 * directly whether that channel is enabled.
 */
static void mmio_channels(void)
{
	int fd = open("/dev/mem", O_RDONLY);
	unsigned int ch;

	say("\n===== DE33 channel headers (planes block) =====\n");
	if (fd < 0) {
		say("(open /dev/mem failed: %s)\n", strerror(errno));
		return;
	}

	for (ch = 0; ch < 10; ch++) {
		unsigned long base = 0x1100000 + 0x1000 + (unsigned long)ch * 0x20000;
		unsigned long page = base & ~0xfffUL;
		unsigned long off = base - page;
		unsigned char *map = mmap(NULL, 0x1000, PROT_READ, MAP_SHARED,
					  fd, (off_t)page);
		unsigned int *p;

		if (map == MAP_FAILED) {
			say("  ch%-2u @ 0x%08lx: (mmap failed: %s)\n", ch, base,
			    strerror(errno));
			continue;
		}
		p = (unsigned int *)(map + off);
		say("  ch%-2u @ 0x%08lx: attr=%08x size=%08x coord=%08x "
		    "pitch=%08x laddr=%08x laddr2=%08x%s\n",
		    ch, base, p[0], p[1], p[2], p[3], p[4], p[5],
		    (p[0] & 1) ? "  [ENABLED]" : "");
		munmap(map, 0x1000);
	}

	close(fd);
}

/*
 * Take the one-shot snapshot of everything that is already known at this
 * point, before Android init touches any of it.
 */
static void snapshot(FILE *o)
{
	out = o;
	say("RG34XXSP boot snapshot\n");
	dump_file("/proc/cmdline");
	dump_file("/proc/version");
	dump_file("/proc/partitions");
	dump_file("/proc/filesystems");
	dump_file("/proc/mounts");
	dump_file("/proc/devices");
	dump_file("/proc/device-tree/chosen/bootargs");
	dump_file("/proc/device-tree/model");
	dump_dir("/dev");
	dump_dir("/dev/block");
	dump_dir("/sys/class/block");
	dump_dir("/sys/bus/mmc/devices");
	dump_dir("/sys/class/drm");
	/*
	 * The graphics side, which is what the "bright streaks instead of text"
	 * symptom points at: /proc/fb and /sys/class/graphics tell us whether a
	 * framebuffer was registered at all, and the per-connector drm dirs tell
	 * us whether tcon-lcd0 came up. If fb0 is missing the panel is being fed
	 * nothing and just holds whatever the SPI init sequence left in it.
	 */
	dump_file("/proc/fb");
	dump_dir("/sys/class/graphics");
	dump_dir("/sys/class/drm/card0");
	/*
	 * Whether the panel driver actually bound: the panel hangs off a
	 * spi-gpio controller, so it only appears under /sys/bus/spi/devices if
	 * spi-gpio probed, and only has a `driver` symlink if panel-mipi
	 * matched the compatible string. A missing entry here means the panel
	 * was never initialised at all, which is exactly what a stuck four-line
	 * pattern looks like.
	 */
	dump_dir("/sys/bus/spi/devices");
	/*
	 * panel-mipi is an *spi* driver (drivers/gpu/drm/panel/panel-mipi.c
	 * registers panel_mipi_spi_driver with .name = "panel-mipi"), so its
	 * driver directory lives under /sys/bus/spi/drivers -- looking under
	 * platform/drivers only ever yields "no such file" and tells us nothing.
	 * An empty directory means "driver registered, no device matched";
	 * a missing one means "driver not built in at all".
	 */
	dump_dir("/sys/bus/spi/drivers/panel-mipi");
	dump_dir("/sys/bus/spi/drivers");
	dump_dir("/sys/class/backlight");
	/* debugfs is where panel-mipi exposes the decoded firmware, if it got
	 * that far. Mounting it here is harmless -- Android remounts /sys later. */
	if (mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL) == 0 ||
	    errno == EBUSY) {
		dump_dir("/sys/kernel/debug/dri");
		dump_dir("/sys/kernel/debug/dri/0");
		/*
		 * The single most useful file when a whole DRM driver never
		 * binds: every device that returned -EPROBE_DEFER and is
		 * waiting for a supplier, with the reason. Without it we can
		 * only guess which link of the chain (planes -> CCU regmap,
		 * mixer -> planes, tcon-top -> mixers, drm -> components) is
		 * the one that never resolved.
		 */
		dump_file("/sys/kernel/debug/devices_deferred");
	}
	/*
	 * Bind state of each display/backlight driver. A driver directory
	 * exists as soon as the driver registers, and contains a symlink per
	 * bound device -- so an empty directory means "driver loaded, device
	 * never matched", while a missing directory means "driver not built
	 * in at all". Those two cases need completely different fixes.
	 */
	dump_dir("/sys/bus/platform/drivers/sun50i-planes");
	dump_dir("/sys/bus/platform/drivers/sun8i-mixer");
	dump_dir("/sys/bus/platform/drivers/sun8i-tcon-top");
	dump_dir("/sys/bus/platform/drivers/sun4i-tcon");
	dump_dir("/sys/bus/platform/drivers/sun4i-drm");
	dump_dir("/sys/bus/platform/drivers/sun8i-pwm");
	dump_dir("/sys/bus/platform/drivers/pwm-backlight");
	dump_dir("/sys/bus/platform/drivers/sunxi-de2-clks");
	/*
	 * The DE2 CCU node is the supplier behind `allwinner,plane-mapping`;
	 * if sunxi-de2-clks bound it, planes can resolve the regmap, otherwise
	 * planes defers forever.
	 *
	 * Device names are bus-address prefixed: the DTS puts these under
	 * bus@1000000, so clock@8000 becomes 1008000.clock, planes@100000
	 * becomes 1100000.planes and mixer@280000 becomes 1280000.mixer --
	 * the plain node address is never the sysfs name. Reading the
	 * `driver` symlink tells us bound vs orphan.
	 */
	dump_file("/sys/bus/platform/devices/1100000.planes/driver/uevent");
	dump_file("/sys/bus/platform/devices/1008000.clock/driver/uevent");
	dump_file("/sys/bus/platform/devices/1280000.mixer/driver/uevent");
	dump_file("/sys/bus/platform/devices/6510000.tcon-top/driver/uevent");
	dump_file("/sys/bus/platform/devices/300a000.pwm/driver/uevent");
	dump_file("/sys/bus/platform/devices/backlight/driver/uevent");
	dump_dir("/sys/bus/platform/devices/1100000.planes");
	dump_dir("/sys/bus/platform/devices/1008000.clock");
	dump_block_sizes();
	dump_platform_devices();
	dump_partition_heads();

	/* Does the super partition look like an Android dynamic partition? */
	{
		int fd = open("/dev/mmcblk0p4", O_RDONLY);
		unsigned char hdr[4096];

		say("\n===== super (mmcblk0p4) LP metadata =====\n");
		if (fd < 0) {
			say("(open failed: %s)\n", strerror(errno));
		} else {
			ssize_t n = read(fd, hdr, sizeof(hdr));

			if (n < 4096) {
				say("(short read: %zd)\n", n);
			} else {
				/* geometry magic 0x616c4467 lives at 4096+offset */
				say("first 32 bytes: ");
				for (int k = 0; k < 32; k++)
					say("%02x", hdr[k]);
				say("\n");
			}
			close(fd);
		}
	}

	dump_kmsg();
	say("\n===== SNAPSHOT COMPLETE =====\n");
}

int main(void)
{
	const char *bootdev = NULL;
	char logpath[128];
	int i, logged = 0;

	mkdir("/proc", 0555);
	mkdir("/sys", 0555);
	mkdir("/dev", 0755);
	mkdir("/mnt", 0755);
	mkdir("/tmp", 0777);
	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);

	con = fopen("/dev/console", "w");

	say("rg34xxsp debug init starting\n");

	/*
	 * Find the FAT boot partition. Wait for *some* mmc block device to show
	 * up first, then let mount_boot_partition() pick the right one by
	 * content -- the TF card is not reliably mmcblk0 on a cold boot.
	 */
	for (i = 0; i < 60 && !bootdev; i++) {
		if (access("/dev/mmcblk0", F_OK) != 0 &&
		    access("/dev/mmcblk1", F_OK) != 0 &&
		    access("/dev/mmcblk2", F_OK) != 0) {
			usleep(250000);
			continue;
		}
		if (mount_boot_partition(&bootdev) != 0)
			usleep(250000);
	}
	if (!bootdev) {
		say("no FAT boot partition with extlinux/extlinux.conf, cannot persist the log\n");
		goto handover;
	}
	say("boot partition: %s (mounted on /mnt)\n", bootdev);

	mkdir("/mnt/DEBUG", 0755);

	/*
	 * Proof-of-life marker, written and fsync'd before anything else.
	 *
	 * This is not cosmetic. fclose() only drops the data in the page cache;
	 * a watchdog reset roughly a dozen seconds later takes the whole FAT
	 * directory entry with it, and the card then looks exactly as if this
	 * init had never run at all. Getting one small file durably onto the
	 * medium first tells us whether the problem is "init never ran" or
	 * "init ran but could not do something".
	 */
	{
		FILE *m = fopen("/mnt/DEBUG/ran.txt", "w");

		if (m) {
			fprintf(m, "debug-init reached main, boot partition %s\n",
				bootdev);
			fflush(m);
			fsync(fileno(m));
			fclose(m);
		} else {
			say("cannot create /mnt/DEBUG/ran.txt: %s\n",
			    strerror(errno));
		}
		sync();
	}

	snprintf(logpath, sizeof(logpath), "/mnt/DEBUG/dmesg.txt");

	{
		FILE *o = fopen(logpath, "w");

		if (!o) {
			say("cannot create %s: %s\n", logpath, strerror(errno));
			goto handover;
		}
		snapshot(o);
		/* Same reasoning as above: make it survive an abrupt reset. */
		fflush(o);
		fsync(fileno(o));
		fclose(o);
		sync();
		logged = 1;
	}

	/*
	 * Paint known colours now, while the log is already safely on the card
	 * and before Android init starts competing for the display.
	 */
	if (logged) {
		FILE *o = fopen(logpath, "a");

		if (o) {
			out = o;
			/*
			 * From here on every line has to reach the medium on
			 * its own. Reading a clock-gated block through
			 * /dev/mem raises an external abort, the kernel turns
			 * it into SIGBUS and this process dies mid-sentence;
			 * FAT only commits the file size on fsync/close, so
			 * without this the whole paint-test + MMIO section
			 * would be written to the card but stay invisible.
			 */
			durable = 1;
			/*
			 * Order matters: the register dump is the evidence we
			 * actually came for, so it runs FIRST. On sd13 the
			 * paint test ran first, held the screen for 18 s and
			 * got cut short by a power-off before mmio_dump ever
			 * printed a line -- 18 seconds of nothing is longer
			 * than anyone waits.
			 */
			mmio_dump();
			mmio_channels();
			/*
			 * Most likely to abort, so after the two structured
			 * dumps: sweep the big blocks for words that look like
			 * CMA addresses. If the layer got told to scan CMA we
			 * find its LADDR here even when it sits at an offset the
			 * driver does not document; if the sweep comes back
			 * empty, no plane was ever programmed.
			 */
			mmio_scan("planes", 0x1100000, 0x180000);
			mmio_scan("mixer0", 0x1280000, 0x20000);
			/*
			 * Then let the hardware answer the one question the
			 * dump could not: which channel id BLEND_ROUTE wants.
			 * Each candidate gets its own colour on the panel, so a
			 * single boot names the right value. This replaces the
			 * old paint test -- it paints too, so it also still
			 * proves fb0 maps the buffer the layer scans.
			 */
			route_probe();
			fflush(o);
			fsync(fileno(o));
			fclose(o);
			out = NULL;
			sync();
		}
	}

handover:
	/*
	 * Hand PID 1 over to the real Android init that ships in the same
	 * initramfs (mk-sdcard.sh renames it to /init.android). Everything we
	 * recorded is already on the card; the streaming child keeps appending
	 * while Android boots, so a crash later still leaves a trace.
	 *
	 * Both descriptors are opened here, before the exec, because Android
	 * init remounts /dev and later stacks tmpfs on /mnt -- an fd opened
	 * afterwards would no longer reach the FAT partition.
	 */
	if (logged) {
		int kfd = open("/dev/kmsg", O_RDONLY | O_NONBLOCK);
		int lfd = open(logpath, O_WRONLY | O_CREAT | O_APPEND, 0644);

		if (kfd >= 0 && lfd >= 0) {
			pid_t pid = fork();

			if (pid == 0) {
				kmsg_logger(kfd, lfd);
				_exit(0);
			}
			say("kmsg logger started (pid %d), log: %s\n", (int)pid,
			    logpath);
		} else {
			say("kmsg logger not started (kmsg=%d log=%d)\n", kfd, lfd);
		}
	}

	if (con) {
		fclose(con);
		con = NULL;
	}
	say("handing over to /init.android\n");

	execl("/init.android", "/init", NULL);

	/* Only reached if the Android init is missing -- keep the log, then stop. */
	say("exec /init.android failed: %s\n", strerror(errno));
	out = fopen(logpath, "a");
	if (out) {
		dump_kmsg();
		say("\n===== exec failed, halting =====\n");
		fclose(out);
		out = NULL;
	}
	sync();
	sleep(3);
	reboot(RB_POWER_OFF);
	for (;;)
		pause();
	return 0;
}
