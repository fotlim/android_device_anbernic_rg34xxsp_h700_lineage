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
#include <signal.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <sys/types.h>
#include <unistd.h>
#include <linux/fb.h>
#include <linux/fs.h>
#include <linux/watchdog.h>

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

/*
 * Breadcrumbs and fault reporting.
 *
 * The single-stage probe on sd21 ended with the card's log stopping at
 * "D1: route 0x1 -> 0x1 ... -- 8s" and the unit left sitting on the red
 * framebuffer. Nothing afterwards reached the card, and a crash of PID 1 is
 * indistinguishable from a hardware hang here: with CONFIG_PANIC_TIMEOUT=0 a
 * fatal signal in PID 1 panics the kernel and then just stops.
 *
 * So two independent witnesses are added:
 *  - hb() appends one line straight to DEBUG/ran.txt and fsyncs it, so the
 *    card always names the last step that *started*;
 *  - fault_handler() catches SIGSEGV/SIGBUS/SIGILL/SIGFPE/SIGABRT, records the
 *    signal and the current stage, then reboots -- a death becomes visible
 *    (the panel comes back up) instead of freezing on stale pixels.
 */
static volatile int stage;

/*
 * One trace file per boot, named after the kernel's boot_id.
 *
 * The machine now reboots itself ten seconds after it panics, so a single
 * DEBUG/ran.txt would be truncated by the next boot and the previous run's
 * breadcrumbs -- the only witness there is -- would be gone. boot_id changes
 * per boot and survives on the card, so every boot keeps its own file.
 */
static char trace_path[64];
static char bootlog_path[64];
static char kmsg_path[64];

static void trace_path_init(void)
{
	char id[64] = "";
	char *dash;
	ssize_t n;
	int fd = open("/proc/sys/kernel/random/boot_id", O_RDONLY);

	if (fd >= 0) {
		n = read(fd, id, sizeof(id) - 1);
		close(fd);
		if (n > 0) {
			id[n] = 0;
			dash = strchr(id, '-');
			if (dash)
				*dash = 0;
		}
	}
	if (!id[0])
		snprintf(id, sizeof(id), "unknown");
	snprintf(trace_path, sizeof(trace_path), "/mnt/DEBUG/ran-%s.txt", id);
	snprintf(bootlog_path, sizeof(bootlog_path), "/mnt/DEBUG/dmesg-%s.txt",
		 id);
	snprintf(kmsg_path, sizeof(kmsg_path), "/mnt/DEBUG/kmsg-%s.txt", id);
}

static void hb(const char *what)
{
	char up[40] = "?";
	char *sp;
	FILE *f;
	int fd;

	if (!trace_path[0])
		trace_path_init();
	fd = open(trace_path, O_WRONLY | O_APPEND | O_CREAT, 0644);
	if (fd < 0)
		return;
	f = fopen("/proc/uptime", "r");
	if (f) {
		if (fgets(up, sizeof(up), f)) {
			sp = strchr(up, ' ');
			if (sp)
				*sp = 0;
		}
		fclose(f);
	}
	dprintf(fd, "hb: [%s] %s\n", up, what);
	fsync(fd);
	close(fd);
}

static void fault_handler(int sig)
{
	char buf[96];
	int n, fd;

	if (!trace_path[0])
		trace_path_init();
	fd = open(trace_path, O_WRONLY | O_APPEND | O_CREAT, 0644);

	if (fd >= 0) {
		n = snprintf(buf, sizeof(buf), "FAULT: signal %d at stage %d\n",
			     sig, stage);
		if (n > 0 && write(fd, buf, (size_t)n) < 0)
			;	/* nothing sane to do from a fault handler */
		fsync(fd);
		close(fd);
	}
	/*
	 * Reboot, but not forever.
	 *
	 * sd30 faulted in the same place on every boot and this handler
	 * dutifully reset the machine each time: ten boots in two minutes,
	 * thirty fresh trace files, and the interesting one scrolled off the
	 * card. Three reboots is enough to show that a fault is systematic;
	 * after that stop dead so the panel keeps whatever init printed and
	 * the card keeps exactly one copy of the evidence.
	 *
	 * A boot that reaches the handover deletes the counter, so this only
	 * ever counts consecutive faults.
	 */
	fd = open("/mnt/FAULTCOUNT", O_RDWR | O_CREAT, 0644);
	if (fd >= 0) {
		char c[16];
		int tries = 0;

		n = read(fd, c, sizeof(c) - 1);
		if (n > 0) {
			c[n] = 0;
			tries = atoi(c);
		}
		tries++;
		lseek(fd, 0, SEEK_SET);
		n = snprintf(c, sizeof(c), "%d\n", tries);
		if (n > 0) {
			if (write(fd, c, (size_t)n) < 0)
				;
			fsync(fd);
		}
		close(fd);
		if (tries > 3) {
			sync();
			for (;;)
				pause();
		}
	}
	sync();
	reboot(RB_AUTOBOOT);
	for (;;)
		pause();
}

static void install_fault_handlers(void)
{
	signal(SIGSEGV, fault_handler);
	signal(SIGBUS, fault_handler);
	signal(SIGILL, fault_handler);
	signal(SIGFPE, fault_handler);
	signal(SIGABRT, fault_handler);
}

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

/*
 * Copy whatever pstore/ramoops salvaged from the previous run into DEBUG/.
 *
 * Android init dies the instant it becomes PID 1 -- the kernel then panics,
 * and with panic_timeout=0 the machine halts with the panic text stuck in the
 * ring buffer while the console is already dead. Nothing is written anywhere,
 * which is exactly the dead end sd21..sd23 all hit. ramoops keeps the panic
 * dump in DRAM across the warm reset that panic=10 triggers, so the next boot
 * is the one that finally gets to read it.
 */
/*
 * Did the DT node reach the ramoops driver at all? /sys/bus/platform/drivers/
 * ramoops holds one symlink per bound device; if it is empty the DT node was
 * never populated, and an empty /sys/fs/pstore means "no driver", not
 * "nothing crashed".
 */
static void report_ramoops(FILE *m)
{
	const char *dir = "/sys/bus/platform/drivers/ramoops";
	DIR *d = opendir(dir);
	struct dirent *e;
	int bound = 0;

	if (!d) {
		fprintf(m, "ramoops driver: %s does not exist (CONFIG_PSTORE_RAM off?)\n",
			dir);
		return;
	}
	fprintf(m, "ramoops driver: bound to");
	while ((e = readdir(d))) {
		const char *n = e->d_name;

		if (n[0] == '.' || !strcmp(n, "bind") || !strcmp(n, "unbind") ||
		    !strcmp(n, "uevent") || !strcmp(n, "module"))
			continue;
		fprintf(m, " [%s]", n);
		bound++;
	}
	if (!bound)
		fprintf(m, " (NOTHING -- the DT node was not populated)");
	fprintf(m, "\n");
	closedir(d);
}

/*
 * The first stage publishes /vendor/etc/fstab.rg34xxsp so that the second
 * stage and vold can find it, and writes a receipt into /metadata (the ext4
 * partition, which survives a reboot). Its LOG() lines are not reliable --
 * userspace writes to /dev/kmsg are ratelimited by default, and fifteen of
 * them vanished on the sd32 run -- so the receipt is the only trustworthy
 * record of what that code decided. Fetch it before anything else touches
 * /dev/mmcblk0p3.
 */
