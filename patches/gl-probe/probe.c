/*
 * dsh-glprobe -- is SwiftShader itself broken on this SoC, or is it our
 * gralloc/EGLImage path?
 *
 * Boot 35526bea is the first one where anything ever asked SurfaceFlinger to
 * composite a layer (SystemUI's keyguard finally started, because sd92 granted
 * it READ_CONTACTS). The screen then went black because SurfaceFlinger dies
 * deterministically in the same place, eight times in a row:
 *
 *   F libc : Fatal signal 11 (SIGSEGV), code 2 (SEGV_ACCERR), fault addr ...
 *            in tid 1358 (surfaceflinger), pid 932 (surfaceflinger)
 *   F DEBUG: #00 pc 000000000000034c  <anonymous:f265bb3af000>
 *   F DEBUG: #01 pc 00000000003b31d4  /vendor/lib64/egl/libGLESv2_swiftshader.so
 *            (sw::Renderer::executeTask(int)+324)
 *
 * and the renderer is SwiftShader, not a GPU driver:
 *
 *   I RenderEngine: renderer  : Google SwiftShader
 *   I RenderEngine: version   : OpenGL ES 3.0 SwiftShader 4.1.0.7
 *
 * SEGV_ACCERR at a data address while running JIT code has two very different
 * explanations, and they need opposite fixes:
 *
 *   (a) SwiftShader's own renderer/JIT is broken here -- its fibers need a
 *       stack, its JIT needs executable memory, and ARMv8 cache maintenance
 *       must actually work. Then the fix is a different GL backend (Mesa's
 *       panfrost on the real Mali-G31) or a different SwiftShader build.
 *   (b) SwiftShader is fine and the memory handed to it through
 *       EGL_ANDROID_image_native_buffer / minigbm is not writable or not
 *       mapped the way it assumes. Then the fix is in gralloc.minigbm.
 *
 * So this probe walks the stack in the cheapest possible order and prints
 * every step before doing it, so the last line of live-gl-probe.txt is the
 * step that killed it:
 *
 *   1. eglInitialize / eglQueryString           (no buffers involved at all)
 *   2. pbuffer + context + glClear + readback   (SwiftShader draws into its
 *                                                own memory, no gralloc)
 *   3. a real shader program + a triangle       (forces JIT compilation and
 *                                                the executeTask path that
 *                                                crashes SF)
 *   4. the same shader into an FBO whose colour attachment is a GRALLOC
 *      buffer imported as an EGLImage            (only if 1-3 pass)
 *
 * Step 4 is deliberately last: it is the only one that can blame gralloc, so
 * its outcome only matters once 1-3 are known good.
 *
 * Run by the live snapshot (patches/patch-live-snapshot.py) as
 *   timeout 20 /vendor/bin/hw/dsh-glprobe > DEBUG/live-gl-probe.txt 2>&1
 */

#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>

static void say(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
static void say(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
    fputc('\n', stdout);
    fflush(stdout);
}

#define STEP(n, what) say("\n=== step %d: %s ===", n, what)
#define OK(...) say("    ok: " __VA_ARGS__)
#define FAIL(...) say("    FAILED: " __VA_ARGS__)

static const char* egl_err(void) {
    switch (eglGetError()) {
        case EGL_SUCCESS: return "EGL_SUCCESS";
        case EGL_NOT_INITIALIZED: return "EGL_NOT_INITIALIZED";
        case EGL_BAD_ACCESS: return "EGL_BAD_ACCESS";
        case EGL_BAD_ALLOC: return "EGL_BAD_ALLOC";
        case EGL_BAD_ATTRIBUTE: return "EGL_BAD_ATTRIBUTE";
        case EGL_BAD_CONFIG: return "EGL_BAD_CONFIG";
        case EGL_BAD_CONTEXT: return "EGL_BAD_CONTEXT";
        case EGL_BAD_DISPLAY: return "EGL_BAD_DISPLAY";
        case EGL_BAD_MATCH: return "EGL_BAD_MATCH";
        case EGL_BAD_NATIVE_PIXMAP: return "EGL_BAD_NATIVE_PIXMAP";
        case EGL_BAD_NATIVE_WINDOW: return "EGL_BAD_NATIVE_WINDOW";
        case EGL_BAD_PARAMETER: return "EGL_BAD_PARAMETER";
        case EGL_BAD_SURFACE: return "EGL_BAD_SURFACE";
        case EGL_CONTEXT_LOST: return "EGL_CONTEXT_LOST";
        default: return "EGL_<other>";
    }
}

static const char* gl_str(GLenum name) {
    const GLubyte* s = glGetString(name);
    return s ? (const char*)s : "(null)";
}

static const char kVert[] =
    "attribute vec2 pos;\n"
    "void main() { gl_Position = vec4(pos, 0.0, 1.0); }\n";
static const char kFrag[] =
    "precision mediump float;\n"
    "void main() { gl_FragColor = vec4(0.0, 1.0, 0.0, 1.0); }\n";

/* Compile the two shaders, draw one triangle, return the centre pixel. */
static int draw_triangle(int size, unsigned char out[4]) {
    GLuint vs = glCreateShader(GL_VERTEX_SHADER);
    GLuint fs = glCreateShader(GL_FRAGMENT_SHADER);
    const GLchar* src;
    if (!vs || !fs) {
        say("    FAILED: glCreateShader returned 0 (err 0x%x)", glGetError());
        return -1;
    }
    src = kVert;
    glShaderSource(vs, 1, &src, NULL);
    src = kFrag;
    glShaderSource(fs, 1, &src, NULL);
    glCompileShader(vs);
    glCompileShader(fs);

    GLint ok = 0;
    glGetShaderiv(vs, GL_COMPILE_STATUS, &ok);
    say("    vertex shader compiled=%d", ok);
    glGetShaderiv(fs, GL_COMPILE_STATUS, &ok);
    say("    fragment shader compiled=%d", ok);

    GLuint prog = glCreateProgram();
    glAttachShader(prog, vs);
    glAttachShader(prog, fs);
    glBindAttribLocation(prog, 0, "pos");
    glLinkProgram(prog);
    glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    say("    program linked=%d (err 0x%x)", ok, glGetError());
    if (!ok) return -1;
    glUseProgram(prog);

    static const GLfloat tri[] = { -0.8f, -0.8f, 0.8f, -0.8f, 0.0f, 0.8f };
    glViewport(0, 0, size, size);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, tri);
    glEnableVertexAttribArray(0);
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glFinish();
    say("    glDrawArrays + glFinish survived");

    memset(out, 0, 4);
    glReadPixels(size / 2, size / 2, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, out);
    say("    centre pixel = %02x %02x %02x %02x (want green 00 ff 00 ff)",
        out[0], out[1], out[2], out[3]);
    return 0;
}

