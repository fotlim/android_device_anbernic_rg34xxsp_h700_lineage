/*
 * dsh-glprobe -- which half of the graphics stack is broken on this SoC?
 *
 * Boot d9d8722a is the second boot in which anything asks SurfaceFlinger to
 * composite (SystemUI's keyguard starts now that sd92 granted it READ_CONTACTS),
 * and the screen stays black because two *independent* things die:
 *
 *   1. SurfaceFlinger, in SwiftShader's renderer thread, nine times:
 *        F libc : Fatal signal 11 (SIGSEGV), code 2 (SEGV_ACCERR),
 *                 fault addr 0xeba9adad2000 (page-aligned) / 0xe246ebc86a04
 *        #00 pc 0x34c  <anonymous:f265bb3af000>
 *        #01 pc 0x3b31d4 /vendor/lib64/egl/libGLESv2_swiftshader.so
 *                       (sw::Renderer::executeTask(int)+324)
 *   2. composer@2.1-service, right after SF dies:
 *        #00 HwcHalImpl<...>::vsyncHook(void*, unsigned long, long)+4
 *        #01 VSyncWorker::Routine()+324   (hwcomposer.drm_minigbm.so)
 *        fault addr 0x0, x0 = 0, "Cause: null pointer dereference"
 *      The vendor HWC delivered a vsync with callbackData == NULL. Its own
 *      source (external/drm_hwcomposer/drm/vsyncworker.cpp:188) guards
 *      `if (callback)`, so this is the *registered* hook being invoked after the
 *      composer dropped it -- a consequence of SF dying first, not the cause.
 *
 * sd93's first run settled half of (1): steps 1-3 below all pass on the
 * hardware, exit=0. EGL initialises, a pbuffer clears and reads back red, the
 * shaders compile, and a triangle goes through the very executeTask path SF
 * dies in. So SwiftShader is not simply broken; what SF does that those steps
 * do not is:
 *
 *   * use EGL_KHR_fence_sync (SF waits on fences between layers);
 *   * run several contexts and renderer tasks at the same time (SF's
 *     RenderEngine feeds SwiftShader's marl fibers from several threads);
 *   * churn the JIT -- SF builds a program per shader family, draws, deletes;
 *   * hand SwiftShader *gralloc* buffers as FBO attachments and as textures.
 *
 * The first three are cheap to imitate here; the fourth needs libandroid's
 * AHardwareBuffer or libgui's GraphicBuffer, and neither has a vendor variant
 * in this tree (soong: "dependency libandroid of dsh-glprobe missing variant"),
 * which is recorded in docs/HANDOVER.md. Steps 4-6 below therefore test
 * threading, fences and JIT churn, and the note at the end says what is still
 * untested and how to test it.
 *
 * Every step prints before doing it, so the last line of live-gl-probe.txt
 * names the killer; the exit code says whether the process survived at all.
 *
 * Run by the live snapshot (patches/patch-live-snapshot.py):
 *   timeout 25 /vendor/bin/hw/dsh-glprobe > DEBUG/live-gl-probe.txt 2>&1
 */

#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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

/* A plain fill program and a texture-reading one, so step 6 can build two
 * different shader families the way SF's RenderEngine does. */
static const char kFill[] =
    "precision mediump float;\n"
    "uniform vec4 color;\n"
    "void main() { gl_FragColor = color; }\n";
static const char kShade[] =
    "precision mediump float;\n"
    "void main() { gl_FragColor = vec4(gl_FragCoord.x * 0.01, 0.2, 0.5, 1.0); }\n";

static GLuint compile(GLenum type, const char* src, const char* what) {
    GLuint s = glCreateShader(type);
    GLint ok = 0;
    if (!s) {
        FAIL("glCreateShader(%s) returned 0", what);
        return 0;
    }
    glShaderSource(s, 1, (const GLchar* const*)&src, NULL);
    glCompileShader(s);
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) FAIL("%s shader did not compile (0x%x)", what, glGetError());
    return ok ? s : 0;
}

static GLuint make_program(const char* frag, const char* vs_src) {
    GLuint vs = compile(GL_VERTEX_SHADER, vs_src, "vertex");
    GLuint fs = compile(GL_FRAGMENT_SHADER, frag, "fragment");
    GLuint prog;
    GLint ok = 0;
    if (!vs || !fs) return 0;
    prog = glCreateProgram();
    glAttachShader(prog, vs);
    glAttachShader(prog, fs);
    glBindAttribLocation(prog, 0, "pos");
    glLinkProgram(prog);
    glGetProgramiv(prog, GL_LINK_STATUS, &ok);
    if (!ok) say("    program linked=%d (err 0x%x)", ok, glGetError());
    return ok ? prog : 0;
}

static void draw_tri(GLuint prog, int size) {
    static const GLfloat tri[] = { -0.8f, -0.8f, 0.8f, -0.8f, 0.0f, 0.8f };
    glUseProgram(prog);
    glViewport(0, 0, size, size);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, tri);
    glEnableVertexAttribArray(0);
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    glFinish();
}

/* ---------------------------------------------------------------- step 5 */

