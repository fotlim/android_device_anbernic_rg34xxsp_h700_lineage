package com.dsh.fallbackhome;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.graphics.drawable.Drawable;
import android.os.Bundle;
import android.os.UserManager;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.FrameLayout;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/**
 * sd123: minimal View-based launcher used as HOME on this port.
 *
 * Quickstep's complex HWUI rendering faults the panfrost driver (js
 * DATA_INVALID_FAULT) and crash-loops the launcher; plain View rendering (like
 * SystemUI's status bar) works, so this shows a simple grid of launchable apps.
 * It is also the directBootAware HOME that lets the boot complete while user 0 is
 * still locked (see patches/fallback-home/AndroidManifest.xml).
 */
public class FallbackHome extends Activity {
    private static final String TAG = "DshFallbackHome";
    private static final String ACTION_USER_UNLOCKED = "android.intent.action.USER_UNLOCKED";

    private GridView mGrid;
    private TextView mHint;
    private AppAdapter mAdapter;
    private final List<ResolveInfo> mApps = new ArrayList<>();

    private final BroadcastReceiver mReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            Log.i(TAG, "broadcast " + intent.getAction());
            refresh();
        }
    };

    @Override
    protected void onCreate(Bundle icicle) {
        super.onCreate(icicle);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF101418);

        mGrid = new GridView(this);
        int pad = dp(8);
        mGrid.setPadding(pad, dp(40), pad, dp(40));
        mGrid.setNumColumns(5);
        mGrid.setHorizontalSpacing(dp(4));
        mGrid.setVerticalSpacing(dp(12));
        mGrid.setStretchMode(GridView.STRETCH_COLUMN_WIDTH);
        mAdapter = new AppAdapter();
        mGrid.setAdapter(mAdapter);
        mGrid.setOnItemClickListener((parent, view, position, id) -> {
            if (position < 0 || position >= mApps.size()) return;
            ResolveInfo ri = mApps.get(position);
            Intent launch = new Intent(Intent.ACTION_MAIN)
                    .addCategory(Intent.CATEGORY_LAUNCHER)
                    .setComponent(new ComponentName(ri.activityInfo.packageName, ri.activityInfo.name))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try { startActivity(launch); } catch (RuntimeException e) { Log.w(TAG, "launch failed", e); }
        });
        root.addView(mGrid, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        mHint = new TextView(this);
        mHint.setText("Starting\u2026");
        mHint.setTextSize(24f);
        mHint.setTextColor(Color.WHITE);
        mHint.setGravity(Gravity.CENTER);
        root.addView(mHint, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(root);

        registerReceiver(mReceiver, new IntentFilter(ACTION_USER_UNLOCKED));
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    @Override
    protected void onDestroy() {
        try { unregisterReceiver(mReceiver); } catch (IllegalArgumentException ignored) {}
        super.onDestroy();
    }

    private void refresh() {
        UserManager um = (UserManager) getSystemService(Context.USER_SERVICE);
        boolean unlocked = um != null && um.isUserUnlocked();
        Log.i(TAG, "refresh unlocked=" + unlocked);
        if (!unlocked) {
            mHint.setVisibility(View.VISIBLE);
            mGrid.setVisibility(View.GONE);
            return;
        }
        loadApps();
        mHint.setVisibility(View.GONE);
        mGrid.setVisibility(View.VISIBLE);
        mAdapter.notifyDataSetChanged();
    }

    private void loadApps() {
        PackageManager pm = getPackageManager();
        Intent i = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> list = pm.queryIntentActivities(i, 0);
        mApps.clear();
        for (ResolveInfo ri : list) {
            if (ri.activityInfo == null) continue;
            if (getPackageName().equals(ri.activityInfo.packageName)) continue;
            mApps.add(ri);
        }
        Collections.sort(mApps, new Comparator<ResolveInfo>() {
            @Override public int compare(ResolveInfo a, ResolveInfo b) {
                CharSequence la = a.loadLabel(pm), lb = b.loadLabel(pm);
                return la.toString().compareToIgnoreCase(lb.toString());
            }
        });
        Log.i(TAG, "loadApps count=" + mApps.size());
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    private final class AppAdapter extends BaseAdapter {
        @Override public int getCount() { return mApps.size(); }
        @Override public Object getItem(int p) { return mApps.get(p); }
        @Override public long getItemId(int p) { return p; }
        @Override public View getView(int position, View convert, ViewGroup parent) {
            LinearLayout ll = new LinearLayout(FallbackHome.this);
            ll.setOrientation(LinearLayout.VERTICAL);
            ll.setGravity(Gravity.CENTER_HORIZONTAL);
            ll.setPadding(0, dp(4), 0, dp(4));
            ResolveInfo ri = mApps.get(position);
            ImageView iv = new ImageView(FallbackHome.this);
            int s = dp(44);
            iv.setLayoutParams(new LinearLayout.LayoutParams(s, s));
            try {
                Drawable d = ri.loadIcon(getPackageManager());
                iv.setImageDrawable(d);
            } catch (Exception ignored) {}
            TextView tv = new TextView(FallbackHome.this);
            tv.setTextSize(11f);
            tv.setTextColor(0xFFE6E6E6);
            tv.setGravity(Gravity.CENTER);
            tv.setSingleLine(true);
            tv.setEllipsize(android.text.TextUtils.TruncateAt.END);
            try { tv.setText(ri.loadLabel(getPackageManager())); } catch (Exception ignored) {}
            ll.addView(iv);
            ll.addView(tv);
            return ll;
        }
    }
}