int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);

    STEP(1, "eglInitialize and eglQueryString (no buffers, no gralloc)");
    EGLDisplay dpy = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    say("    eglGetDisplay = %p (err %s)", (void*)dpy, egl_err());
    if (dpy == EGL_NO_DISPLAY) return 1;
    EGLint vmaj = 0, vmin = 0;
    if (!eglInitialize(dpy, &vmaj, &vmin)) {
        FAIL("eglInitialize (err %s)", egl_err());
        return 1;
    }
    OK("EGL %d.%d", vmaj, vmin);
    say("    vendor  = %s", eglQueryString(dpy, EGL_VENDOR));
    say("    version = %s", eglQueryString(dpy, EGL_VERSION));
    say("    client APIs = %s", eglQueryString(dpy, EGL_CLIENT_APIS));
    say("    extensions = %s", eglQueryString(dpy, EGL_EXTENSIONS));

    STEP(2, "pbuffer + context + glClear + glReadPixels");
    const EGLint cfg_attr[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
        EGL_NONE,
    };
    EGLConfig cfg = NULL;
    EGLint n = 0;
    if (!eglChooseConfig(dpy, cfg_attr, &cfg, 1, &n) || n < 1) {
        FAIL("eglChooseConfig (n=%d err %s)", n, egl_err());
        return 1;
    }
    OK("config %p", (void*)cfg);
    const EGLint pb_attr[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
    EGLSurface surf = eglCreatePbufferSurface(dpy, cfg, pb_attr);
    if (surf == EGL_NO_SURFACE) {
        FAIL("eglCreatePbufferSurface (err %s)", egl_err());
        return 1;
    }
    OK("pbuffer %p", (void*)surf);
    eglBindAPI(EGL_OPENGL_ES_API);
    const EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, ctx_attr);
    if (ctx == EGL_NO_CONTEXT) {
        FAIL("eglCreateContext (err %s)", egl_err());
        return 1;
    }
    OK("context %p", (void*)ctx);
    if (!eglMakeCurrent(dpy, surf, surf, ctx)) {
        FAIL("eglMakeCurrent (err %s)", egl_err());
        return 1;
    }
    OK("current");
    say("    GL_VENDOR   = %s", gl_str(GL_VENDOR));
    say("    GL_VERSION  = %s", gl_str(GL_VERSION));
    say("    GL_RENDERER = %s", gl_str(GL_RENDERER));
    glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    say("    after glClear+glFinish (process alive)");
    unsigned char px[4] = { 0, 0, 0, 0 };
    glReadPixels(16, 16, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
    say("    pixel = %02x %02x %02x %02x (want red ff 00 00 ff)",
        px[0], px[1], px[2], px[3]);

    STEP(3, "shader program + triangle (this is the executeTask path SF dies in)");
    if (draw_triangle(64, px) != 0) {
        FAIL("triangle");
        return 1;
    }
    OK("triangle drawn and read back");

    STEP(4, "same triangle into an FBO backed by a minigbm graphic buffer");
    /*
     * Deliberately NOT implemented yet: it needs gralloc.minigbm's
     * get_handle() layout and an EGLImage import, and there is no point
     * writing that until steps 1-3 are known to survive on the hardware.
     * The snapshot prints this line so the next dump says which question is
     * still open.
     */
    say("    skipped: run this step only if steps 1-3 passed");
    say("\n=== probe done, nothing crashed ===");
    eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglTerminate(dpy);
    return 0;
}
