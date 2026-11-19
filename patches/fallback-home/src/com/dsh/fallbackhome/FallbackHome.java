package com.dsh.fallbackhome;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.UserManager;
import android.util.Log;
import android.view.Gravity;
import android.widget.TextView;

/**
 * sd111: minimal directBootAware HOME, a clone of AOSP's Settings FallbackHome.
 *
 * The product does not build com.android.settings, so during boot nothing
 * resolves as HOME for the still-locked user 0.  Because PackageManagerService
 * only matches MATCH_DIRECT_BOOT_AWARE components until the user is
 * unlocking/unlocked, the non-direct-boot launcher is filtered out and
 * resolveHomeActivity() returns null.  With no activity ever started, no idle
 * callback fires, so ActivityStackSupervisor.checkFinishBootingLocked() never
 * runs and ActivityManagerService.finishBooting() never happens: sys.boot_completed
 * is never set, the screen is never enabled, and user 0 stays BOOTING forever.
 *
 * This activity is directBootAware with a very low HOME priority (-1000), so it
 * is the only HOME candidate while the user is locked.  Once it draws and goes
 * idle it unblocks finishBooting() -> sendBootCompleted() -> finishUserBoot()
 * (which, on a device with ro.crypto.state=unsupported, unlocks user 0
 * immediately).  ACTION_USER_UNLOCKED then lets us hand off to the real
 * launcher and finish().
 *
 * It uses only public APIs on purpose (so it compiles without platform_apis and
 * does not need a privapp allowlist): ACTION_USER_UNLOCKED is spelled out as a
 * string literal because the constant is @SystemApi.
 */
public class FallbackHome extends Activity {
    private static final String TAG = "DshFallbackHome";
    private static final String ACTION_USER_UNLOCKED = "android.intent.action.USER_UNLOCKED";
    private static final long RECHECK_MS = 2000L;

    private final Handler mHandler = new Handler(Looper.getMainLooper());
    private boolean mDone;

    private final BroadcastReceiver mReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            Log.i(TAG, "broadcast " + intent.getAction());
            maybeFinish();
        }
    };

    private final Runnable mRecheck = new Runnable() {
        @Override
        public void run() {
            if (mDone) {
                return;
            }
            maybeFinish();
            if (!mDone) {
                mHandler.postDelayed(this, RECHECK_MS);
            }
        }
    };

    @Override
    protected void onCreate(Bundle icicle) {
        super.onCreate(icicle);
        TextView tv = new TextView(this);
        tv.setText("Starting\u2026");
        tv.setTextSize(28f);
        tv.setGravity(Gravity.CENTER);
        tv.setBackgroundColor(0xFF000000);
        tv.setTextColor(0xFFFFFFFF);
        setContentView(tv);

        IntentFilter f = new IntentFilter(ACTION_USER_UNLOCKED);
        f.addAction(Intent.ACTION_BOOT_COMPLETED);
        registerReceiver(mReceiver, f);

        mHandler.post(mRecheck);
        maybeFinish();
    }

    @Override
    protected void onDestroy() {
        mDone = true;
        mHandler.removeCallbacksAndMessages(null);
        try {
            unregisterReceiver(mReceiver);
        } catch (IllegalArgumentException ignored) {
            // receiver may not have been registered if onCreate threw early
        }
        super.onDestroy();
    }

    private void maybeFinish() {
        if (mDone) {
            return;
        }
        UserManager um = (UserManager) getSystemService(Context.USER_SERVICE);
        if (um == null || !um.isUserUnlocked()) {
            Log.i(TAG, "user not unlocked yet");
            return;
        }
        Intent home = new Intent(Intent.ACTION_MAIN);
        home.addCategory(Intent.CATEGORY_HOME);
        home.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ResolveInfo ri = getPackageManager().resolveActivity(
                home, PackageManager.MATCH_DEFAULT_ONLY);
        String pkg = (ri != null && ri.activityInfo != null)
                ? ri.activityInfo.packageName : null;
        if (pkg != null && !getPackageName().equals(pkg)) {
            Log.i(TAG, "user unlocked; handing off to " + ri.activityInfo.name);
            mDone = true;
            try {
                startActivity(home);
            } catch (RuntimeException e) {
                Log.w(TAG, "startActivity(real home) failed; staying", e);
                mDone = false;
                return;
            }
            finish();
        } else {
            Log.i(TAG, "user unlocked but no other home yet (pkg=" + pkg + ")");
        }
    }
}
