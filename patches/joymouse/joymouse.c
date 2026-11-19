// dsh-joymouse: turn the RG34XXSP built-in gamepad ("H700 Gamepad") into a
// virtual mouse via /dev/uinput, so the no-touchscreen Android build can be
// driven: left analog stick = pointer, face buttons = click.
//
// Generic: it scans /dev/input/event* for a device that reports both ABS_X and
// ABS_Y (and a gamepad button), so it does not hardcode the event node.
//
// Build: aarch64-linux-gnu-gcc -static -O2 -o dsh-joymouse joymouse.c
// Run as root (init service) from the ramdisk/system.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/input.h>
#include <linux/uinput.h>

static int msg(const char *s) {
    int f = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (f >= 0) { dprintf(f, "dsh-joymouse: %s\n", s); close(f); }
    fprintf(stderr, "dsh-joymouse: %s\n", s);
    return 0;
}

static int find_gamepad(char *path, size_t plen) {
    for (int i = 0; i < 32; i++) {
        char p[64];
        snprintf(p, sizeof(p), "/dev/input/event%d", i);
        int fd = open(p, O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        unsigned long absbits[(ABS_MAX / 8) + 1] = {0};
        unsigned long keybits[(KEY_MAX / 8) + 1] = {0};
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
            char m[200];
            snprintf(m, sizeof(m), "gamepad found at %s (name=%s)", p, name);
            msg(m);
            return fd;
        }
        close(fd);
    }
    return -1;
}

static int axis_step(int val, int center, int half, int speed) {
    if (half <= 0) return 0;
    double n = (double)(val - center) / (double)half;   // -1..1
    if (n > 1) n = 1; if (n < -1) n = -1;
    if (n > -0.18 && n < 0.18) return 0;                // deadzone
    n = (n > 0) ? (n - 0.18) / 0.82 : (n + 0.18) / 0.82;
    int s = (int)(n * speed);                            // signed px per tick
    if (s == 0) s = (n > 0) ? 1 : -1;
    return s;
}

int main(void) {
    char gp[64];
    int gi = find_gamepad(gp, sizeof(gp));
    if (gi < 0) { msg("no gamepad with ABS_X/ABS_Y found"); return 1; }

    struct input_absinfo ax = {0}, ay = {0};
    ioctl(gi, EVIOCGABS(ABS_X), &ax);
    ioctl(gi, EVIOCGABS(ABS_Y), &ay);
    char m[160];
    snprintf(m, sizeof(m), "ABS_X %d..%d ABS_Y %d..%d", ax.minimum, ax.maximum, ay.minimum, ay.maximum);
    msg(m);
    int cx = (ax.minimum + ax.maximum) / 2, hx = (ax.maximum - ax.minimum) / 2;
    int cy = (ay.minimum + ay.maximum) / 2, hy = (ay.maximum - ay.minimum) / 2;
    if (hx <= 0) hx = 1; if (hy <= 0) hy = 1;

    int ui = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (ui < 0) { snprintf(m, sizeof(m), "open /dev/uinput failed: %d (%s)", errno, strerror(errno)); msg(m); return 1; }
    ioctl(ui, UI_SET_EVBIT, EV_KEY);
    ioctl(ui, UI_SET_EVBIT, EV_REL);
    ioctl(ui, UI_SET_EVBIT, EV_SYN);
    ioctl(ui, UI_SET_KEYBIT, BTN_LEFT);
    ioctl(ui, UI_SET_KEYBIT, BTN_RIGHT);
    ioctl(ui, UI_SET_KEYBIT, BTN_MIDDLE);
    ioctl(ui, UI_SET_RELBIT, REL_X);
    ioctl(ui, UI_SET_RELBIT, REL_Y);

    struct uinput_setup us = {0};
    us.id.bustype = BUS_USB;
    us.id.vendor = 0x484b;
    us.id.product = 0x14df;
    us.id.version = 1;
    snprintf(us.name, sizeof(us.name), "Dsh Joy Mouse");
    if (ioctl(ui, UI_DEV_SETUP, &us) < 0 || ioctl(ui, UI_DEV_CREATE) < 0) {
        snprintf(m, sizeof(m), "uinput create failed: %d (%s)", errno, strerror(errno)); msg(m); return 1;
    }
    msg("virtual mouse created; mapping stick->pointer, A/B->left click, X->right, Y->middle");

    int cur_x = cx, cur_y = cy;
    for (;;) {
        struct input_event ev;
        while (read(gi, &ev, sizeof(ev)) == (ssize_t)sizeof(ev)) {
            if (ev.type == EV_ABS) {
                if (ev.code == ABS_X) cur_x = ev.value;
                else if (ev.code == ABS_Y) cur_y = ev.value;
            } else if (ev.type == EV_KEY) {
                int btn = -1;
                if (ev.code == BTN_SOUTH || ev.code == BTN_EAST) btn = BTN_LEFT;
                else if (ev.code == BTN_NORTH) btn = BTN_RIGHT;
                else if (ev.code == BTN_WEST) btn = BTN_MIDDLE;
                if (btn >= 0) {
                    struct input_event e = {0};
                    e.type = EV_KEY; e.code = btn; e.value = ev.value ? 1 : 0;
                    write(ui, &e, sizeof(e));
                    e.type = EV_SYN; e.code = SYN_REPORT; e.value = 0;
                    write(ui, &e, sizeof(e));
                }
            }
        }
        int dx = axis_step(cur_x, cx, hx, 12);
        int dy = axis_step(cur_y, cy, hy, 12);
        if (dx || dy) {
            struct input_event e = {0};
            if (dx) { e.type = EV_REL; e.code = REL_X; e.value = dx; write(ui, &e, sizeof(e)); }
            if (dy) { e.type = EV_REL; e.code = REL_Y; e.value = dy; write(ui, &e, sizeof(e)); }
            e.type = EV_SYN; e.code = SYN_REPORT; e.value = 0; write(ui, &e, sizeof(e));
        }
        usleep(8000);
    }
    return 0;
}
