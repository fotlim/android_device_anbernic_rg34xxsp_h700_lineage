// dsh-joymouse: turn the RG34XXSP built-in gamepad ("H700 Gamepad") into a
// virtual mouse via /dev/uinput, so the no-touchscreen build can be driven.
//
// sd124: adds a self-check log (/metadata/dsh/joymouse.log), creates /dev/uinput
// if ueventd didn't, and sets INPUT_PROP_POINTER so Android classifies the
// virtual device as a mouse. Left stick = pointer, face buttons = click.
//
// Build: aarch64-linux-gnu-gcc -static -O2 -o dsh-joymouse joymouse.c

#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <linux/input.h>
#include <linux/uinput.h>

static void jlog(const char *fmt, ...) {
    char buf[512];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    int k = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (k >= 0) { dprintf(k, "dsh-joymouse: %s\n", buf); close(k); }
    int f = open("/metadata/dsh/joymouse.log", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (f >= 0) { dprintf(f, "dsh-joymouse: %s\n", buf); close(f); }
}

static int open_uinput(void) {
    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd >= 0) return fd;
    if (errno == ENOENT) {
        // ueventd may not create /dev/uinput; make it (misc char 10:223).
        unlink("/dev/uinput");
        if (mknod("/dev/uinput", S_IFCHR | 0666, makedev(10, 223)) != 0)
            jlog("mknod /dev/uinput failed: %d (%s)", errno, strerror(errno));
        else
            jlog("created /dev/uinput node (10:223)");
        chmod("/dev/uinput", 0666);
        fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    }
    return fd;
}

static int find_gamepad(char *path, size_t plen) {
    for (int i = 0; i < 32; i++) {
        char p[64];
        snprintf(p, sizeof(p), "/dev/input/event%d", i);
        int fd = open(p, O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        unsigned long absbits[(ABS_MAX / 8) + 2] = {0};
        unsigned long keybits[(KEY_MAX / 8) + 2] = {0};
        int hasx = 0, hasy = 0, hasbtn = 0;
        if (ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(absbits)), absbits) >= 0) {
            hasx = (absbits[ABS_X / 8] >> (ABS_X % 8)) & 1;
            hasy = (absbits[ABS_Y / 8] >> (ABS_Y % 8)) & 1;
        }
        if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keybits)), keybits) >= 0) {
            hasbtn = ((keybits[BTN_SOUTH / 8] >> (BTN_SOUTH % 8)) & 1) ||
                     ((keybits[BTN_GAMEPAD / 8] >> (BTN_GAMEPAD % 8)) & 1) ||
                     ((keybits[BTN_JOYSTICK / 8] >> (BTN_JOYSTICK % 8)) & 1);
        }
        if (hasx && hasy && hasbtn) {
            char name[128] = {0};
            ioctl(fd, EVIOCGNAME(sizeof(name) - 1), name);
            snprintf(path, plen, "%s", p);
            jlog("gamepad at %s (name=%s) absx=%d absy=%d btn=%d", p, name, hasx, hasy, hasbtn);
            return fd;
        }
        close(fd);
    }
    return -1;
}

static int axis_step(int val, int center, int half, int speed) {
    if (half <= 0) return 0;
    double n = (double)(val - center) / (double)half;
    if (n > 1) n = 1; if (n < -1) n = -1;
    if (n > -0.18 && n < 0.18) return 0;
    n = (n > 0) ? (n - 0.18) / 0.82 : (n + 0.18) / 0.82;
    int s = (int)(n * speed);
    if (s == 0) s = (n > 0) ? 1 : -1;
    return s;
}

