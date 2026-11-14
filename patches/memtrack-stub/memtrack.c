/*
 * memtrack.default.so -- a deliberately empty legacy memtrack HAL.
 *
 * Why it exists: system/core's ActivityManagerService builds ProcessStats in its
 * constructor, which asks Debug.getMemoryInfo for per-process graphics memory.
 * That path (frameworks/base/core/jni/android_os_Debug.cpp,
 * read_memtrack_memory -> memtrack_proc_get) blocks on the HIDL service
 * android.hardware.memtrack@1.0::IMemtrack/default until it is registered, and
 * the HIDL service is a passthrough wrapper that needs hw_get_module("memtrack").
 * hardware/libhardware/modules/memtrack does not exist in LineageOS 18.1, so the
 * service exited 1 forever, so memtrack_proc_get never returned, so the
 * system_server main thread sat inside ActivityManagerService.<init> until the
 * Watchdog killed it (boot b8c70ff4: "Blocked in handler on main thread (main)"
 * at android.os.Debug.getMemoryInfo, 60 s, every cycle).
 *
 * Memtrack.cpp treats a NULL getMemory as "SUCCESS with no records" and calls
 * init() once, so this stub only has to exist and answer init(). Do not add
 * getMemory here: no board-specific accounting exists, and an empty answer is
 * correct.
 */
#include <hardware/hardware.h>
#include <hardware/memtrack.h>

static int stub_init(const struct memtrack_module *module)
{
    (void)module;
    return 0;
}

struct memtrack_module HAL_MODULE_INFO_SYM = {
    .common = {
        .tag = HARDWARE_MODULE_TAG,
        .module_api_version = MEMTRACK_MODULE_API_VERSION_0_1,
        .hal_api_version = HARDWARE_HAL_API_VERSION,
        .id = MEMTRACK_HARDWARE_MODULE_ID,
        .name = "rg34xxsp memtrack stub",
        .author = "dsh",
        .methods = NULL,
    },
    .init = stub_init,
    .getMemory = NULL,
};