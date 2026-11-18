/*
 * dsh-glprobe2 -- the one graphics question sd94 could not answer: can
 * SwiftShader import a *minigbm* buffer at all?
 *
 * What is already proven (boot a354b994, live-gl-probe.txt):
 *
 *   step 1  EGL initialises, vendor=Android, EGL 1.4                     ok
 *   step 2  pbuffer + glClear + glReadPixels returns the right pixel     ok
 *   step 3  shaders compile and a triangle goes through
 *           sw::Renderer::executeTask(int) -- the exact frame that
 *           SurfaceFlinger dies in --                                    ok
 *   step 4  20 EGL_KHR_fence_sync create/flush/wait cycles               ok
 *   step 5  two threads with two contexts, 200 triangles each, at once   ok
 *   step 6  (failed on a probe bug: the second fragment shader used a
 *           varying the vertex shader never wrote, so linking it was
 *           always going to fail -- not a hardware finding)
 *
 * So SwiftShader's own JIT, its fibers, its fences and its multi-threading
 * all work on this SoC. What SurfaceFlinger still does that none of those
 * steps do is hand SwiftShader a buffer that came out of gralloc/minigbm: every
 * layer SystemUI draws is a BufferQueue buffer, and SF's framebuffer target is
 * one too. It dies right after SystemUI first draws, which is when the first
 * such import happens.
 *
 * That test cannot be written as a *vendor* binary: AHardwareBuffer lives in
 * libandroid, and soong refuses `libandroid` for a vendor variant in this tree
 * ("dependency libandroid of dsh-glprobe missing variant: image:vendor.30").
 * This binary is therefore a *system* one, published to /system/bin by
 * patches/patch-system-app-overlay.py, and run by the live snapshot. It uses
 * exactly the API libEGL itself uses to import: AHardwareBuffer_allocate
 * (gralloc4 mapper via the allocator service), then eglGetNativeClientBufferANDROID,
 * then eglCreateImageKHR(EGL_NATIVE_BUFFER_ANDROID).
 *
 * Steps, in the order that isolates blame:
 *
 *   1. allocate a render target (GPU_FRAMEBUFFER | GPU_SAMPLED | CPU_READ)
 *   2. import it as an EGLImage and render into it through an FBO
 *      -- this is SF's framebuffer / saveLayer path
 *   3. allocate a texture source, CPU-fill it, import it and sample from it
 *      -- this is SF's "draw a layer" path, the one that runs when SystemUI
 *         first paints, and the first one that ever crashed SF
 *   4. lock/unlock with a release fence, the way BufferQueue producers do
 *
 * Every step prints before acting, so the last line of live-ahb-probe.txt
 * names the step that killed it.
 *
 * Run by the live snapshot (patches/patch-live-snapshot.py):
 *   timeout 25 /system/bin/dsh-glprobe2 > DEBUG/live-ahb-probe.txt 2>&1
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <android/hardware_buffer.h>

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

static const char kVert[] =
    "attribute vec2 pos;\n"
    "void main() { gl_Position = vec4(pos, 0.0, 1.0); }\n";
static const char kFill[] =
    "precision mediump float;\n"
    "void main() { gl_FragColor = vec4(0.0, 1.0, 0.0, 1.0); }\n";
static const char kTex[] =
    "precision mediump float;\n"
    "uniform sampler2D tex;\n"
    "void main() { gl_FragColor = texture2D(tex, vec2(0.5, 0.5)); }\n";

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
    if (!ok) {
        char log[512];
        GLsizei n = 0;
        glGetShaderInfoLog(s, sizeof(log), &n, log);
        FAIL("%s shader did not compile: %s", what, log);
        return 0;
    }
    return s;
}

static GLuint make_program(const char* frag) {
    GLuint vs = compile(GL_VERTEX_SHADER, kVert, "vertex");
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
    if (!ok) {
        char log[512];
        GLsizei n = 0;
        glGetProgramInfoLog(prog, sizeof(log), &n, log);
        FAIL("program link: %s", log);
        return 0;
    }
    return prog;
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

static AHardwareBuffer* make_ahb(int w, int h, uint64_t usage, const char* what) {
    AHardwareBuffer_Desc d;
    AHardwareBuffer* b = NULL;
    AHardwareBuffer_Desc got;
    memset(&d, 0, sizeof(d));
    d.width = (uint32_t)w;
    d.height = (uint32_t)h;
    d.layers = 1;
    d.format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM;
    d.usage = usage;
    if (AHardwareBuffer_allocate(&d, &b) != 0 || b == NULL) {
        FAIL("AHardwareBuffer_allocate(%s) failed", what);
        return NULL;
    }
    AHardwareBuffer_describe(b, &got);
    OK("%s: %ux%u fmt=0x%x usage=0x%llx stride=%u", what, got.width, got.height,
       got.format, (unsigned long long)got.usage, got.stride);
    return b;
}

static EGLImageKHR image_from_ahb(EGLDisplay dpy, AHardwareBuffer* b) {
    PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC get_cb;
    EGLClientBuffer cb;
    EGLImageKHR img;

    get_cb = (PFNEGLGETNATIVECLIENTBUFFERANDROIDPROC)eglGetProcAddress(
        "eglGetNativeClientBufferANDROID");
    if (get_cb == NULL) {
        FAIL("this libEGL has no eglGetNativeClientBufferANDROID");
        return EGL_NO_IMAGE_KHR;
    }
    cb = get_cb(b);
    say("    eglGetNativeClientBufferANDROID = %p (err %s)", (void*)cb, egl_err());
    if (cb == NULL) return EGL_NO_IMAGE_KHR;
    img = eglCreateImageKHR(dpy, EGL_NO_CONTEXT, EGL_NATIVE_BUFFER_ANDROID, cb, NULL);
    say("    eglCreateImageKHR(EGL_NATIVE_BUFFER_ANDROID) = %p (err %s)", (void*)img,
        egl_err());
    return img;
}

int main(void) {
    EGLDisplay dpy;
    EGLConfig cfg = NULL;
    EGLSurface surf = EGL_NO_SURFACE;
    EGLContext ctx = EGL_NO_CONTEXT;
    EGLint vmaj = 0, vmin = 0, n = 0;
    GLuint fill_prog = 0;
    unsigned char px[4];
    int rc = 0;

    setvbuf(stdout, NULL, _IOLBF, 0);

    STEP(1, "EGL context (the same setup the vendor probe proved works)");
    dpy = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (dpy == EGL_NO_DISPLAY || !eglInitialize(dpy, &vmaj, &vmin)) {
        FAIL("eglInitialize (err %s)", egl_err());
        return 1;
    }
    OK("EGL %d.%d, client extensions: %s", vmaj, vmin,
       eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS));
    {
        const EGLint cfg_attr[] = {
            EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
            EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
            EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
            EGL_NONE,
        };
        const EGLint pb_attr[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
        const EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
        if (!eglChooseConfig(dpy, cfg_attr, &cfg, 1, &n) || n < 1) {
            FAIL("eglChooseConfig (n=%d err %s)", n, egl_err());
            return 1;
        }
        eglBindAPI(EGL_OPENGL_ES_API);
        surf = eglCreatePbufferSurface(dpy, cfg, pb_attr);
        ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, ctx_attr);
        if (surf == EGL_NO_SURFACE || ctx == EGL_NO_CONTEXT ||
            !eglMakeCurrent(dpy, surf, surf, ctx)) {
            FAIL("surface=%p ctx=%p current (err %s)", (void*)surf, (void*)ctx, egl_err());
            return 1;
        }
    }
    OK("current, GL_RENDERER = %s",
       (const char*)glGetString(GL_RENDERER));
    fill_prog = make_program(kFill);
    if (!fill_prog) return 1;

    /* ------------------------------------- step 2: render INTO a minigbm buffer */
    STEP(2, "render into a minigbm buffer imported as an EGLImage (SF's framebuffer)");
    {
        AHardwareBuffer* rt =
            make_ahb(720, 480,
                     AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER |
                     AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
                     AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN,
                     "render target 720x480");
        EGLImageKHR img;
        GLuint tex = 0, fbo = 0;
        GLenum st;

        if (rt == NULL) return 1;
        img = image_from_ahb(dpy, rt);
        if (img == EGL_NO_IMAGE_KHR) {
            AHardwareBuffer_release(rt);
            return 1;
        }
        glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_2D, tex);
        glEGLImageTargetTexture2DOES(GL_TEXTURE_2D, (GLeglImageOES)img);
        say("    bound the image as a texture (err 0x%x)", glGetError());
        glGenFramebuffers(1, &fbo);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
        st = glCheckFramebufferStatus(GL_FRAMEBUFFER);
        say("    framebuffer status 0x%x (0x8cd5 = COMPLETE)", st);
        draw_tri(fill_prog, 512);
        say("    drew a triangle into it and finished");
        memset(px, 0, 4);
        glReadPixels(256, 240, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
        say("    readback = %02x %02x %02x %02x (want green 00 ff 00 ff)",
            px[0], px[1], px[2], px[3]);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glDeleteFramebuffers(1, &fbo);
        glDeleteTextures(1, &tex);
        eglDestroyImageKHR(dpy, img);
        AHardwareBuffer_release(rt);
        OK("step 2 survived");
    }

    /* ------------------------------------- step 3: sample FROM a minigbm buffer */
    STEP(3, "CPU-fill a minigbm buffer, import it, sample it (SF's per-layer path)");
    {
        AHardwareBuffer* tb =
            make_ahb(64, 64,
                     AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
                     AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN |
                     AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN,
                     "layer texture 64x64");
        AHardwareBuffer_Desc d;
        EGLImageKHR img;
        GLuint tex = 0;
        GLuint tex_prog;
        void* base = NULL;
        int fd = -1;
        int lrc;
        uint32_t row;
        GLint unc;

        if (tb == NULL) return 1;
        AHardwareBuffer_describe(tb, &d);
        lrc = AHardwareBuffer_lock(tb, AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN, -1, NULL,
                                  &base);
        say("    AHardwareBuffer_lock rc=%d base=%p stride=%u", lrc, base, d.stride);
        if (lrc != 0 || base == NULL) {
            FAIL("cannot map the buffer from the CPU");
            AHardwareBuffer_release(tb);
            return 1;
        }
        /* Left half red, right half blue, honouring the returned stride. */
        for (row = 0; row < d.height; row++) {
            uint32_t* line = (uint32_t*)((char*)base + (size_t)row * d.stride * 4);
            uint32_t x;
            for (x = 0; x < d.width; x++)
                line[x] = (x < d.width / 2) ? 0xff0000ffu : 0xffff0000u;
        }
        AHardwareBuffer_unlock(tb, &fd);
        say("    CPU fill done, unlock fence fd=%d", fd);
        if (fd >= 0) close(fd);

        img = image_from_ahb(dpy, tb);
        if (img == EGL_NO_IMAGE_KHR) {
            AHardwareBuffer_release(tb);
            return 1;
        }
        glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glEGLImageTargetTexture2DOES(GL_TEXTURE_2D, (GLeglImageOES)img);
        say("    imported as a texture (err 0x%x)", glGetError());
        tex_prog = make_program(kTex);
        if (!tex_prog) {
            glDeleteTextures(1, &tex);
            eglDestroyImageKHR(dpy, img);
            AHardwareBuffer_release(tb);
            return 1;
        }
        glUseProgram(tex_prog);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, tex);
        unc = glGetUniformLocation(tex_prog, "tex");
        glUniform1i(unc, 0);
        draw_tri(tex_prog, 64);
        say("    sampled the buffer and finished");
        memset(px, 0, 4);
        glReadPixels(16, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
        say("    left half  = %02x %02x %02x %02x (want red ff 00 00 ff)", px[0],
            px[1], px[2], px[3]);
        glReadPixels(48, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
        say("    right half = %02x %02x %02x %02x (want blue ff ff 00 00)", px[0],
            px[1], px[2], px[3]);
        glDeleteTextures(1, &tex);
        eglDestroyImageKHR(dpy, img);
        AHardwareBuffer_release(tb);
        OK("step 3 survived");
    }

    /* ------------------------------------- step 4: repeat, as SF does per frame */
    STEP(4, "10 more imports and draws in a row (SF re-imports every frame)");
    {
        int i;
        for (i = 0; i < 10; i++) {
            AHardwareBuffer* b =
                make_ahb(720, 480,
                         AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER |
                         AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE,
                         "per-frame target");
            EGLImageKHR img;
            GLuint tex = 0, fbo = 0;
            if (b == NULL) {
                rc = 1;
                break;
            }
            img = image_from_ahb(dpy, b);
            if (img == EGL_NO_IMAGE_KHR) {
                AHardwareBuffer_release(b);
                rc = 1;
                break;
            }
            glGenTextures(1, &tex);
            glBindTexture(GL_TEXTURE_2D, tex);
            glEGLImageTargetTexture2DOES(GL_TEXTURE_2D, (GLeglImageOES)img);
            glGenFramebuffers(1, &fbo);
            glBindFramebuffer(GL_FRAMEBUFFER, fbo);
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, tex, 0);
            draw_tri(fill_prog, 512);
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            glDeleteFramebuffers(1, &fbo);
            glDeleteTextures(1, &tex);
            eglDestroyImageKHR(dpy, img);
            AHardwareBuffer_release(b);
            say("    frame %d ok", i);
        }
        if (rc == 0) OK("step 4 survived");
        else FAIL("step 4 did not finish");
    }

    say("\n=== probe done (rc=%d) ===", rc);
    eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (ctx != EGL_NO_CONTEXT) eglDestroyContext(dpy, ctx);
    if (surf != EGL_NO_SURFACE) eglDestroySurface(dpy, surf);
    eglTerminate(dpy);
    return rc;
}