static int harvest_metadata(void)
{
	const char *devs[] = { "/dev/mmcblk0p3", "/dev/block/mmcblk0p3" };
	const char *files[] = { "first-stage-publish.txt", "fstab.rg34xxsp",
				"fstab.published" };
	char p[160], dst[160];
	int i, j, copied = 0, mounted = 0;

	mkdir("/meta", 0755);
	for (i = 0; i < 2 && !mounted; i++) {
		if (access(devs[i], F_OK) != 0)
			continue;
		if (mount(devs[i], "/meta", "ext4", MS_RDONLY, NULL) == 0)
			mounted = 1;
	}
	if (!mounted) {
		char m[160];

		snprintf(m, sizeof(m),
			 "meta: could not mount the metadata partition (%s)",
			 strerror(errno));
		hb(m);
		return 0;
	}

	for (j = 0; j < 3; j++) {
		char buf[4096];
		size_t n;
		FILE *in, *o;

		snprintf(p, sizeof(p), "/meta/%s", files[j]);
		if (access(p, F_OK) != 0)
			continue;
		snprintf(dst, sizeof(dst), "/mnt/DEBUG/meta-%s", files[j]);
		in = fopen(p, "r");
		if (!in)
			continue;
		o = fopen(dst, "w");
		if (!o) {
			fclose(in);
			continue;
		}
		fprintf(o, "===== %s (left behind by the first stage) =====\n", p);
		while ((n = fread(buf, 1, sizeof(buf), in)) > 0)
			fwrite(buf, 1, n, o);
		fflush(o);
		fsync(fileno(o));
		fclose(o);
		fclose(in);
		hb(strcat(strcpy(buf, "meta: saved "), dst));
		copied++;
	}
	umount("/meta");
	if (!copied)
		hb("meta: nothing left behind (the first stage wrote no receipt)");
	return copied;
}

/*
 * Copy a file, but never more than cap bytes of it. The card's boot partition
 * is 64 MiB and the kernel already eats 35 of them, so an unbounded copy of
 * whatever a crashing framework left behind can fill it and lose the boot
 * entirely. Truncation is recorded in the header line.
 */
static int copy_capped(const char *src, const char *dst, size_t cap)
{
	char buf[4096];
	size_t n, copied = 0;
	FILE *in, *o;

	in = fopen(src, "r");
	if (!in)
		return 0;
	o = fopen(dst, "w");
	if (!o) {
		fclose(in);
		return 0;
	}
	fprintf(o, "===== %s (from the previous boot, cap %zu) =====\n", src,
		cap);
	while (copied < cap && (n = fread(buf, 1, sizeof(buf), in)) > 0) {
		if (copied + n > cap)
			n = cap - copied;
		fwrite(buf, 1, n, o);
		copied += n;
	}
	if (copied >= cap)
		fprintf(o, "\n[truncated at %zu bytes]\n", cap);
	fflush(o);
	fsync(fileno(o));
	fclose(o);
	fclose(in);
	return 1;
}

/*
 * Harvest what the framework wrote into /data on the previous boot. This is
 * how a zygote crash is going to name itself: /data is mounted read-write by
 * now, so tombstoned has somewhere to put the tombstone -- but only after the
 * machine has already gone down, which is why it is collected at the *start*
 * of the next boot.
 */
static int harvest_data(void)
{
	const char *devs[] = { "/dev/mmcblk0p5", "/dev/block/mmcblk0p5" };
	const char *dirs[] = { "tombstones", "anr", "system/dropbox",
			       "misc/logd", "recovery" };
	char d[200], src[260], dst[180];
	int i, k, mounted = 0, copied = 0;

	mkdir("/olddata", 0755);
	for (i = 0; i < 2 && !mounted; i++) {
		if (access(devs[i], F_OK) != 0)
			continue;
		if (mount(devs[i], "/olddata", "ext4", MS_RDONLY, NULL) == 0)
			mounted = 1;
	}
	if (!mounted) {
		char m[160];

		snprintf(m, sizeof(m),
			 "data: could not mount /dev/mmcblk0p5 read-only (%s)",
			 strerror(errno));
		hb(m);
		return 0;
	}

	for (k = 0; k < 5; k++) {
		DIR *dd;
		struct dirent *e;
		int per_dir = 0;

		snprintf(d, sizeof(d), "/olddata/%s", dirs[k]);
		dd = opendir(d);
		if (!dd)
			continue;
		while ((e = readdir(dd)) && per_dir < 4 && copied < 12) {
			if (e->d_name[0] == '.')
				continue;
			snprintf(src, sizeof(src), "%s/%s", d, e->d_name);
			snprintf(dst, sizeof(dst),
				 "/mnt/DEBUG/prev-%s-%s", dirs[k], e->d_name);
			{
				/* '/' is not a legal FAT file name character. */
				char *s = dst;

				while (*s) {
					if (*s == '/')
						*s = '_';
					s++;
				}
			}
			if (!copy_capped(src, dst, 384 * 1024))
				continue;
			hb(strcat(strcpy(src, "data: saved "), dst));
			per_dir++;
			copied++;
		}
		closedir(dd);
	}
	umount("/olddata");
	if (!copied)
		hb("data: the previous boot left nothing in /data/tombstones etc.");
	return copied;
}

