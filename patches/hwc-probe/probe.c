// dsh-hwcprobe: replay exactly what android.hardware.graphics.composer@2.x-service
// does with the conventional hwcomposer module, and print every step to stdout.
//
// Why this exists: the composer service dies with status 1 one millisecond afte
// it dlopen()s hwcomposer.drm_minigbm.so, and not one byte of its ALOG output
// reaches live-logcat.txt -- neither from init's restarts nor from the manual
// `timeout 3` run the live snapshot does.  The loader it uses
// (composer-passthrough/2.1/HwcLoader.h) has exactly four ways to return null
// and all four are either silent or log through logd, so the boot log cannot
// tell them apart.  This probe walks the same path and writes to a file that
// the snapshot copies off the card, so the answer survives logd.
//
// Path under test, in order:
//   1. hw_get_module("hwcomposer")        -> module id / versions / methods
//   2. module->methods->open("hwcomposer")-> hw_device_t (this is the step that
//      reaches drm_hwcomposer's HookDevOpen -> DrmHwcTwo::Init -> ResourceManager)
//   3. device->version                    -> major must be 2, otherwise the
//      loader wraps it in HWC2On1Adapte
//   4. device->getCapabilities            -> PRESENT_FENCE_IS_NOT_RELIABLE makes
//      initWithDevice() refuse with "present fence must be reliable"
//   5. device->getFunction(<each HWC2 2.1 descriptor>) -> a missing one makes
//      initDispatch() return false, which is a silent null
#define LOG_TAG "dsh-hwcprobe"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <dlfcn.h>
#include <errno.h>

#include <hardware/hardware.h>
#include <hardware/hwcomposer.h>
#include <hardware/hwcomposer2.h>

struct RequiredFn {
    int32_t id;
    const char* name;
};

// Every descriptor HwcHalImpl::initDispatch() insists on for the 2.1 wrappe
// (hardware/interfaces/graphics/composer/2.1/utils/passthrough/.../HwcHal.h,
// lines 515-575).  setLayerSidebandStream is conditional on the capability, so
// it is reported but not counted as missing.
static const struct RequiredFn kRequired[] = {
    {HWC2_FUNCTION_ACCEPT_DISPLAY_CHANGES, "acceptDisplayChanges"},
    {HWC2_FUNCTION_CREATE_LAYER, "createLayer"},
    {HWC2_FUNCTION_CREATE_VIRTUAL_DISPLAY, "createVirtualDisplay"},
    {HWC2_FUNCTION_DESTROY_LAYER, "destroyLayer"},
    {HWC2_FUNCTION_DESTROY_VIRTUAL_DISPLAY, "destroyVirtualDisplay"},
    {HWC2_FUNCTION_DUMP, "dump"},
    {HWC2_FUNCTION_GET_ACTIVE_CONFIG, "getActiveConfig"},
    {HWC2_FUNCTION_GET_CHANGED_COMPOSITION_TYPES, "getChangedCompositionTypes"},
    {HWC2_FUNCTION_GET_CLIENT_TARGET_SUPPORT, "getClientTargetSupport"},
    {HWC2_FUNCTION_GET_COLOR_MODES, "getColorModes"},
    {HWC2_FUNCTION_GET_DISPLAY_ATTRIBUTE, "getDisplayAttribute"},
    {HWC2_FUNCTION_GET_DISPLAY_CONFIGS, "getDisplayConfigs"},
    {HWC2_FUNCTION_GET_DISPLAY_NAME, "getDisplayName"},
    {HWC2_FUNCTION_GET_DISPLAY_REQUESTS, "getDisplayRequests"},
    {HWC2_FUNCTION_GET_DISPLAY_TYPE, "getDisplayType"},
    {HWC2_FUNCTION_GET_DOZE_SUPPORT, "getDozeSupport"},
    {HWC2_FUNCTION_GET_HDR_CAPABILITIES, "getHdrCapabilities"},
    {HWC2_FUNCTION_GET_MAX_VIRTUAL_DISPLAY_COUNT, "getMaxVirtualDisplayCount"},
    {HWC2_FUNCTION_GET_RELEASE_FENCES, "getReleaseFences"},
    {HWC2_FUNCTION_PRESENT_DISPLAY, "presentDisplay"},
    {HWC2_FUNCTION_REGISTER_CALLBACK, "registerCallback"},
    {HWC2_FUNCTION_SET_ACTIVE_CONFIG, "setActiveConfig"},
    {HWC2_FUNCTION_SET_CLIENT_TARGET, "setClientTarget"},
    {HWC2_FUNCTION_SET_COLOR_MODE, "setColorMode"},
    {HWC2_FUNCTION_SET_COLOR_TRANSFORM, "setColorTransform"},
    {HWC2_FUNCTION_SET_CURSOR_POSITION, "setCursorPosition"},
    {HWC2_FUNCTION_SET_LAYER_BLEND_MODE, "setLayerBlendMode"},
    {HWC2_FUNCTION_SET_LAYER_BUFFER, "setLayerBuffer"},
    {HWC2_FUNCTION_SET_LAYER_COLOR, "setLayerColor"},
    {HWC2_FUNCTION_SET_LAYER_COMPOSITION_TYPE, "setLayerCompositionType"},
    {HWC2_FUNCTION_SET_LAYER_DATASPACE, "setLayerDataspace"},
    {HWC2_FUNCTION_SET_LAYER_DISPLAY_FRAME, "setLayerDisplayFrame"},
    {HWC2_FUNCTION_SET_LAYER_PLANE_ALPHA, "setLayerPlaneAlpha"},
    {HWC2_FUNCTION_SET_LAYER_SOURCE_CROP, "setLayerSourceCrop"},
    {HWC2_FUNCTION_SET_LAYER_SURFACE_DAMAGE, "setLayerSurfaceDamage"},
    {HWC2_FUNCTION_SET_LAYER_TRANSFORM, "setLayerTransform"},
    {HWC2_FUNCTION_SET_LAYER_VISIBLE_REGION, "setLayerVisibleRegion"},
    {HWC2_FUNCTION_SET_LAYER_Z_ORDER, "setLayerZOrder"},
    {HWC2_FUNCTION_SET_OUTPUT_BUFFER, "setOutputBuffer"},
    {HWC2_FUNCTION_SET_POWER_MODE, "setPowerMode"},
    {HWC2_FUNCTION_SET_VSYNC_ENABLED, "setVsyncEnabled"},
    {HWC2_FUNCTION_VALIDATE_DISPLAY, "validateDisplay"},
};