struct thread_arg {
    EGLDisplay dpy;
    EGLConfig cfg;
    int draws;
    int ok;
};

static void* draw_thread(void* p) {
    struct thread_arg* a = (struct thread_arg*)p;
    const EGLint pb[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
    const EGLint ca[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLSurface s;
    EGLContext c;
    GLuint prog;
    int i;

    eglBindAPI(EGL_OPENGL_ES_API);
    s = eglCreatePbufferSurface(a->dpy, a->cfg, pb);
    c = eglCreateContext(a->dpy, a->cfg, EGL_NO_CONTEXT, ca);
    if (s == EGL_NO_SURFACE || c == EGL_NO_CONTEXT ||
        !eglMakeCurrent(a->dpy, s, s, c)) {
        say("    thread: surface=%p ctx=%p makeCurrent failed (err %s)",
            (void*)s, (void*)c, egl_err());
        return NULL;
    }
    prog = make_program(kFill, kVert);
    if (!prog) return NULL;
    glUseProgram(prog);  /* uniforms apply to the bound program, so bind first */
    glUniform4f(glGetUniformLocation(prog, "color"), 0.0f, 1.0f, 0.0f, 1.0f);
    for (i = 0; i < a->draws; i++) draw_tri(prog, 64);
    a->ok = 1;
    say("    thread: %d draws survived", a->draws);
    eglMakeCurrent(a->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(a->dpy, c);
    eglDestroySurface(a->dpy, s);
    return NULL;
}

/* ---------------------------------------------------------------- main */

int main(void) {
    EGLDisplay dpy;
    EGLConfig cfg = NULL;
    EGLSurface surf = EGL_NO_SURFACE;
    EGLContext ctx = EGL_NO_CONTEXT;
    GLuint prog;
    unsigned char px[4];
    EGLint vmaj = 0, vmin = 0, n = 0;

    setvbuf(stdout, NULL, _IOLBF, 0);

    STEP(1, "eglInitialize and eglQueryString (no buffers, no gralloc)");
    dpy = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    say("    eglGetDisplay = %p (err %s)", (void*)dpy, egl_err());
    if (dpy == EGL_NO_DISPLAY) return 1;
    if (!eglInitialize(dpy, &vmaj, &vmin)) {
        FAIL("eglInitialize (err %s)", egl_err());
        return 1;
    }
    OK("EGL %d.%d", vmaj, vmin);
    say("    vendor  = %s", eglQueryString(dpy, EGL_VENDOR));
    say("    version = %s", eglQueryString(dpy, EGL_VERSION));
    /* Client extensions are only reported for EGL_NO_DISPLAY; the native-buffer
     * ones live there, which is why they looked absent in the sd93 run. */
    say("    client extensions = %s",
        eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS));

    STEP(2, "pbuffer + context + glClear + glReadPixels");
    {
        const EGLint cfg_attr[] = {
            EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
            EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
            EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
            EGL_NONE,
        };
        if (!eglChooseConfig(dpy, cfg_attr, &cfg, 1, &n) || n < 1) {
            FAIL("eglChooseConfig (n=%d err %s)", n, egl_err());
            return 1;
        }
    }
    OK("config %p", (void*)cfg);
    {
        const EGLint pb_attr[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
        const EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
        surf = eglCreatePbufferSurface(dpy, cfg, pb_attr);
        if (surf == EGL_NO_SURFACE) {
            FAIL("eglCreatePbufferSurface (err %s)", egl_err());
            return 1;
        }
        OK("pbuffer %p", (void*)surf);
        eglBindAPI(EGL_OPENGL_ES_API);
        ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, ctx_attr);
        if (ctx == EGL_NO_CONTEXT) {
            FAIL("eglCreateContext (err %s)", egl_err());
            return 1;
        }
        OK("context %p", (void*)ctx);
        if (!eglMakeCurrent(dpy, surf, surf, ctx)) {
            FAIL("eglMakeCurrent (err %s)", egl_err());
            return 1;
        }
    }
    OK("current");
    say("    GL_VENDOR   = %s", gl_str(GL_VENDOR));
    say("    GL_VERSION  = %s", gl_str(GL_VERSION));
    say("    GL_RENDERER = %s", gl_str(GL_RENDERER));
    glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    say("    after glClear+glFinish (process alive)");
    glReadPixels(16, 16, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
    say("    pixel = %02x %02x %02x %02x (want red ff 00 00 ff)",
        px[0], px[1], px[2], px[3]);

    STEP(3, "shader program + triangle (the executeTask path SF dies in)");
    prog = make_program(kFill, kVert);
    if (!prog) {
        FAIL("triangle program");
        return 1;
    }
    glUseProgram(prog);  /* uniforms apply to the bound program, so bind first */
    glUniform4f(glGetUniformLocation(prog, "color"), 0.0f, 1.0f, 0.0f, 1.0f);
    draw_tri(prog, 64);
    say("    glDrawArrays + glFinish survived");
    memset(px, 0, 4);
    glReadPixels(32, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
    say("    centre pixel = %02x %02x %02x %02x (want green 00 ff 00 ff)",
        px[0], px[1], px[2], px[3]);
    OK("triangle drawn and read back");

    /* ------------------------------------------------ step 4: fence sync */
    STEP(4, "EGL_KHR_fence_sync, the mechanism SF uses between layers");
    {
        PFNEGLCREATESYNCKHRPROC create_sync =
            (PFNEGLCREATESYNCKHRPROC)eglGetProcAddress("eglCreateSyncKHR");
        PFNEGLCLIENTWAITSYNCKHRPROC wait_sync =
            (PFNEGLCLIENTWAITSYNCKHRPROC)eglGetProcAddress("eglClientWaitSyncKHR");
        PFNEGLDESTROYSYNCKHRPROC destroy_sync =
            (PFNEGLDESTROYSYNCKHRPROC)eglGetProcAddress("eglDestroySyncKHR");
        if (!create_sync || !wait_sync || !destroy_sync) {
            FAIL("fence entry points missing (%p %p %p)", (void*)create_sync,
                 (void*)wait_sync, (void*)destroy_sync);
        } else {
            int i;
            for (i = 0; i < 20; i++) {
                EGLSyncKHR sync = create_sync(dpy, EGL_SYNC_FENCE_KHR, NULL);
                if (sync == EGL_NO_SYNC_KHR) {
                    FAIL("eglCreateSyncKHR #%d (err %s)", i, egl_err());
                    return 1;
                }
                glFlush();
                if (wait_sync(dpy, sync, EGL_SYNC_FLUSH_COMMANDS_BIT_KHR,
                              1000000) == EGL_TIMEOUT_EXPIRED_KHR) {
                    FAIL("fence #%d timed out", i);
                    destroy_sync(dpy, sync);
                    return 1;
                }
                destroy_sync(dpy, sync);
            }
            OK("20 fences created, flushed and waited");
        }
    }

    /* ------------------------------------------------ step 5: two threads */
    STEP(5, "two threads, two contexts, 200 triangles each (SF's renderer is MT)");
    {
        pthread_t t1, t2;
        struct thread_arg a1, a2;
        memset(&a1, 0, sizeof(a1));
        memset(&a2, 0, sizeof(a2));
        a1.dpy = a2.dpy = dpy;
        a1.cfg = a2.cfg = cfg;
        a1.draws = a2.draws = 200;
        /* Release the main context first: this is the closest cheap imitation
         * of RenderEngine handing work to SwiftShader's fibers from elsewhere. */
        eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (pthread_create(&t1, NULL, draw_thread, &a1) != 0) {
            FAIL("pthread_create 1");
            return 1;
        }
        if (pthread_create(&t2, NULL, draw_thread, &a2) != 0) {
            FAIL("pthread_create 2");
            return 1;
        }
        pthread_join(t1, NULL);
        pthread_join(t2, NULL);
        say("    thread results: %d / %d (both must be 1)", a1.ok, a2.ok);
        if (!a1.ok || !a2.ok) {
            FAIL("a thread did not finish");
            return 1;
        }
        OK("step 5 survived");
    }

    /* ------------------------------------------------ step 6: JIT churn */
    STEP(6, "8 fresh contexts, alternating two shader families (JIT churn)");
    {
        const char* frags[2];
        int c;
        frags[0] = kFill;
        frags[1] = kShade;
        for (c = 0; c < 8; c++) {
            const EGLint pb[] = { EGL_WIDTH, 32, EGL_HEIGHT, 32, EGL_NONE };
            const EGLint ca[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
            EGLSurface s = eglCreatePbufferSurface(dpy, cfg, pb);
            EGLContext k = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, ca);
            GLuint p;
            int d;
            if (s == EGL_NO_SURFACE || k == EGL_NO_CONTEXT ||
                !eglMakeCurrent(dpy, s, s, k)) {
                FAIL("context %d (surface %p ctx %p err %s)", c, (void*)s,
                     (void*)k, egl_err());
                return 1;
            }
            p = make_program(frags[c % 2], kVert);
            if (!p) {
                FAIL("program %d", c);
                return 1;
            }
            glUseProgram(p);
            glUniform4f(glGetUniformLocation(p, "color"), 1.0f, 0.0f, 0.0f, 1.0f);
            for (d = 0; d < 10; d++) draw_tri(p, 32);
            say("    context %d: 10 draws ok", c);
            glDeleteProgram(p);
            eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            eglDestroyContext(dpy, k);
            eglDestroySurface(dpy, s);
        }
        OK("step 6 survived");
    }

    say("\n=== probe done, nothing crashed ===");
    say("still untested: gralloc buffers as FBO attachments / textures. That needs");
    say("AHardwareBuffer or GraphicBuffer, and neither has a vendor variant in this");
    say("tree (soong: dependency libandroid of dsh-glprobe missing variant). If");
    say("steps 4-6 all pass, the minigbm import path is the remaining suspect.");
    eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (ctx != EGL_NO_CONTEXT) eglDestroyContext(dpy, ctx);
    if (surf != EGL_NO_SURFACE) eglDestroySurface(dpy, surf);
    eglTerminate(dpy);
    return 0;
}