static int harvest_pstore(void)
{
	DIR *d;
	struct dirent *e;
	int files = 0;

	/*
	 * pstorefs is not mounted by anything in this initramfs -- without
	 * this mount /sys/fs/pstore is an empty stub and the dump from the
	 * previous boot is invisible.
	 */
	mkdir("/sys/fs/pstore", 0755);
	mount("pstore", "/sys/fs/pstore", "pstore", 0, NULL);

	d = opendir("/sys/fs/pstore");
	if (!d)
		return 0;

	while ((e = readdir(d))) {
		char src[160], dst[128];
		char buf[4096];
		FILE *in, *o;
		size_t n;
		int fd;

		if (e->d_name[0] == '.')
			continue;
		snprintf(src, sizeof(src), "/sys/fs/pstore/%s", e->d_name);
		snprintf(dst, sizeof(dst), "/mnt/DEBUG/pstore-%s.txt",
			 e->d_name);

		in = fopen(src, "r");
		if (!in)
			continue;
		o = fopen(dst, "w");
		if (!o) {
			fclose(in);
			continue;
		}
		fprintf(o, "===== %s (from the previous boot) =====\n", src);
		while ((n = fread(buf, 1, sizeof(buf), in)) > 0)
			fwrite(buf, 1, n, o);
		fflush(o);
		fsync(fileno(o));
		fclose(o);
		fclose(in);
		fd = open(dst, O_RDONLY);
		if (fd >= 0) {
			close(fd);
			files++;
		}
	}
	closedir(d);
	return files;
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
static void tb(int rfd)
{
	char line[192], up[64] = "?", comm[64] = "?";
	int fd, r, n;
	char *p;

	fd = open("/proc/uptime", O_RDONLY);
	if (fd >= 0) {
		r = (int)read(fd, up, sizeof(up) - 1);
		close(fd);
		up[r > 0 ? r : 0] = 0;
	}
	fd = open("/proc/1/comm", O_RDONLY);
	if (fd >= 0) {
		r = (int)read(fd, comm, sizeof(comm) - 1);
		close(fd);
		comm[r > 0 ? r : 0] = 0;
	}
	for (p = up; *p; p++)
		if (*p == '\n' || *p == ' ')
			*p = 0;
	for (p = comm; *p; p++)
		if (*p == '\n' || *p == ' ')
			*p = 0;

	n = snprintf(line, sizeof(line), "tb: up=%s pid1=%s\n", up, comm);
	if (n > 0 && write(rfd, line, (size_t)n) > 0)
		fsync(rfd);
}

static void kmsg_logger(int kfd, int lfd, int rfd)
{
	char buf[8192];
	ssize_t n;
	struct timespec now, last;
	int dirty = 0, ticks = 0;

	clock_gettime(CLOCK_MONOTONIC, &last);
	for (;;) {
		n = read(kfd, buf, sizeof(buf));
		if (n > 0) {
			if (write(lfd, buf, (size_t)n) > 0)
				dirty++;
			/*
			 * A panic is at most a second away from the reboot and
			 * FAT keeps the file size until fsync, so a count-based
			 * flush (32 messages) can lose exactly the message that
			 * matters. Flush at most every 200 ms instead: fast
			 * enough for a panic, cheap enough for the drm storm at
			 * boot.
			 */
			clock_gettime(CLOCK_MONOTONIC, &now);
			if ((now.tv_sec - last.tv_sec) * 1000 +
			    (now.tv_nsec - last.tv_nsec) / 1000000 >= 200) {
				fsync(lfd);
				dirty = 0;
				last = now;
			}
			continue;
		}
		if (n < 0 && errno != EAGAIN && errno != EINTR)
			break;
		if (dirty) {
			fsync(lfd);
			dirty = 0;
			clock_gettime(CLOCK_MONOTONIC, &last);
		}
		usleep(100000);
		/*
		 * Once a second: prove that the kernel still schedules us and
		 * say who PID 1 is now. If these lines stop, whatever we do
		 * next took the whole SoC down; if they keep coming with
		 * pid1=init, the handover happened and Android init is alive.
		 */
		if (rfd >= 0 && ++ticks >= 10) {
			ticks = 0;
			tb(rfd);
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

/*
 * The SoC watchdog has been the executioner all along.
 *
 * Every boot since sd25 stopped 16.2 s after the last line our initramfs
 * printed, with no panic, no oops, no init shutdown message, no pstore record
 * and a stale frame on the panel: an external reset. sd40 took Android out of
 * the picture entirely (/mnt/NOSTART keeps PID 1 in the initramfs) and the
 * machine still died:
 *
 *     [12.084645] watchdog: timer re-armed for handover
 *     hb: [28.25] nostart: t=15s alive, uptime=28s          <- last line
 *
 * 12.08 + 16 = 28.08, and 16 s is WDT_MAX_TIMEOUT. The watchdog node asks for
 * "allwinner,sun50i-h616-wdt" but the driver has no h616 entry, so it binds
 * the sun6i fallback whose .wdt_key_val is 0 -- and the H616 ignores every
 * WDT_MODE write that does not carry 0x16AA0000. The driver's stop() was a
 * no-op all along, and the kernel said so: "watchdog0: watchdog did not stop!"
 *
 * So: never re-arm it at handover, and disarm it for real, then prove it by
 * reading the countdown twice. If the driver's write is still ignored, slam
 * the key into the register by hand through /dev/mem and read it again.
 */
static int wdt_fd = -1;
static pid_t wdt_pid = -1;

static void wdt_start(void)
{
	int fd = open("/dev/watchdog", O_WRONLY);
	int tmo = 16;
	int opt = WDIOS_DISABLECARD;

	if (fd < 0) {
		say("watchdog: open failed (%s), probes may be cut at 16 s\n",
		    strerror(errno));
		return;
	}
	ioctl(fd, WDIOC_SETTIMEOUT, &tmo);
	wdt_fd = fd;

	/*
	 * Stop the timer outright instead of racing it with pings. The probe
	 * stages sleep for tens of seconds while a human watches the panel, and
	 * a pinger thread that dies for any reason silently costs us the run:
	 * sd13, sd14 and sd16 were all cut off at the 16 s timeout with only
	 * part of the output written. WDIOC_SETOPTIONS/WDIOS_DISABLECARD is the
	 * documented way to disarm, and wdt_stop() re-arms it before handover so
	 * a genuinely hung Android still reboots.
	 */
	if (ioctl(fd, WDIOC_SETOPTIONS, &opt) < 0)
		say("watchdog: disable failed (%s), probes may be cut at 16 s\n",
		    strerror(errno));
	else
		say("watchdog: timer disabled for the probe\n");
}

/* Seconds left on the watchdog, or -1 if the driver will not say. */
static int wdt_timeleft(void)
{
	int left = -1;

	if (wdt_fd < 0)
		return -1;
	if (ioctl(wdt_fd, WDIOC_GETTIMELEFT, &left) < 0)
		return -1;
	return left;
}

/*
 * Write the H616 key straight into WDT_MODE.
 *
 * The driver cannot do this because its fallback reg entry has no key, and the
 * SoC drops every write to that register without 0x16AA0000 in the top half.
 * 0x30090a0 is the node's base, WDT_MODE sits at +0x18, and the page is
 * ordinary MMIO so /dev/mem will map it (unlike the no-map ramoops window).
 */
static void wdt_bang_key(void)
{
	int fd = open("/dev/mem", O_RDWR | O_SYNC);
	unsigned char *page;
	volatile unsigned int *mode;

	if (fd < 0) {
		hb("wdt: /dev/mem refused, cannot force the key");
		return;
	}
	page = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
		    0x30090000);
	if (page == MAP_FAILED) {
		close(fd);
		hb("wdt: mapping 0x30090000 failed");
		return;
	}
	mode = (volatile unsigned int *)(page + 0xb8);
	*mode = 0x16aa0000u;	/* key, enable bit clear: stopped */
	munmap(page, 4096);
	close(fd);
	hb("wdt: wrote 0x16aa0000 to 0x30090b8 by hand");
}

static void wdt_stop(void)
{
	int opt = WDIOS_DISABLECARD;
	int before, after;
	char line[160];

	if (wdt_pid > 0) {
		kill(wdt_pid, SIGKILL);
		waitpid(wdt_pid, NULL, 0);
		wdt_pid = -1;
	}
	if (wdt_fd < 0) {
		hb("wdt: no descriptor open, nothing to disarm");
		return;
	}

	before = wdt_timeleft();
	if (ioctl(wdt_fd, WDIOC_SETOPTIONS, &opt) < 0)
		snprintf(line, sizeof line, "wdt: disable ioctl failed (%s)",
			 strerror(errno));
	else
		snprintf(line, sizeof line, "wdt: disable ioctl accepted");
	hb(line);

	sleep(4);
	after = wdt_timeleft();
	snprintf(line, sizeof line,
		 "wdt: timeleft %d -> %d over 4 s (%s)", before, after,
		 (after >= 0 && before >= 0 && after >= before) ? "stopped"
							       : "STILL COUNTING");
	hb(line);

	if (!(after >= 0 && before >= 0 && after >= before)) {
		wdt_bang_key();
		sleep(4);
		after = wdt_timeleft();
		snprintf(line, sizeof line, "wdt: after the key, timeleft %d (%s)",
			 after, after < 0 ? "cannot read" : "read back");
		hb(line);
	}

	close(wdt_fd);
	wdt_fd = -1;
}

/*
 * Translate a user virtual address to a physical one through
 * /proc/self/pagemap.
 *
 * We need this because /dev/mem refuses the framebuffer: fb0 lives in the CMA
 * pool (0x7dc00000..), which is ordinary DRAM, and CONFIG_STRICT_DEVMEM makes
 * devmem_is_allowed() reject page_is_ram() pages -- mmap returns EPERM, which
 * is exactly what stage 0 hit on sd16. pagemap has no such restriction for a
 * process with CAP_SYS_ADMIN (PID 1 is root, so show_pfn is on) and answers
 * the only question that matters: is the buffer we paint the same one the
 * channel scans?
 */
static unsigned long long virt_to_phys(const void *vaddr)
{
	unsigned long long entry = 0;
	unsigned long va = (unsigned long)vaddr;
	unsigned long long pfn;
	off_t pos = (off_t)(va / 4096) * 8;
	int fd = open("/proc/self/pagemap", O_RDONLY);

	if (fd < 0)
		return 0;
	if (pread(fd, &entry, sizeof(entry), pos) != (ssize_t)sizeof(entry)) {
		close(fd);
		return 0;
	}
	close(fd);

	if (!(entry & (1ULL << 63)))		/* page not present */
		return 0;
	pfn = entry & ((1ULL << 55) - 1);	/* PM_PFRAME_MASK */
	if (!pfn)
		return 0;			/* PFN hidden without CAP_SYS_ADMIN */
	return pfn * 4096ULL + (va % 4096);
}

/*
 * Ask the hardware, one variable at a time, why the panel stays black.
 *
 * Everything the driver programs already reads back correct on sd15: the
 * primary plane lives in channel 6 with the right size, pitch, address and
 * format; BLEND_ROUTE, BLEND_MODE, OUTSIZE and BKCOLOR hold sane values;
 * tcon0 has the right timing, polarity and tri-state; PD0..PD27 are muxed to
 * lcd0; the backlight is on. Yet six different BLEND_ROUTE values, each with
 * its own solid colour in the framebuffer, changed nothing.
 *
 * That leaves two things the register dump cannot answer:
 *
 *  1. The driver asks for ALPHA_MODE_PIXEL (attribute bits 2:1 == 0), i.e.
 *     the per-pixel alpha byte is used for blending. We have been painting
 *     0x00RRGGBB, whose alpha byte is 0x00 -- fully transparent. fbcon
 *     paints the same way (the fb_var_screeninfo has transp.length == 0), so
 *     if the hardware honours that byte nothing is ever visible.
 *  2. PIPE_CTL bit 0 is PIPE_CTL_FC_EN(0) and BLEND_ATTR_FCOLOR(0) holds
 *     opaque black, so pipe 0 may be filling the whole output with black
 *     behind whatever the layer draws.
 *
 * Each stage below changes exactly one of those and holds for three seconds,
 * so a single boot tells us which one matters. Stage order is deliberate:
 * the two opaque-pixel stages come first because they are the cheapest
 * explanation, and the fill-colour stage is the one that proves whether the
 * chain downstream of the blender is alive at all.
 */
/*
 * Ask the hardware, one variable at a time, why the panel stays black.
 *
 * Everything the driver programs already reads back correct on sd15: the
 * primary plane lives in channel 6 with the right size, pitch, address and
 * format; BLEND_ROUTE, BLEND_MODE, OUTSIZE and BKCOLOR hold sane values;
 * tcon0 has the right timing, polarity and tri-state; PD0..PD27 are muxed to
 * lcd0; the backlight is on. Yet six different BLEND_ROUTE values, each with
 * its own solid colour in the framebuffer, changed nothing.
 *
 * Two observations narrow it down:
 *
 *  - On sd14 (route = layer index = 1, a channel with no layer in it) the
 *    panel showed bright lines, and later went black. So the chain from the
 *    blender to the panel does carry something and reacts to what we write.
 *  - The driver asks for ALPHA_MODE_PIXEL (attribute bits 2:1 == 0), i.e. the
 *    per-pixel alpha byte is used for blending. fb0 is XRGB8888, whose
 *    fb_var_screeninfo reports transp.length == 0, so both fbcon and our own
 *    painting leave that byte at 0x00 -- fully transparent.
 *
 * The stages below change exactly one variable each and hold for three
 * seconds, so a single boot names the culprit. Stage 1 is the cheapest
 * explanation; stage 5 is the orthogonal test that proves whether anything
 * downstream of the blender works at all.
 */
static void display_probe(void)
{
	/* 32-bit framebuffer formats, as numbered by SUN8I_MIXER_FBFMT_*. */
	static const struct {
		unsigned int fmt;
		const char *name;
	} fmts[] = {
		{ 4, "XRGB8888 (driver's current choice)" },
		{ 0, "ARGB8888" },
		{ 2, "RGBA8888" },
		{ 6, "RGBX8888" },
	};
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	int ffd = open("/dev/fb0", O_RDWR);
	int mfd = open("/dev/mem", O_RDWR);
	unsigned char *fb = MAP_FAILED;
	unsigned char *blend = MAP_FAILED;
	unsigned char *ch6 = MAP_FAILED;
	unsigned char *mix = MAP_FAILED;
	size_t stride = 0, total = 0;
	unsigned int x, y, i;
	unsigned long bpage, boff, cpage, coff, mpage, moff;
	volatile unsigned int *pipe_ctl, *fcolor, *route, *bkcolor;
	volatile unsigned int *attr, *lay_size, *pitch, *laddr;
	unsigned int o_pipe, o_fcolor, o_route, o_bk, o_attr, o_dbuff = 0;
	unsigned int fbaddr = 0;

	say("\n===== display probe (watch the panel) =====\n");
	if (ffd < 0 || mfd < 0) {
		say("(open fb0/mem failed: %s)\n", strerror(errno));
		goto out;
	}
	if (ioctl(ffd, FBIOGET_FSCREENINFO, &fix) != 0 ||
	    ioctl(ffd, FBIOGET_VSCREENINFO, &var) != 0) {
		say("(fb ioctl failed: %s)\n", strerror(errno));
		goto out;
	}
	/*
	 * smem_start is only filled in when the kernel is booted with
	 * drm_leak_fbdev_smem=1 (drm_fb_helper.c:363 otherwise sets
	 * FBINFO_HIDE_SMEM_START and drm_fbdev_dma.c:219 skips the store).
	 * With the parameter we get the real physical address of fb0 for free,
	 * which is exactly the number the channel register has to match.
	 */
	say("fb0: %s smem_start=0x%lx smem_len=%u line_length=%u bpp=%u %ux%u transp@%u/%u\n",
	    fix.id, (unsigned long)fix.smem_start, fix.smem_len,
	    fix.line_length, var.bits_per_pixel, var.xres, var.yres,
	    var.transp.offset, var.transp.length);

	stride = fix.line_length ? fix.line_length : (var.xres_virtual * 4);
	total = stride * var.yres_virtual;
	fb = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, ffd, 0);
	if (fb == MAP_FAILED) {
		say("(mmap fb0 failed: %s)\n", strerror(errno));
		goto out;
	}

	/* mixer 0 blender: base 0x1281000. Channel 6 header: 0x11c1000. */
	bpage = 0x1281000UL & ~0xfffUL;
	boff = 0x1281000UL - bpage;
	blend = mmap(NULL, boff + 0x100, PROT_READ | PROT_WRITE, MAP_SHARED,
		     mfd, (off_t)bpage);
	cpage = 0x11c1000UL & ~0xfffUL;
	coff = 0x11c1000UL - cpage;
	ch6 = mmap(NULL, coff + 0x100, PROT_READ | PROT_WRITE, MAP_SHARED,
		   mfd, (off_t)cpage);
	if (blend == MAP_FAILED || ch6 == MAP_FAILED) {
		say("(mmap mixer/planes failed: %s)\n", strerror(errno));
		goto out;
	}

	/*
	 * Mixer 0 global block: 0x1280000. Only GLOBAL_DBUFF (0x8) matters here
	 * -- the driver skips it for DE33, so stage 6 writes it by hand.
	 */
	mpage = 0x1280000UL & ~0xfffUL;
	moff = 0x1280000UL - mpage;
	mix = mmap(NULL, moff + 0x100, PROT_READ | PROT_WRITE, MAP_SHARED,
		   mfd, (off_t)mpage);
	if (mix == MAP_FAILED) {
		say("(mmap mixer global failed: %s)\n", strerror(errno));
		goto out;
	}

	pipe_ctl = (volatile unsigned int *)(blend + boff + 0x00);
	fcolor = (volatile unsigned int *)(blend + boff + 0x04);
	route = (volatile unsigned int *)(blend + boff + 0x80);
	bkcolor = (volatile unsigned int *)(blend + boff + 0x88);
	attr = (volatile unsigned int *)(ch6 + coff + 0x00);
	lay_size = (volatile unsigned int *)(ch6 + coff + 0x04);
	pitch = (volatile unsigned int *)(ch6 + coff + 0x0c);
	laddr = (volatile unsigned int *)(ch6 + coff + 0x10);

	o_pipe = *pipe_ctl;
	o_fcolor = *fcolor;
	o_route = *route;
	o_bk = *bkcolor;
	o_attr = *attr;

	say("before: pipe_ctl=0x%08x fcolor=0x%08x route=0x%08x bk=0x%08x\n",
	    o_pipe, o_fcolor, o_route, o_bk);
	say("before: ch6 attr=0x%08x size=0x%08x pitch=%u laddr=0x%08x\n",
	    o_attr, *lay_size, *pitch, *laddr);

	/*
	 * Stage 0 -- confirm that fb0 really is the buffer the channel scans.
	 * Write a magic word at the top of the framebuffer, then read the same
	 * offset back through /dev/mem at the address the channel holds. If
	 * these disagree, every "we painted the screen" conclusion so far was
	 * about a different buffer and the whole picture changes.
	 */
#define PAINT_SOLID(__v)						\
	do {								\
		for (y = 0; y < var.yres_virtual; y++) {		\
			unsigned int *row =				\
				(unsigned int *)(fb + y * stride);	\
			for (x = 0; x < var.xres_virtual; x++)		\
				row[x] = (__v);				\
		}							\
		msync(fb, total, MS_SYNC);				\
		__sync_synchronize();					\
	} while (0)

	fbaddr = *laddr;
	{
		/*
		 * Where does fb0 actually live, and is the channel pointed
		 * there?
		 *
		 * Two independent sources, because neither is reliable alone:
		 *  - fix.smem_start is only filled in when the kernel boots with
		 *    drm_leak_fbdev_smem=1 (drm_fb_helper.c:363 otherwise sets
		 *    FBINFO_HIDE_SMEM_START and drm_fbdev_dma.c:219 skips the
		 *    store); we now pass that on the command line.
		 *  - pagemap returned PFN 0 here, which is why sd18 printed
		 *    phys=0x0, decided the pages differed and never reached
		 *    stage 0b. fb0's vma comes from drm_gem_prime_mmap rather
		 *    than an anonymous page, so its pte is a PFNMAP entry that
		 *    pagemap reports without a usable frame.
		 *
		 * A mismatch means the mixer scans a buffer nobody ever painted,
		 * which alone would explain "painted every colour, panel stayed
		 * black" -- and would mean the driver is programming the wrong
		 * address.
		 */
		unsigned long long pgphys = virt_to_phys(fb);
		unsigned long long fbphys = fix.smem_start ?
					    (unsigned long long)fix.smem_start :
					    pgphys;

		say("S0: fb0 smem_start=0x%lx pagemap=0x%llx -> using 0x%llx, ch6 laddr=0x%08x -- %s\n",
		    (unsigned long)fix.smem_start, pgphys, fbphys, fbaddr,
		    (fbphys && (fbphys & ~0xfffULL) ==
		     ((unsigned long long)fbaddr & ~0xfffULL)) ?
		    "SAME PAGE" : "DIFFERENT PAGE");

		if (fbphys) {
			/*
			 * Prove the write lands before blaming the hardware:
			 * paint a magic word and read it back through the same
			 * mapping, so a later mismatch cannot be caching.
			 */
			*(volatile unsigned int *)fb = 0xdeadbeefu;
			msync(fb, total, MS_SYNC);
			__sync_synchronize();
			say("S0: wrote 0xdeadbeef, read back 0x%08x\n",
			    *(volatile unsigned int *)fb);

			/*
			 * Stage 0b -- the decisive repair, and a test in one.
			 * Point the channel at fb0's real address, paint red,
			 * hold. Red here means the rest of the pipeline is fine
			 * and the only bug left is the address the driver wrote.
			 */
			if ((fbphys & ~0xfffULL) !=
			    ((unsigned long long)fbaddr & ~0xfffULL)) {
				*laddr = (unsigned int)fbphys;
				__sync_synchronize();
				PAINT_SOLID(0xffff0000u);
				say("S0b: laddr 0x%08x -> 0x%llx (readback 0x%08x), painted RED -- 6s\n",
				    fbaddr, fbphys, *laddr);
				sleep(6);
				*laddr = fbaddr;
				__sync_synchronize();
				say("S0b: laddr restored to 0x%08x\n", *laddr);
			}
		}
	}

	/*
	 * One decisive test, and nothing else.
	 *
	 * Eight stages of three seconds each is more than anyone waits: sd16 and
	 * sd18 both ended with the panel unchanged and the log cut off mid-stage,
	 * so every "we saw nothing" after that is worthless. One colour, one
	 * register, long enough to look.
	 *
	 * The hypothesis is BLEND_ROUTE -- and the evidence says our last change
	 * to it went the wrong way. On sd14 the driver wrote layer->index (1) and
	 * the panel showed dense bright lines: garbage, but *output*. On sd15,
	 * after the driver was changed to write layer->channel (6), the same
	 * setup gave pure black: no output at all. So the blender reads a layer
	 * out of BLEND_ROUTE and pipe0 has to name the layer that holds the
	 * framebuffer, i.e. index 1, not the physical channel 6; the CCU's
	 * PORT02CHN mux is what maps layer -> channel afterwards.
	 *
	 * That also means the stripes on sd14 were real data drawn with the wrong
	 * pixel format or timing, not an empty scan -- which is a much better
	 * place to be. This stage re-tests index 1 with an opaque red buffer: if
	 * the panel shows red, the route is settled and only format/timing is
	 * left; if it shows stripes again, the buffer we paint is not the buffer
	 * the channel scans, and stage 0's answer tells us which.
	 */
	PAINT_SOLID(0xffff0000u);
	*route = 0x1;
	__sync_synchronize();
	say("D1: route 0x%08x -> 0x%08x (layer index, opaque RED) -- 8s\n",
	    o_route, *route);
	sleep(8);
	*route = o_route;
	__sync_synchronize();
	say("D1: route restored to 0x%08x\n", *route);

#undef PAINT_SOLID

	*pipe_ctl = o_pipe;
	*fcolor = o_fcolor;
	*route = o_route;
	*bkcolor = o_bk;
	*attr = o_attr;
	{
		volatile unsigned int *dbuff =
			(volatile unsigned int *)(mix + moff + 0x08);
		*dbuff = o_dbuff;
	}
	__sync_synchronize();
	say("restored: pipe_ctl=0x%08x fcolor=0x%08x route=0x%08x attr=0x%08x\n",
	    *pipe_ctl, *fcolor, *route, *attr);
	say("display probe done\n");

out:
	if (fb != MAP_FAILED)
		munmap(fb, total);
	if (blend != MAP_FAILED)
		munmap(blend, boff + 0x100);
	if (ch6 != MAP_FAILED)
		munmap(ch6, coff + 0x100);
	if (mix != MAP_FAILED)
		munmap(mix, moff + 0x100);
	if (ffd >= 0)
		close(ffd);
	if (mfd >= 0)
		close(mfd);
}

/*
 * The cheap path: paint fb0 and nothing else.
 *
 * sd21 settled the two questions this probe existed for. With the v2 panel
 * firmware the panel actually scans -- the user saw the Linux penguin logo,
 * then the solid red fill -- and after the probe died the red was still on the
 * glass, i.e. the framebuffer, the blender and the tcon are all live. What was
 * still not proven is that PID 1 can get past the probe and hand over to
 * Android at all, so this stage keeps only the safe half (mmap fb0, msync --
 * exactly what painted the red the user saw) and touches no register.
 *
 * Red = debug-init is running and the framebuffer path is live; black hands
 * the panel back so that whatever appears next is Android's, not ours.
 */
static void paint_test(void)
{
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	int ffd = open("/dev/fb0", O_RDWR);
	unsigned char *fb;
	size_t stride, total;
	unsigned int x, y;

	if (ffd < 0) {
		say("paint: open /dev/fb0 failed: %s\n", strerror(errno));
		return;
	}
	if (ioctl(ffd, FBIOGET_FSCREENINFO, &fix) != 0 ||
	    ioctl(ffd, FBIOGET_VSCREENINFO, &var) != 0) {
		say("paint: fb ioctl failed: %s\n", strerror(errno));
		close(ffd);
		return;
	}
	stride = fix.line_length ? fix.line_length : (var.xres_virtual * 4);
	total = stride * var.yres_virtual;
	fb = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, ffd, 0);
	if (fb == MAP_FAILED) {
		say("paint: mmap failed: %s\n", strerror(errno));
		close(ffd);
		return;
	}

	for (y = 0; y < var.yres_virtual; y++) {
		unsigned int *row = (unsigned int *)(fb + y * stride);

		for (x = 0; x < var.xres_virtual; x++)
			row[x] = 0xffff0000u;
	}
	msync(fb, total, MS_SYNC);
	__sync_synchronize();
	say("paint: solid RED on %ux%u, holding 1s\n", var.xres, var.yres);
	sleep(1);

	for (y = 0; y < var.yres_virtual; y++) {
		unsigned int *row = (unsigned int *)(fb + y * stride);

		for (x = 0; x < var.xres_virtual; x++)
			row[x] = 0xff000000u;
	}
	msync(fb, total, MS_SYNC);
	__sync_synchronize();
	say("paint: black, releasing the panel to Android\n");

	munmap(fb, total);
	close(ffd);
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

/*
 * What does the driver itself think, and did anything reset us?
 *
 * CONFIG_WATCHDOG_SYSFS is off in this kernel, so /sys/class/watchdog does
 * not exist (sd26 reported exactly that). The ioctls are the only way in:
 * GETTIMEOUT proves the handle is the 16 s timer, GETTIMELEFT shows whether
 * it is still counting down after WDIOS_DISABLECARD, and GETBOOTSTATUS says
 * whether the previous reset came from the watchdog -- the difference between
 * a kernel panic that panic=10 rebooted and a hardware reset we never saw.
 */
static void report_wdt_ioctl(const char *tag)
{
	char line[224];
	int tmo = -1, left = -1, bs = -1;
	int r1, r2, r3;

	if (wdt_fd < 0) {
		hb("wdt: no open descriptor to ask");
		return;
	}
	r1 = ioctl(wdt_fd, WDIOC_GETTIMEOUT, &tmo);
	r2 = ioctl(wdt_fd, WDIOC_GETTIMELEFT, &left);
	r3 = ioctl(wdt_fd, WDIOC_GETBOOTSTATUS, &bs);
	snprintf(line, sizeof(line),
		 "wdt[%s]: timeout=%d(rc %d) timeleft=%d(rc %d) bootstatus=0x%x(rc %d)",
		 tag, tmo, r1, left, r2, bs, r3);
	hb(line);
}

/*
 * What does first-stage init compare against androidboot.boot_devices?
 *
 * It creates /dev/block/by-name/<part> only for block devices whose platform
 * device counts as a "boot device" (system/core/init/devices.cpp: the
 * is_boot_device test gates that symlink), and the string it tests is the
 * sysfs device path with "/devices/platform/" stripped. On this board the mmc
 * lives under the "soc" platform device, so the string is "soc/4020000.mmc",
 * while the cmdline had been saying "4020000.mmc". With no match the symlink
 * is never created, and first-stage mount dies on exactly what sd29's kmsg
 * shows: "realpath failed: /dev/block/by-name/super".
 *
 * The cmdline now carries both spellings, so this is only a receipt. Keep it
 * to readlink() into a small stack buffer: sd30's first attempt used realpath()
 * plus stdio here, and that alone was enough to abort PID 1 before it wrote
 * anything -- ten boots in two minutes before anything reached the card.
 */
static void report_boot_device(void)
{
	char buf[128], line[256];
	ssize_t n;

	n = readlink("/sys/bus/platform/devices/4020000.mmc", buf, sizeof(buf) - 1);
	if (n < 0) {
		hb("platdev: /sys/bus/platform/devices/4020000.mmc is not there");
		return;
	}
	buf[n > 0 ? n : 0] = 0;
	snprintf(line, sizeof(line), "platdev: 4020000.mmc -> %s", buf);
	hb(line);
}

/* ---- does the machine die of time, or of what we do? ---------------- */

/*
 * Every run so far has stopped somewhere between 8.6 s and 12 s, sometimes
 * while painting, sometimes while handing over -- never at the same
 * instruction. A death that follows the clock rather than the code points at
 * the watchdog: sunxi-wdt arms a 16 s timer when it probes at 0.266 s, and
 * opening /dev/watchdog may start it again even if we then try to disarm it.
 *
 * So sit still for a while and say once a second that we are alive, together
 * with what the driver thinks is left on the timer. If the log stops around
 * 16 s with timeleft counting down, the watchdog is the killer and the paint
 * test is innocent. If timeleft stops counting and we sail past 16 s, the
 * timer is really off and whatever kills us does it in response to our code.
 */
static void soak(int seconds)
{
	char line[200];
	int t, left, rc;

	for (t = 1; t <= seconds; t++) {
		sleep(1);
		left = -1;
		rc = ioctl(wdt_fd, WDIOC_GETTIMELEFT, &left);
		snprintf(line, sizeof(line),
			 "soak: t=%ds alive, wdt timeleft=%d (rc %d)", t, left,
			 rc);
		say("%s\n", line);
		hb(line);
	}
}

/* ---- watching /init.android die as an ordinary child ---------------- */

/*
 * Android init installs handlers for SIGSEGV/SIGBUS/SIGABRT/... at the very
 * top of FirstStageMain. As PID 1 they call InitFatalReboot(), which with
 * androidboot.init_fatal_panic unset does RebootSystem(RESTART2,
 * "bootloader") -- a reset the kernel may well end up converting into a halt,
 * leaving no kernel message, no panic, no watchdog reset and a stale frame on
 * the panel. That is exactly what every run so far looked like.
 *
 * As a *child*, the very same handler does `_exit(signal)`, so the parent gets
 * the signal number back through waitpid(). Run init once that way first:
 * whatever kills it names itself in the exit status, and nothing about the
 * display or the kernel's restart path can hide it.
 *
 * The child gets its own mount namespace so that the tmpfs init stacks on
 * /dev, /proc and /mnt stays out of ours -- otherwise the rest of this log
 * would be written into a filesystem nobody will ever read again.
 */
static const char *syscall_name(long nr)
{
	switch (nr) {
	case 29: return "ioctl";
	case 33: return "mknodat";
	case 34: return "mkdirat";
	case 40: return "mount";
	case 43: return "statfs";
	case 53: return "fchmodat";
	case 56: return "openat";
	case 64: return "write";
	case 66: return "writev";
	case 78: return "readlinkat";
	case 98: return "futex";
	case 123: return "sched_getaffinity";
	case 159: return "setgroups";
	case 160: return "uname";
	case 167: return "prctl";
	case 220: return "clone";
	case 221: return "execve";
	case 222: return "mmap";
	case 226: return "mprotect";
	case 260: return "wait4";
	case 273: return "finit_module";
	case 278: return "getrandom";
	case 291: return "statx";
	default: return NULL;
	}
}

static void read_first_line(const char *path, char *buf, size_t len,
			    int keep_newline)
{
	FILE *f = fopen(path, "r");
	size_t n;

	buf[0] = 0;
	if (!f)
		return;
	n = fread(buf, 1, len - 1, f);
	buf[n] = 0;
	fclose(f);
	if (!keep_newline) {
		char *nl = strchr(buf, '\n');

		if (nl)
			*nl = 0;
	}
}

static void trace_child(pid_t pid, int sec)
{
	char path[64], stat[256], wchan[80], sysc[192], line[400];
	char state = '?';
	char *p;

	snprintf(path, sizeof(path), "/proc/%d/stat", (int)pid);
	read_first_line(path, stat, sizeof(stat), 0);
	/* the comm field is parenthesised and may itself contain spaces */
	p = strrchr(stat, ')');
	if (p && p[1] == ' ' && p[2])
		state = p[2];

	snprintf(path, sizeof(path), "/proc/%d/wchan", (int)pid);
	read_first_line(path, wchan, sizeof(wchan), 0);
	if (!wchan[0])
		strcpy(wchan, "-");

	snprintf(path, sizeof(path), "/proc/%d/syscall", (int)pid);
	read_first_line(path, sysc, sizeof(sysc), 0);
	if (!sysc[0])
		strcpy(sysc, "-");

	{
		long nr = strtol(sysc, NULL, 10);
		const char *name = syscall_name(nr);

		snprintf(line, sizeof(line),
			 "stage 4b: t=%ds state=%c wchan=%s syscall=%s(%ld)",
			 sec, state, wchan, name ? name : "?", nr);
	}
	hb(line);
}

static void child_probe(void)
{
	pid_t pid;
	int i, alive = 1;
	char line[256];

	hb("stage 4b: child probe -- /init.android as a normal process");
	pid = fork();
	if (pid == 0) {
		int fd;

		/*
		 * The child writes its own breadcrumbs: the parent cannot tell a
		 * child that never started from a child that died silently, and
		 * child-out.txt came back empty on sd25.
		 */
		hb("stage 4c: child alive, about to unshare");
		if (unshare(CLONE_NEWNS) != 0)
			_exit(125);
		mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL);
		fd = open("/mnt/DEBUG/child-out.txt",
			  O_WRONLY | O_CREAT | O_TRUNC, 0644);
		if (fd >= 0) {
			dup2(fd, 1);
			dup2(fd, 2);
			if (fd > 2)
				close(fd);
		}
		hb("stage 4d: child about to execl /init.android");
		/*
		 * Same courtesy as the real handover: init mounts these itself
		 * and an EBUSY is fatal. This namespace is the child's own, so
		 * clearing them cannot touch the parent's view of the card.
		 */
		umount2("/dev", MNT_DETACH);
		umount2("/proc", MNT_DETACH);
		umount2("/sys", MNT_DETACH);
		umount2("/mnt", MNT_DETACH);
		execl("/init.android", "/init", NULL);
		_exit(127);
	}
	if (pid < 0) {
		hb("stage 4b: fork for the child probe failed");
		return;
	}
	hb("stage 4b: child forked, tracing it");

	for (i = 0; i < 80; i++) {
		int st = 0;

		if (waitpid(pid, &st, WNOHANG) == pid) {
			alive = 0;
			if (WIFEXITED(st))
				snprintf(line, sizeof(line),
					 "stage 4b: init.android exited with %d (125 = no namespaces, 127 = exec failed)",
					 WEXITSTATUS(st));
			else if (WIFSIGNALED(st))
				snprintf(line, sizeof(line),
					 "stage 4b: init.android died on signal %d",
					 WTERMSIG(st));
			else
				snprintf(line, sizeof(line),
					 "stage 4b: init.android status 0x%x",
					 st);
			hb(line);
			break;
		}
		if (i % 4 == 0)
			trace_child(pid, i / 4);
		usleep(250000);
	}
	if (alive) {
		hb("stage 4b: init.android still alive at 20s, killing it");
		kill(pid, SIGKILL);
		waitpid(pid, NULL, 0);
	}
}

static int logger_started;

/*
 * Start the /dev/kmsg follower now rather than at handover time.
 *
 * The runs die somewhere between the paint test and the exec, and the
 * snapshot on the card only covers the kernel up to the moment it was taken.
 * With the follower running from here on, DEBUG/kmsg-tail.txt holds whatever
 * the kernel said right up to the freeze -- including any panic, which is the
 * one thing that would settle this.
 */
static void start_kmsg_logger(void)
{
	int kfd = open("/dev/kmsg", O_RDONLY | O_NONBLOCK);
	int lfd = open(kmsg_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
	int rfd = open(trace_path, O_WRONLY | O_APPEND | O_CREAT, 0644);
	pid_t pid;

	if (kfd < 0 || lfd < 0) {
		hb("logger: cannot start (/dev/kmsg or the log file)");
		return;
	}
	/*
	 * Start at the *new* messages only. Reading the ring buffer from the
	 * beginning means replaying a few hundred kilobytes of boot log into
	 * FAT, which on sd26 ate the whole second the machine had left and
	 * buried the panic. DEBUG/dmesg.txt already holds the boot log.
	 */
	lseek(kfd, 0, SEEK_END);
	pid = fork();
	if (pid == 0) {
		kmsg_logger(kfd, lfd, rfd);
		_exit(0);
	}
	logger_started = (pid > 0);
	hb(logger_started ? "logger: kmsg follower running from the paint test on"
			   : "logger: fork failed");
}

/* DSH-ramoops-marker */
/*
 * Did this boot's DRAM survive the reset that killed the last one?
 *
 * Our ramoops node reserves 1 MiB at 0x7ff00000 with no-map, and the two
 * zones only use the first 0xa0000 of it, so the tail is ours to scribble
 * on. A magic word here is a cheaper and more honest test than pstore:
 * if it comes back, DRAM survives and the empty pstore means nothing ever
 * wrote to it (so the crash is not a plain panic path); if it is gone,
 * the bootloader wiped the RAM and pstore_ram can never work on this board.
 */
static void ramoops_marker(FILE *m)
{
	FILE *iomem;
	char line[192];
	int seen = 0;

	/*
	 * sd38 read this reservation straight out of /dev/mem and PID 1 took
	 * SIGBUS for it, five boots in a row. The region is reserved with
	 * no-map, so it is not in the linear map and a userspace access is an
	 * external abort -- a bus error, not a read of stale bytes. Nothing in
	 * this program may touch it again.
	 *
	 * What is left is the kernel's own view of the reservation.
	 */
	fprintf(m, "ramo: 0x7ffc0000 sits inside the no-map ramoops reservation, "
		   "so userspace cannot read it (sd38 proved that with SIGBUS)\n");
	iomem = fopen("/proc/iomem", "r");
	if (!iomem) {
		fprintf(m, "ramo: no /proc/iomem (%s)\n", strerror(errno));
		return;
	}
	while (fgets(line, sizeof(line), iomem) != NULL) {
		if (strstr(line, "ramoops") == NULL)
			continue;
		fprintf(m, "ramo: iomem %s", line);
		seen++;
	}
	fclose(iomem);
	fprintf(m, "ramo: %d ramoops region(s) in iomem\n", seen);
}

int main(void)
{
	const char *bootdev = NULL;
	char logpath[128];
	int i, logged = 0, pstore_files = 0, meta_files = 0, data_files = 0;

	mkdir("/proc", 0555);
	mkdir("/sys", 0555);
	mkdir("/dev", 0755);
	mkdir("/mnt", 0755);
	mkdir("/tmp", 0777);
	mount("proc", "/proc", "proc", 0, NULL);
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);

	con = fopen("/dev/console", "w");

	install_fault_handlers();
	stage = 0;
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
	trace_path_init();

	/*
	 * First thing on every boot: did the previous run leave a crash dump
	 * behind? Harvested before ran.txt is recreated, and reported in it.
	 */
	pstore_files = harvest_pstore();
	meta_files = harvest_metadata();
	data_files = harvest_data();

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
		FILE *m = fopen(trace_path, "w");

		if (m) {
			fprintf(m, "debug-init reached main, boot partition %s\n",
				bootdev);
			fprintf(m, "trace file %s\n", trace_path);
			fprintf(m, "pstore files from the previous boot: %d%s\n",
				pstore_files,
				pstore_files ? " (see DEBUG/pstore-*.txt)"
					     : " (no crash was salvaged)");
			fprintf(m, "metadata receipts from the previous boot: %d%s\n",
				meta_files,
				meta_files ? " (see DEBUG/meta-*.txt)"
					   : " (nothing on /dev/mmcblk0p3)");
			fprintf(m, "data receipts from the previous boot: %d%s\n",
				data_files,
				data_files ? " (see DEBUG/prev-*.txt)"
					   : " (nothing in /data/tombstones)");
			ramoops_marker(m);
			report_ramoops(m);
			fflush(m);
			fsync(fileno(m));
			fclose(m);
		} else {
			say("cannot create %s: %s\n", trace_path,
			    strerror(errno));
		}
		sync();
	}

	stage = 1;
	hb("stage 1: main reached, boot partition mounted");

	snprintf(logpath, sizeof(logpath), "%s", bootlog_path);

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
			 * Keep a kernel-message follower alive from here on, so
			 * a freeze during the paint test or the child probe
			 * still leaves the kernel's last words on the card.
			 */
			start_kmsg_logger();
			report_wdt_ioctl("before-arm");
			/*
			 * Open the watchdog so its countdown is readable, and
			 * try to disarm it here. On this SoC the driver's
			 * disarm is a no-op, so the real one happens at
			 * handover, where wdt_stop() verifies it. Opening it
			 * arms a 16 s timer, so the stages below have to reach
			 * the handover well inside that window.
			 */
			wdt_start();
			report_wdt_ioctl("after-disarm");
			report_boot_device();
			/*
			 * The countdown is the machine's deadline: it reads 16
			 * at handover and the SoC resets at 16 + 12 s of
			 * wall clock. Three seconds is enough to see it move
			 * in the trace without making every boot slower.
			 */
			soak(3);
			/*
			 * Two modes, chosen by a file on the FAT partition so no
			 * rebuild is needed to switch:
			 *
			 *  - REGPROBE present  -> the sd21 register/route probe
			 *    (mmio dump + blender route experiment). It is the
			 *    thing that killed the sd21 run: the log stops right
			 *    after "D1: ... -- 8s" and nothing since has been
			 *    written, so it is kept behind an opt-in file.
			 *  - otherwise         -> paint fb0 red for 3 s, black,
			 *    hand straight over to Android. Safe half only.
			 *
			 * sd21 already proved the panel works with the v2 panel
			 * firmware: the user saw the penguin logo and then the
			 * red fill, and the red was still on the glass when the
			 * probe died. What is still unproven is that PID 1 gets
			 * to execl("/init.android") at all.
			 */
			if (access("/mnt/REGPROBE", F_OK) == 0) {
				stage = 2;
				hb("stage 2: register probe (REGPROBE present)");
				mmio_dump();
				mmio_channels();
				/*
				 * Most likely to abort, so after the two
				 * structured dumps: sweep the big blocks for
				 * words that look like CMA addresses.
				 */
				mmio_scan("planes", 0x1100000, 0x180000);
				mmio_scan("mixer0", 0x1280000, 0x20000);
				display_probe();
			} else if (access("/mnt/NOPAINT", F_OK) == 0) {
				/*
				 * Control run: the soak happened, touch nothing
				 * on the display, hand straight over. If this
				 * one survives, the paint is what kills us.
				 */
				stage = 3;
				hb("stage 3: paint skipped (NOPAINT present)");
			} else {
				stage = 3;
				hb("stage 3: fb0 paint test");
				paint_test();
			}
			stage = 4;
			hb("stage 4: probe returned");
			report_wdt_ioctl("before-disarm");
			/*
			 * Never re-arm: Android has no keeper for it, so a
			 * running watchdog ends the boot 16 s in, which is
			 * exactly what every trace since sd25 shows.
			 */
			wdt_stop();
			/* wdt_stop() verified the countdown and closed the fd. */
			fflush(o);
			fsync(fileno(o));
			fclose(o);
			out = NULL;
			sync();
		}
	}

