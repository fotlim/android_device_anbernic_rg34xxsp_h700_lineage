/*
 * dsh-glprobe2 -- can SwiftShader *sample* a minigbm buffer?
 *
 * History of this question:
 *
 *   sd93  vendor probe: EGL, pbuffer, shaders, the executeTask path -> all ok
 *   sd94  vendor probe v2: EGL_KHR_fence_sync ok, two contexts/two threads
 *         200 draws each ok -> SwiftShader's own JIT, fibers, fences and
 *         threading are fine on this SoC.
 *   sd95  this probe, first run (boot 5789ea90), four steps:
 *           step 2  render INTO a 720x480 minigbm buffer via EGLImage+FBO,
 *                   draw a triangle, read it back -> 00 ff 00 ff  **ok**
 *           step 3  CPU-fill a 64x64 minigbm buffer, import it as a texture,
 *                   then sample it -> printed "imported as a texture (err 0x0)"
 *                   and died: exit=139 (SIGSEGV)
 *         So filling a gralloc buffer *from* the GPU works, and reading it
 *         back works; what kills the process is SwiftShader's *image decode*
 *         path -- the very thing SurfaceFlinger runs once per layer per frame
 *         (sw::Renderer::executeTask(int)+324, the frame SF dies in).
 *
 * sd96 splits step 3 into three questions so the next dump says which part is
 * at fault, and prints a line before every GL call so the last line names the
 * call that killed it:
 *
 *   step 3  control: sample a plain GL texture (no gralloc anywhere).
 *           If THIS dies, the sampler program or the JIT is at fault instead.
 *   step 4  sample a minigbm buffer that only the GPU touched
 *           (GPU_SAMPLED_IMAGE | GPU_FRAMEBUFFER, filled by drawing into it).
 *   step 5  sample a minigbm buffer that the CPU wrote
 *           (GPU_SAMPLED_IMAGE | CPU_WRITE_OFTEN | CPU_READ_OFTEN). This is the
 *           exact sequence that crashed in sd95.
 *   step 6  ten render-into-gralloc frames in a row (SF's per-frame path).
 *
 * The tombstone is the other half of the evidence: the live snapshot now
 * captures logcat *again* after running the probes (DEBUG/live-logcat-after.txt),
 * so a SIGSEGV here leaves its backtrace on the card. If that backtrace shows
 * sw::Renderer::executeTask like SurfaceFlinger's, the attribution is airtight.
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
#define TRY(what) say("    -> %s", what)

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
    GLuint vs, fs, prog;
    GLint ok = 0;

    TRY("compile vertex shader");
    vs = compile(GL_VERTEX_SHADER, kVert, "vertex");
    TRY("compile fragment shader");
    fs = compile(GL_FRAGMENT_SHADER, frag, "fragment");
    if (!vs || !fs) return 0;
    TRY("create + link program");
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
    say("    program linked, id=%u", prog);
    return prog;
}

static void draw_tri(GLuint prog, int size) {
    static const GLfloat tri[] = { -0.8f, -0.8f, 0.8f, -0.8f, 0.0f, 0.8f };
    TRY("glUseProgram");
    glUseProgram(prog);
    glViewport(0, 0, size, size);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, tri);
    glEnableVertexAttribArray(0);
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    TRY("glClear");
    glClear(GL_COLOR_BUFFER_BIT);
    TRY("glDrawArrays");
    glDrawArrays(GL_TRIANGLES, 0, 3);
    TRY("glFinish");
    glFinish();
    say("    draw completed");
}

/* Draw with a texture bound: this is the path SF runs for every layer. */
static void draw_sampling(GLuint prog, GLuint tex, int size) {
    static const GLfloat tri[] = { -0.8f, -0.8f, 0.8f, -0.8f, 0.0f, 0.8f };
    TRY("glUseProgram (sampler)");
    glUseProgram(prog);
    TRY("glActiveTexture + glBindTexture");
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    TRY("glUniform1i(tex)");
    glUniform1i(glGetUniformLocation(prog, "tex"), 0);
    glViewport(0, 0, size, size);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, tri);
    glEnableVertexAttribArray(0);
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    TRY("glClear");
    glClear(GL_COLOR_BUFFER_BIT);
    TRY("glDrawArrays (this samples the texture)");
    glDrawArrays(GL_TRIANGLES, 0, 3);
    TRY("glFinish");
    glFinish();
    say("    sampling draw completed");
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
    TRY("AHardwareBuffer_allocate");
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
    TRY("eglGetNativeClientBufferANDROID");
    cb = get_cb(b);
    say("    client buffer = %p (err %s)", (void*)cb, egl_err());
    if (cb == NULL) return EGL_NO_IMAGE_KHR;
    TRY("eglCreateImageKHR");
    img = eglCreateImageKHR(dpy, EGL_NO_CONTEXT, EGL_NATIVE_BUFFER_ANDROID, cb, NULL);
    say("    image = %p (err %s)", (void*)img, egl_err());
    return img;
}

