#!/usr/bin/env python3
"""sd76: let drm_hwcomposer find the DRM node that actually has displays.

Boot 661f9531 (recovered from lost FAT chains, see the dev log) finally shows the
composer failing for a real reason instead of silently:

    E hwc-resource-manager: Failed to initialize any displays
    E hwc-drm-two: Can't initialize the resource manager -22
    E hwc-drm-two: Failed to initialize DrmHwcTwo err=6
    E android.hardware.graphics.composer@2.1-service: failed to open hwcomposer
        device: Invalid argument

`drm/resourcemanager.cpp:35`:

    int path_len = property_get("hwc.drm.device", path_pattern, "/dev/dri/card0");
    if (path_pattern[path_len - 1] != '%') {
        ret = AddDrmDevice(std::string(path_pattern));      // exactly one node
    } else {
        for (int idx = 0; !ret; ++idx) AddDrmDevice("...card" + idx);   // walk
    }
    if (!num_displays_) { ALOGE("Failed to initialize any displays"); ... }

and `ls /sys/class/drm` on this board:

    card0                 <- panfrost (the GPU): opens, no connectors, no KMS
    card1
    card1-DSI-1           <- the panel is here
    card1-HDMI-A-1
    /proc/fb: 0 sun4i-drmdrmfb

So the composer opened card0 (the GPU node), found no displays, and gave up. Our
own 97-graphics-device.rc is the reason it only ever looked at card0: it has been
doing `setprop hwc.drm.device /dev/dri/card0` since sd39, when card0 was still
the KMS node.

Fix: point hwc.drm.device at **/dev/dri/card1**. The wildcard form
(`/dev/dri/card%`) would be nicer, but ResourceManager::Init() stops at the first
node that errors and card0 errors out (`DrmDevice::Init` fails on panfrost before
any display is added), so the walk would never reach card1 -- making the walk
tolerant of failures-before-any-display is the robust follow-up (see HANDOVER
backlog). Both places that set the property are changed, because the rc runs
during boot and would otherwise overwrite the prop.default value.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

DEVICE = "/dev/dri/card1"

RC_OLD = '"    setprop hwc.drm.device /dev/dri/card0\\n";'
RC_NEW = '"    setprop hwc.drm.device %s\\n";' % DEVICE

PROP_ANCHOR = '''    text += "persist.sys.disable_rescue=true\\n";
'''

PROP_ADD = PROP_ANCHOR + '''    // sd76: drm_hwcomposer reads hwc.drm.device (drm/resourcemanager.cpp:35) and
    // its default is /dev/dri/card0, which on this board is panfrost -- the GPU
    // node, with no connectors at all. The DSI panel and HDMI are on card1
    // (sun4i-drm, the "sun4i-drmdrmfb" behind /proc/fb). Opening card0 therefore
    // ends in "hwc-resource-manager: Failed to initialize any displays".
    text += "hwc.drm.device=%s\\n";
''' % DEVICE

MARK = "hwc.drm.device=" + DEVICE

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if MARK in text and RC_NEW in text:
    print("already patched (hwc.drm.device points at %s), nothing to do" % DEVICE)
    sys.exit(0)

changed = []

if RC_NEW not in text:
    if RC_OLD not in text:
        sys.exit("anchor for the rc setprop not found")
    text = text.replace(RC_OLD, RC_NEW, 1)
    changed.append("97-graphics-device.rc setprop -> %s" % DEVICE)

if MARK not in text:
    if PROP_ANCHOR not in text:
        sys.exit("anchor for the prop.default append not found")
    text = text.replace(PROP_ANCHOR, PROP_ADD, 1)
    changed.append("prop.default gains " + MARK)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "->", text.count("\n") + 1, "lines; " + ", ".join(changed))