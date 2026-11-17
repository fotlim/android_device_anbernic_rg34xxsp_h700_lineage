// dsh-wifi-stub: publish a do-nothing "wifi" binder service.
//
// Why: boot a95db9e0 finally reached Android's boot phase 550 and then
// system_server died:
//
//   FATAL EXCEPTION IN SYSTEM PROCESS: main
//   java.lang.RuntimeException: Failed to boot service
//     org.lineageos.platform.internal.ProfileManagerService: onBootPhase threw
//     an exception during phase 550
//   Caused by: java.lang.NullPointerException: Attempt to invoke virtual method
//     'android.net.wifi.WifiInfo android.net.wifi.WifiManager.getConnectionInfo()'
//     on a null object reference
//     at org.lineageos.platform.internal.ProfileTriggerHelper.getActiveSSID(...)
//
// ProfileTriggerHelper.java:71 does
//     mWifiManager = (WifiManager) mContext.getSystemService(Context.WIFI_SERVICE);
// and :192 dereferences it unconditionally. That getSystemService returns null on
// this device because WifiFrameworkInitializer registers WifiManager with
// registerContextAwareService(), which can only build a wrapper when a binder named
// "wifi" exists -- and the framework only starts WifiService when the product
// declares FEATURE_WIFI, which this product does not.
//
// So register the binder. Nothing here implements wifi: every transaction gets an
// empty, non-exception reply, so getConnectionInfo() reads back a null WifiInfo,
// and getActiveSSID() already handles that ("if (wifiinfo == null) return null").
// That is exactly the state a wifi-less device should report, and it keeps the
// profile service's boot phase from taking down system_server.
//
// If a real wifi bring-up happens later, this stub has to be dropped again --
// a stale "wifi" binder would shadow the real WifiService.
#define LOG_TAG "dsh-wifi-stub"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#include <binder/Binder.h>
#include <binder/IPCThreadState.h>
#include <binder/IServiceManager.h>
#include <binder/Parcel.h>
#include <binder/ProcessState.h>
#include <log/log.h>
#include <utils/String16.h>

namespace {

class WifiStub final : public android::BBinder {
   public:
    const android::String16& getInterfaceDescriptor() const override {
        static const android::String16 kDescriptor("android.net.wifi.IWifiManager");
        return kDescriptor;
    }

    android::status_t onTransact(uint32_t /*code*/, const android::Parcel& data,
                                 android::Parcel* reply, uint32_t /*flags*/) override {
        (void)data;
        // No exception, and no payload: the generated client reads a null WifiInfo.
        reply->writeNoException();
        return android::NO_ERROR;
    }
};

}  // namespace

int main() {
    // A vendor binary's libbinder defaults to /dev/vndbinder, i.e. to
    // vndservicemanager: boot adb4643b shows the stub doing
    //   avc: ioctl path="/dev/binderfs/vndbinder" ioctlcmd=0x6201
    // and the framework still reporting
    //   SystemServiceRegistry: ServiceNotFoundException: No service published: wifi
    // because WifiManager's context-aware fetcher asks the *framework*
    // servicemanager on /dev/binder. Pin the driver explicitly.
    //
    // Boot e1625b6a then reported
    //   addService(wifi) ... failed: -129
    // which is not an errno: -129 is EX_TRANSACTION_FAILED from libbinder, i.e.
    // the transaction never reached servicemanager at all (strerror() prints the
    // unrelated EKEYREJECTED text for that number). So log what the driver open
    // itself returned, and fall back to the binderfs path.
    int probe_fd = open("/dev/binder", O_RDWR | O_CLOEXEC);
    ALOGI("open(/dev/binder) = %d%s", probe_fd,
          probe_fd < 0 ? " " : "");
    if (probe_fd < 0) {
        ALOGE("open(/dev/binder) errno=%d (%s)", errno, strerror(errno));
    } else {
        close(probe_fd);
    }

    android::sp<android::ProcessState> process_state =
        android::ProcessState::initWithDriver("/dev/binder");
    ALOGI("ProcessState::initWithDriver(/dev/binder) -> %p", process_state.get());
    if (process_state == nullptr) {
        ALOGE("initWithDriver(/dev/binder) failed; errno from the open() above");
        process_state = android::ProcessState::initWithDriver("/dev/binderfs/binder");
        ALOGI("ProcessState::initWithDriver(/dev/binderfs/binder) -> %p", process_state.get());
    }
    // A thread pool is not needed for a synchronous addService, but starting it
    // first removes one variable and costs nothing.
    android::ProcessState::self()->setThreadPoolMaxThreadCount(1);
    android::ProcessState::self()->startThreadPool();

    android::sp<android::IServiceManager> service_manager = android::defaultServiceManager();
    if (service_manager == nullptr) {
        ALOGE("no defaultServiceManager(); servicemanager is not up yet");
        return 1;
    }

    android::sp<WifiStub> stub = new WifiStub();
    android::status_t status = android::NAME_NOT_FOUND;
    for (int attempt = 0; attempt < 60; ++attempt) {
        status = service_manager->addService(android::String16("wifi"), stub,
                                            /*allowIsolated=*/false);
        if (status == android::NO_ERROR) break;
        ALOGW("addService(wifi) attempt %d failed: %d (%s)", attempt, status,
              strerror(-status));
        usleep(100000);
    }
    if (status != android::NO_ERROR) {
        ALOGE("could not publish the wifi stub: %d (%s)", status, strerror(-status));
        return 1;
    }
    ALOGI("published the wifi stub as service \"wifi\"");

    android::ProcessState::self()->startThreadPool();
    android::IPCThreadState::self()->joinThreadPool();
    return 0;
}