static const char* CapabilityName(int32_t capability) {
    switch (capability) {
        case HWC2_CAPABILITY_SIDEBAND_STREAM:
            return "SIDEBAND_STREAM";
        case HWC2_CAPABILITY_SKIP_CLIENT_COLOR_TRANSFORM:
            return "SKIP_CLIENT_COLOR_TRANSFORM";
        case HWC2_CAPABILITY_PRESENT_FENCE_IS_NOT_RELIABLE:
            return "PRESENT_FENCE_IS_NOT_RELIABLE";
        case HWC2_CAPABILITY_SKIP_VALIDATE:
            return "SKIP_VALIDATE";
        default:
            return "unknown";
    }
}

int main(int argc, char** argv) {
    (void)argc;
    (void)argv;
    printf("== dsh-hwcprobe ==\n");
    printf("uid=%d euid=%d gid=%d egid=%d\n", (int)getuid(), (int)geteuid(), (int)getgid(),
           (int)getegid());

    hw_module_t const* module = NULL;
    int err = hw_get_module(HWC_HARDWARE_MODULE_ID, &module);
    printf("1. hw_get_module(\"hwcomposer\") = %d\n", err);
    if (err != 0 || module == NULL) {
        // The loader's own fallback would be the gralloc module; print whethe
        // the module .so can even be dlopen()ed so a broken NEEDED list shows up
        // here instead of as a bare exit code.
        void* handle = dlopen("hwcomposer.drm_minigbm.so", RTLD_NOW);
        printf("   hw_get_module failed; direct dlopen(\"hwcomposer.drm_minigbm.so\") = %p\n",
               handle);
        if (handle == NULL) printf("   dlerror: %s\n", dlerror());
        return 1;
    }

    printf("   module id=%s name=%s module_api_version=0x%08x hal_api_version=0x%08x\n",
           module->id ? module->id : "(null)", module->name ? module->name : "(null)",
           (unsigned)module->module_api_version, (unsigned)module->hal_api_version);
    printf("   author=%s methods=%p\n", module->author ? module->author : "(null)",
           (void*)module->methods);
    if (module->methods == NULL || module->methods->open == NULL) {
        printf("2. module->methods or methods->open is NULL\n");
        return 1;
    }

    hw_device_t* device = NULL;
    err = module->methods->open(module, HWC_HARDWARE_COMPOSER, &device);
    printf("2. methods->open(HWC_HARDWARE_COMPOSER) = %d (%s)\n", err,
           err ? strerror(err < 0 ? -err : err) : "ok");
    if (err != 0 || device == NULL) return 1;

    const int major = (device->version >> 24) & 0xf;
    const int minor = (device->version >> 16) & 0xf;
    printf("3. device version=0x%08x major=%d minor=%d -> %s\n", (unsigned)device->version, major,
           minor, major == 2 ? "used directly (no HWC2On1Adapter)" : "wrapped by HWC2On1Adapter");

    hwc2_device_t* device2 = (hwc2_device_t*)device;
    uint32_t capability_count = 0;
    if (device2->getCapabilities != NULL) {
        device2->getCapabilities(device2, &capability_count, NULL);
        printf("4. capabilities: %u", capability_count);
        if (capability_count > 0 && capability_count < 16) {
            int32_t capabilities[16];
            uint32_t count = capability_count;
            uint32_t i;
            device2->getCapabilities(device2, &count, capabilities);
            for (i = 0; i < count && i < 16; i++) {
                printf(" %d(%s)", capabilities[i], CapabilityName(capabilities[i]));
            }
        }
        printf("\n");
    } else {
        printf("4. device->getCapabilities is NULL\n");
    }

    if (device2->getFunction == NULL) {
        printf("5. device->getFunction is NULL\n");
        return 1;
    }
    int missing = 0;
    size_t i;
    for (i = 0; i < sizeof(kRequired) / sizeof(kRequired[0]); i++) {
        if (device2->getFunction(device2, kRequired[i].id) == NULL) {
            printf("5. MISSING getFunction(%d) %s\n", kRequired[i].id, kRequired[i].name);
            missing++;
        }
    }
    const bool sideband =
        device2->getFunction(device2, HWC2_FUNCTION_SET_LAYER_SIDEBAND_STREAM) != NULL;
    printf("5. required descriptors=%d missing=%d setLayerSidebandStream=%s\n",
           (int)(sizeof(kRequired) / sizeof(kRequired[0])), missing, sideband ? "yes" : "no");
    printf("verdict: %s\n",
           missing == 0 ? "loader would return a composer" : "initDispatch fails");

    device2->common.close(&device2->common);
    return 0;
}