/*
	 * Before turning PID 1 into init, let it run once as a child: that is
	 * the only configuration in which a fatal signal reports itself.
	 */
	child_probe();

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
	hb("stage 5: handover begin");
	if (logger_started)
		hb("stage 6: kmsg logger already running (started before the paint)");
	else
		start_kmsg_logger();

	if (con) {
		fclose(con);
		con = NULL;
	}
	hb("stage 7: console closed");

	/*
	 * Can this kernel still fork/exec/wait a brand-new static ELF after the
	 * display has been painted? Nothing about Android's init matters if the
	 * answer is no, and sd22 could not tell the two apart.
	 */
	{
		pid_t p = fork();
		char line[160];

		if (p == 0) {
			execl("/init.test", "/init.test", NULL);
			_exit(127);
		}
		if (p > 0) {
			int st = 0;

			waitpid(p, &st, 0);
			if (WIFEXITED(st))
				snprintf(line, sizeof(line),
					 "stage 7b: exec test exited %d (42 = it ran)",
					 WEXITSTATUS(st));
			else if (WIFSIGNALED(st))
				snprintf(line, sizeof(line),
					 "stage 7b: exec test killed by signal %d",
					 WTERMSIG(st));
			else
				snprintf(line, sizeof(line),
					 "stage 7b: exec test state 0x%x", st);
			hb(line);
		} else {
			hb("stage 7b: fork for exec test failed");
		}
	}

	/*
	 * Pre-flight the binary we are about to turn PID 1 into. sd22 stopped
	 * with "stage 5" on the card and not one kernel message after the
	 * paint test, so the run died somewhere between that line and the
	 * first thing Android init would have logged. Record what the file
	 * actually is before the exec; if this is the last breadcrumb, the
	 * exec itself is the killer.
	 */
	{
		struct stat st;
		unsigned char magic[4] = { 0, 0, 0, 0 };
		char line[192];
		int fd = open("/init.android", O_RDONLY);

		if (fd >= 0) {
			ssize_t r = read(fd, magic, sizeof(magic));

			(void)r;
			close(fd);
			if (stat("/init.android", &st) != 0)
				st.st_size = -1;
			snprintf(line, sizeof(line),
				 "stage 8: init.android xok=%d size=%ld magic=%02x%02x%02x%02x",
				 access("/init.android", X_OK) == 0,
				 (long)st.st_size, magic[0], magic[1], magic[2],
				 magic[3]);
		} else {
			snprintf(line, sizeof(line),
				 "stage 8: init.android unreadable: %s",
				 strerror(errno));
		}
		hb(line);
	}

	stage = 5;
	say("handing over to /init.android\n");
	hb("stage 9: about to execl");

	/*
	 * Give the root back the way Android's init expects it.
	 *
	 * FirstStageMain mounts /dev (tmpfs), /dev/pts, /proc, /sys, and /mnt
	 * itself, and it treats any CHECKCALL failure as fatal: the console
	 * said it in as many words -- "Init encountered errors starting first
	 * stage, aborting". Every one of those mountpoints was still ours, so
	 * each mount came back EBUSY; devtmpfs also made every
	 * mknod("/dev/kmsg"/"random"/"urandom"/"ptmx"/"null") an EEXIST.
	 * selinuxfs was the third failure, and that one is the kernel's job
	 * (CONFIG_SECURITY_SELINUX, added in sd29).
	 *
	 * The last breadcrumb is already fsync'd to the card, so nothing of
	 * ours is needed past this point.
	 */
	/*
	 * /mnt/NOSTART: stay in the initramfs and do nothing at all.
	 *
	 * The machine has now died at 26-29 s six boots in a row, always in the
	 * middle of ordinary framework work, with nothing in the kernel log to
	 * explain it -- the follower is killed by the same event, so its last
	 * 200 ms are lost. The obvious question is whether the kernel can
	 * survive on its own, with no Android on top of it. This switch answers
	 * that in one boot: with it set, PID 1 never execs the Android init and
	 * simply heartbeats to the card. If the heartbeats keep coming, the
	 * kernel is fine and something in userspace pulls the trigger; if they
	 * stop at 28 s, the trigger is in the kernel and userspace is innocent.
	 */
	if (access("/mnt/NOSTART", F_OK) == 0) {
		char line[128];
		int beat = 0;
		struct timespec ts;
		long up;

		hb("stage 9c: NOSTART is set, staying in the initramfs");
		say("NOSTART: not handing over to Android, heartbeating\n");
		for (;;) {
			sleep(1);
			beat++;
			clock_gettime(CLOCK_MONOTONIC, &ts);
			up = ts.tv_sec;
			snprintf(line, sizeof(line),
				 "nostart: t=%ds alive, uptime=%lds (no Android)",
				 beat, up);
			hb(line);
			if (beat % 10 == 0)
				say("%s\n", line);
		}
	}

	hb("stage 9b: unmounting our /dev, /proc, /sys and /mnt");
	/*
	 * We got all the way here, so no fault loop is in progress: clear the
	 * counter the fault handler uses to stop rebooting forever.
	 */
	unlink("/mnt/FAULTCOUNT");
	umount2("/dev", MNT_DETACH);
	umount2("/proc", MNT_DETACH);
	umount2("/sys", MNT_DETACH);
	umount2("/mnt", MNT_DETACH);
	sync();

	execl("/init.android", "/init", NULL);

	/* Only reached if the Android init is missing -- keep the log, then stop. */
	hb("stage 10: execl RETURNED");
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