int main(void) {
    jlog("start");
    char gp[64];
    int gi = -1;
    for (int t = 0; t < 600; t++) {
        gi = find_gamepad(gp, sizeof(gp));
        if (gi >= 0) break;
        if (t == 0) jlog("no gamepad yet, waiting...");
        sleep(1);
    }
    if (gi < 0) { jlog("no gamepad after wait, exit"); return 1; }

    struct input_absinfo ax = {0}, ay = {0};
    ioctl(gi, EVIOCGABS(ABS_X), &ax);
    ioctl(gi, EVIOCGABS(ABS_Y), &ay);
    jlog("ABS_X %d..%d  ABS_Y %d..%d", ax.minimum, ax.maximum, ay.minimum, ay.maximum);
    int cx = (ax.minimum + ax.maximum) / 2, hx = (ax.maximum - ax.minimum) / 2;
    int cy = (ay.minimum + ay.maximum) / 2, hy = (ay.maximum - ay.minimum) / 2;
    if (hx <= 0) hx = 1; if (hy <= 0) hy = 1;

    int ui = open_uinput();
    if (ui < 0) { jlog("open /dev/uinput failed: %d (%s), exit", errno, strerror(errno)); return 1; }
    ioctl(ui, UI_SET_EVBIT, EV_KEY);
    ioctl(ui, UI_SET_EVBIT, EV_REL);
    ioctl(ui, UI_SET_EVBIT, EV_SYN);
    ioctl(ui, UI_SET_KEYBIT, BTN_LEFT);
    ioctl(ui, UI_SET_KEYBIT, BTN_RIGHT);
    ioctl(ui, UI_SET_KEYBIT, BTN_MIDDLE);
    ioctl(ui, UI_SET_RELBIT, REL_X);
    ioctl(ui, UI_SET_RELBIT, REL_Y);
    ioctl(ui, UI_SET_PROPBIT, INPUT_PROP_POINTER);   // classify as pointer/mouse

    struct uinput_setup us = {0};
    us.id.bustype = BUS_USB;
    us.id.vendor = 0x484b;
    us.id.product = 0x14df;
    us.id.version = 1;
    snprintf(us.name, sizeof(us.name), "Dsh Joy Mouse");
    if (ioctl(ui, UI_DEV_SETUP, &us) < 0 || ioctl(ui, UI_DEV_CREATE) < 0) {
        jlog("uinput create failed: %d (%s), exit", errno, strerror(errno)); return 1;
    }
    jlog("virtual mouse created; stick->pointer, A/B->left, X->right, Y->middle");

    int cur_x = cx, cur_y = cy;
    long abs_events = 0, rel_events = 0, btn_events = 0, tick = 0;
    for (;;) {
        struct input_event ev;
        while (read(gi, &ev, sizeof(ev)) == (ssize_t)sizeof(ev)) {
            if (ev.type == EV_ABS) {
                abs_events++;
                if (ev.code == ABS_X) cur_x = ev.value;
                else if (ev.code == ABS_Y) cur_y = ev.value;
            } else if (ev.type == EV_KEY) {
                int btn = -1;
                if (ev.code == BTN_SOUTH || ev.code == BTN_EAST) btn = BTN_LEFT;
                else if (ev.code == BTN_NORTH) btn = BTN_RIGHT;
                else if (ev.code == BTN_WEST) btn = BTN_MIDDLE;
                if (btn >= 0) {
                    btn_events++;
                    struct input_event e = {0};
                    e.type = EV_KEY; e.code = btn; e.value = ev.value ? 1 : 0;
                    if (write(ui, &e, sizeof(e)) < 0) {}
                    e.type = EV_SYN; e.code = SYN_REPORT; e.value = 0;
                    if (write(ui, &e, sizeof(e)) < 0) {}
                }
            }
        }
        int dx = axis_step(cur_x, cx, hx, 12);
        int dy = axis_step(cur_y, cy, hy, 12);
        if (dx || dy) {
            struct input_event e = {0};
            if (dx) { e.type = EV_REL; e.code = REL_X; e.value = dx; if (write(ui, &e, sizeof(e)) < 0) {} }
            if (dy) { e.type = EV_REL; e.code = REL_Y; e.value = dy; if (write(ui, &e, sizeof(e)) < 0) {} }
            e.type = EV_SYN; e.code = SYN_REPORT; e.value = 0;
            if (write(ui, &e, sizeof(e)) < 0) {}
            rel_events++;
        }
        if ((++tick % 500) == 0)
            jlog("stats abs=%ld rel=%ld btn=%ld curx=%d cury=%d", abs_events, rel_events, btn_events, cur_x, cur_y);
        usleep(8000);
    }
    return 0;
}
