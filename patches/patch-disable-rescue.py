#!/usr/bin/env python3
"""sd75: stop RescueParty from escalating while the port is still being brought up.

Boot df79ab2d: system_server restarts every 5 s (pids 654, 753, 857, 961, 1923,
2027, 2131, 2235, 2339, 2443, 2547 ...). PackageWatchdog.noteBoot() counts those
as a boot loop and hands them to RescueParty, which had already reached

    W RescueParty: Attempting rescue level RESET_SETTINGS_TRUSTED_DEFAULTS
    E PackageManager: Failed rescue level RESET_SETTINGS_TRUSTED_DEFAULTS: ...
        'IActivityManager.getContentProvider ... on a null object reference'

(the action cannot even run, because AMS is not up yet). The ladder after that
is RESET_ALL_SETTINGS, then the highest level factory-resets /data and reboots
into recovery -- i.e. one more round of crashes would start deleting the very
state every round is trying to build.

RescueParty.isDisabled() honours persist.sys.disable_rescue, and
/system/etc/prop.default is read by PropertyLoadBootDefaults() before anything
else, on a file the first stage already publishes. The append has to happen in
the same place sys.use_memfd is appended: the overlay is re-created from the
image on every boot, so the file never carries last boot's additions.
"""

import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

OLD = '''    text += "# no ashmem driver in this kernel; libcutils must use memfd\\n";
    text += "sys.use_memfd=true\\n";
'''

NEW = '''    text += "# no ashmem driver in this kernel; libcutils must use memfd\\n";
    text += "sys.use_memfd=true\\n";
    // sd75: RescueParty read the system_server restart storm of boot df79ab2d as
    // a boot loop and escalated to RESET_SETTINGS_TRUSTED_DEFAULTS (which could
    // not even run, AMS was not up). The next levels reset all settings and then
    // factory-reset /data. Nothing on this port should be wiped by a watchdog
    // while it is still being brought up; see patches/patch-disable-rescue.py.
    text += "persist.sys.disable_rescue=true\\n";
'''

with open(PATH, encoding="utf-8") as f:
    text = f.read()

if "persist.sys.disable_rescue" in text:
    print("already patched (persist.sys.disable_rescue is published), nothing to do")
    sys.exit(0)

if OLD not in text:
    sys.exit("anchor for the prop.default append not found")

text = text.replace(OLD, NEW, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(text)

print("patched", PATH, "->", text.count("\n") + 1, "lines")