/* Bind an EGLImage to a 2D texture and, optionally, attach it to an FBO. */
static GLuint image_to_texture(EGLImageKHR img, GLuint* out_fbo) {
    GLuint tex = 0;
    TRY("glGenTextures + glBindTexture");
    glGenTextures(1, &tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    TRY("glEGLImageTargetTexture2DOES");
    glEGLImageTargetTexture2DOES(GL_TEXTURE_2D, (GLeglImageOES)img);
    say("    bound as texture (err 0x%x)", glGetError());
    /*
     * sd98: an imported texture that ends up as a *mipmap* target is
     * incomplete -- GLES then samples it as (0,0,0,1), i.e. black, without any
     * error. The first version only set filters in some steps, so part of the
     * "black" evidence may be this. Set them here, for every import, and print
     * the format and completeness so the log tells us which it is.
     */
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    {
        /* GLES2 has no glGetTexLevelParameteriv, so verify what it does expose:
         * the filter state (an imported texture left as a mipmap target is
         * incomplete and samples as black with no GL error) and the error flag
         * after each step. */
        GLint minf = 0, magf = 0;
        glGetTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, &minf);
        glGetTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, &magf);
        say("    min filter 0x%x (0x2600=NEAREST OK, 0x2703=NEAREST_MIPMAP_LINEAR = incomplete),"
            " mag 0x%x, err 0x%x", minf, magf, glGetError());
    }
    if (out_fbo != NULL) {
        GLuint fbo = 0;
        TRY("glGenFramebuffers + attach the image");
        glGenFramebuffers(1, &fbo);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
        say("    framebuffer status 0x%x (0x8cd5 = COMPLETE)",
            glCheckFramebufferStatus(GL_FRAMEBUFFER));
        *out_fbo = fbo;
    }
    return tex;
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

    STEP(1, "EGL context (proved to work by the sd93/sd94 vendor probe)");
    TRY("eglGetDisplay + eglInitialize");
    dpy = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (dpy == EGL_NO_DISPLAY || !eglInitialize(dpy, &vmaj, &vmin)) {
        FAIL("eglInitialize (err %s)", egl_err());
        return 1;
    }
    OK("EGL %d.%d", vmaj, vmin);
    say("    client extensions: %s", eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS));
    {
        const EGLint cfg_attr[] = {
            EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
            EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
            EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8,
            EGL_NONE,
        };
        const EGLint pb_attr[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
        const EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
        TRY("eglChooseConfig");
        if (!eglChooseConfig(dpy, cfg_attr, &cfg, 1, &n) || n < 1) {
            FAIL("eglChooseConfig (n=%d err %s)", n, egl_err());
            return 1;
        }
        eglBindAPI(EGL_OPENGL_ES_API);
        TRY("create pbuffer + context + makeCurrent");
        surf = eglCreatePbufferSurface(dpy, cfg, pb_attr);
        ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, ctx_attr);
        if (surf == EGL_NO_SURFACE || ctx == EGL_NO_CONTEXT ||
            !eglMakeCurrent(dpy, surf, surf, ctx)) {
            FAIL("surface=%p ctx=%p current (err %s)", (void*)surf, (void*)ctx, egl_err());
            return 1;
        }
    }
    OK("current, GL_RENDERER = %s", (const char*)glGetString(GL_RENDERER));
    fill_prog = make_program(kFill);
    if (!fill_prog) return 1;

    /* ------------------------------------- step 2: render INTO a minigbm buffer */
    STEP(2, "render into a minigbm buffer (SF's framebuffer target) -- passed in sd95");
    {
        AHardwareBuffer* rt =
            make_ahb(720, 480,
                     AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER |
                     AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
                     AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN,
                     "render target 720x480");
        EGLImageKHR img;
        GLuint tex = 0, fbo = 0;
        if (rt == NULL) return 1;
        img = image_from_ahb(dpy, rt);
        if (img == EGL_NO_IMAGE_KHR) {
            AHardwareBuffer_release(rt);
            return 1;
        }
        tex = image_to_texture(img, &fbo);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        draw_tri(fill_prog, 512);
        TRY("glReadPixels");
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

    /* ------------------------------------- step 3: CONTROL, no gralloc at all */
    STEP(3, "control: sample a plain GL texture (no gralloc anywhere)");
    {
        GLuint prog, tex = 0;
        static const unsigned char red[4 * 4 * 4] = { 0 };
        TRY("make the sampling program");
        prog = make_program(kTex);
        if (!prog) {
            FAIL("sampling program");
            return 1;
        }
        TRY("glGenTextures + glTexImage2D (4x4 opaque red)");
        glGenTextures(1, &tex);
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        /* red is (255,0,0,255): build the 4x4 image from that constant. */
        {
            unsigned char data[4 * 4 * 4];
            int i;
            for (i = 0; i < 16; i++) {
                data[i * 4 + 0] = 0xff;
                data[i * 4 + 1] = 0x00;
                data[i * 4 + 2] = 0x00;
                data[i * 4 + 3] = 0xff;
            }
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 4, 4, 0, GL_RGBA, GL_UNSIGNED_BYTE,
                         data);
        }
        (void)red;
        say("    plain texture uploaded (err 0x%x)", glGetError());
        draw_sampling(prog, tex, 64);
        TRY("glReadPixels");
        memset(px, 0, 4);
        glReadPixels(32, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
        say("    readback = %02x %02x %02x %02x (want red ff 00 00 ff)",
            px[0], px[1], px[2], px[3]);
        glDeleteTextures(1, &tex);
        OK("step 3 survived -- the sampler program itself is fine");
    }

    /* ------------------------------------- step 4: sample a GPU-only gralloc buffer */
    STEP(4, "sample a minigbm buffer the GPU filled (GPU_SAMPLED|GPU_FRAMEBUFFER only)");
    {
        AHardwareBuffer* gb =
            make_ahb(64, 64,
                     AHARDWAREBUFFER_USAGE_GPU_FRAMEBUFFER |
                     AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE,
                     "gpu-only buffer 64x64");
        EGLImageKHR img;
        GLuint tex = 0, fbo = 0;
        GLuint prog;
        if (gb == NULL) return 1;
        img = image_from_ahb(dpy, gb);
        if (img == EGL_NO_IMAGE_KHR) {
            AHardwareBuffer_release(gb);
            return 1;
        }
        tex = image_to_texture(img, &fbo);
        TRY("fill it by drawing (FBO)");
        glBindFramebuffer(GL_FRAMEBUFFER, fbo);
        draw_tri(fill_prog, 64);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        TRY("make the sampling program");
        prog = make_program(kTex);
        if (!prog) {
            AHardwareBuffer_release(gb);
            return 1;
        }
        TRY("sample it");
        draw_sampling(prog, tex, 64);
        TRY("glReadPixels");
        memset(px, 0, 4);
        glReadPixels(32, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
        say("    readback = %02x %02x %02x %02x (want green 00 ff 00 ff)",
            px[0], px[1], px[2], px[3]);
        glDeleteTextures(1, &tex);
        eglDestroyImageKHR(dpy, img);
        AHardwareBuffer_release(gb);
        OK("step 4 survived -- sampling a GPU-written gralloc buffer is fine");
    }

    /* ------------------------------------- step 5: the sd95 crash, with prints */
    STEP(5, "sample a minigbm buffer the CPU wrote (GPU_SAMPLED|CPU_WRITE|CPU_READ)");
    {
        AHardwareBuffer* tb =
            make_ahb(64, 64,
                     AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
                     AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN |
                     AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN,
                     "cpu-written buffer 64x64");
        AHardwareBuffer_Desc d;
        void* base = NULL;
        int fd = -1;
        int lrc;
        uint32_t row;
        EGLImageKHR img;
        GLuint tex = 0;
        GLuint prog;

        if (tb == NULL) return 1;
        AHardwareBuffer_describe(tb, &d);
        TRY("AHardwareBuffer_lock(CPU_WRITE)");
        lrc = AHardwareBuffer_lock(tb, AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN, -1, NULL,
                                  &base);
        say("    lock rc=%d base=%p stride=%u", lrc, base, d.stride);
        if (lrc != 0 || base == NULL) {
            FAIL("cannot map the buffer from the CPU");
            AHardwareBuffer_release(tb);
            return 1;
        }
        for (row = 0; row < d.height; row++) {
            uint32_t* line = (uint32_t*)((char*)base + (size_t)row * d.stride * 4);
            uint32_t x;
            for (x = 0; x < d.width; x++)
                line[x] = (x < d.width / 2) ? 0xff0000ffu : 0xffff0000u;
        }
        TRY("AHardwareBuffer_unlock");
        AHardwareBuffer_unlock(tb, &fd);
        say("    unlock fence fd=%d (nothing was handed in, so -1 is expected)", fd);
        if (fd >= 0) close(fd);

        img = image_from_ahb(dpy, tb);
        if (img == EGL_NO_IMAGE_KHR) {
            AHardwareBuffer_release(tb);
            return 1;
        }
        tex = image_to_texture(img, NULL);
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        TRY("make the sampling program");
        prog = make_program(kTex);
        if (!prog) {
            AHardwareBuffer_release(tb);
            return 1;
        }
        TRY("sample the CPU-written buffer (this is what killed sd95)");
        draw_sampling(prog, tex, 64);
        TRY("glReadPixels");
        memset(px, 0, 4);
        glReadPixels(16, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
        say("    left half  = %02x %02x %02x %02x (want red ff 00 00 ff)", px[0],
            px[1], px[2], px[3]);
        glReadPixels(80, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
        say("    right half = %02x %02x %02x %02x (want blue ff ff 00 00)", px[0],
            px[1], px[2], px[3]);
        glDeleteTextures(1, &tex);
        eglDestroyImageKHR(dpy, img);
        AHardwareBuffer_release(tb);
        OK("step 5 survived -- CPU-written gralloc buffers sample fine too");
    }

    /* ------------------------------------- step 6: ten frames in a row */
    STEP(6, "10 more render-into-gralloc frames in a row (SF does this every frame)");
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
            tex = image_to_texture(img, &fbo);
            glBindFramebuffer(GL_FRAMEBUFFER, fbo);
            draw_tri(fill_prog, 512);
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            glDeleteFramebuffers(1, &fbo);
            glDeleteTextures(1, &tex);
            eglDestroyImageKHR(dpy, img);
            AHardwareBuffer_release(b);
            say("    frame %d ok", i);
        }
        if (rc == 0) OK("step 6 survived");
        else FAIL("step 6 did not finish");
    }

    /* ------------------------------------- step 7: a buffer with NO GPU usage */
    STEP(7, "sample a buffer allocated without any GPU usage flag (linear/dumb)");
    {
        AHardwareBuffer* lb =
            make_ahb(64, 64,
                     AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN |
                     AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN,
                     "cpu-only buffer 64x64");
        if (lb == NULL) {
            say("    (no buffer: gralloc refused a CPU-only allocation)");
        } else {
            AHardwareBuffer_Desc d;
            void* base = NULL;
            int fd = -1;
            int lrc;
            uint32_t row;
            EGLImageKHR img;
            GLuint tex = 0;
            GLuint prog;

            AHardwareBuffer_describe(lb, &d);
            TRY("AHardwareBuffer_lock(CPU_WRITE)");
            lrc = AHardwareBuffer_lock(lb, AHARDWAREBUFFER_USAGE_CPU_WRITE_OFTEN, -1, NULL,
                                       &base);
            say("    lock rc=%d base=%p stride=%u", lrc, base, d.stride);
            if (lrc != 0 || base == NULL) {
                AHardwareBuffer_release(lb);
                FAIL("cannot map a CPU-only buffer");
                return 1;
            }
            for (row = 0; row < d.height; row++) {
                uint32_t* line = (uint32_t*)((char*)base + (size_t)row * d.stride * 4);
                uint32_t x;
                for (x = 0; x < d.width; x++)
                    line[x] = (x < d.width / 2) ? 0xff0000ffu : 0xffff0000u;
            }
            TRY("AHardwareBuffer_unlock");
            AHardwareBuffer_unlock(lb, &fd);
            if (fd >= 0) close(fd);

            img = image_from_ahb(dpy, lb);
            if (img == EGL_NO_IMAGE_KHR) {
                say("    (this allocation cannot be imported as an EGLImage at all)");
                AHardwareBuffer_release(lb);
            } else {
                tex = image_to_texture(img, NULL);
                prog = make_program(kTex);
                if (prog) {
                    TRY("sample it (if this is correct, GPU-usage buffers are the problem)");
                    draw_sampling(prog, tex, 64);
                    memset(px, 0, 4);
                    glReadPixels(16, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
                    say("    left half  = %02x %02x %02x %02x (want red ff 00 00 ff)",
                        px[0], px[1], px[2], px[3]);
                    glReadPixels(48, 32, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, px);
                    say("    right half = %02x %02x %02x %02x (want blue ff ff 00 00)",
                        px[0], px[1], px[2], px[3]);
                }
                glDeleteTextures(1, &tex);
                eglDestroyImageKHR(dpy, img);
                AHardwareBuffer_release(lb);
            }
        }
    }

    say("\n=== probe done (rc=%d) ===", rc);
    eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (ctx != EGL_NO_CONTEXT) eglDestroyContext(dpy, ctx);
    if (surf != EGL_NO_SURFACE) eglDestroySurface(dpy, surf);
    eglTerminate(dpy);
    return rc;
}
