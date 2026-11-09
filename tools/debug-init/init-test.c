/*
 * exec sanity check for the RG34XXSP debug initramfs.
 *
 * debug-init forks, execs this, and waits for it right before handing PID 1 to
 * Android init. If this runs, the kernel can still fork/exec/wait a brand-new
 * AArch64 ELF from the ramfs after the display has been painted -- which is
 * the one thing sd22 left ambiguous, because the card's log simply stopped.
 *
 * It must stay statically linked and must not touch the display, /dev/mem or
 * the watchdog: its only job is to leave a line behind and exit with a value
 * the parent can report.
 */
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

int main(void)
{
	char buf[128];
	int fd = open("/mnt/DEBUG/ran.txt", O_WRONLY | O_APPEND);
	int n;

	if (fd >= 0) {
		n = snprintf(buf, sizeof(buf),
			     "exectest: alive as pid %d, exec of a fresh ELF works\n",
			     (int)getpid());
		if (n > 0 && write(fd, buf, (size_t)n) < 0)
			;	/* nothing to do about it here */
		fsync(fd);
		close(fd);
	}
	return 42;
}