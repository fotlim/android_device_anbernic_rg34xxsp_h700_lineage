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
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>
#include <linux/fs.h>

#ifndef SYSLOG_ACTION_READ_ALL
#define SYSLOG_ACTION_READ_ALL 3
#endif
#ifndef SYSLOG_ACTION_SIZE_BUFFER
#define SYSLOG_ACTION_SIZE_BUFFER 10
#endif

static FILE *out;
static FILE *con;

static void say(const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	if (out) {
		vfprintf(out, fmt, ap);
		fflush(out);
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
	dump_dir("/sys/bus/platform/drivers/panel-mipi-dpi-spi");
	dump_dir("/sys/class/backlight");
	/* debugfs is where panel-mipi exposes the decoded firmware, if it got
	 * that far. Mounting it here is harmless -- Android remounts /sys later. */
	if (mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL) == 0 ||
	    errno == EBUSY)
		dump_dir("/sys/kernel/debug/dri/0");
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
	snprintf(logpath, sizeof(logpath), "/mnt/DEBUG/dmesg.txt");

	{
		FILE *o = fopen(logpath, "w");

		if (!o) {
			say("cannot create %s: %s\n", logpath, strerror(errno));
			goto handover;
		}
		snapshot(o);
		fclose(o);
		logged = 1;
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
