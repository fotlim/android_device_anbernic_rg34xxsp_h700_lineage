#!/usr/bin/env python3
"""CONTINGENCY PATCH -- do not run unless the sd50 probe asks for it.

This file is deliberately NOT listed in patches/apply.sh.  It encodes the fix
for exactly one of the two outcomes the sd50 devprobe can produce:

    Branch A -- "the binder nodes are gone after the namespace switch"

The evidence that would select this branch (see docs/HANDOVER.md and
2026-11-12 dev log sections 7 and 8.4):

  * devprobe[post-fs-data] / devprobe[boot] show /dev/binderfs empty, or
    /dev/binder present as a symlink whose target reports MISSING;
  * `grep " /dev/" /proc/self/mountinfo` inside the probe does not contain the
    binderfs mount while /proc/1/mountinfo does;
  * `open /dev/binder` from the probe fails.

Why moving the mount into the first stage is the fix
----------------------------------------------------
SetupMountNamespaces() (system/core/init/init.cpp) clones the mount tree to
build the "default" namespace, and init.rc does its binderfs work in
`on early-init` -- an action that runs in the second stage, i.e. after that
clone.  A mount created after the clone lives in one namespace only: the
directory entry /dev/binderfs is shared (same tmpfs), the mount on top of it is
not, so the symlinks /dev/binder, /dev/hwbinder, /dev/vndbinder dangle for
everything that runs in the default namespace.

Mounting binderfs a few lines after the /dev tmpfs is created -- inside
FirstStageMain(), before the clone -- makes the mount part of the tree that is
copied, so every namespace inherits it.  init.rc's later
`mount binder binder /dev/binderfs stats=global` then fails with EBUSY, which is
expected; it is a wasted command, not a regression.

Nothing else changes: the nodes, their 0666 mode and the symlink paths are the
same ones init.rc would have created.

STATUS: written while WSL was down (a zombie vmmemWSL holding ext4.vhdx), so it
has NOT been compiled.  After WSL is back, before trusting it:

    bash patches/patch-binderfs-early.py     # inserts, or says MISSING anchor
    m init_first_stage -j4                   # must build clean
    strings -a .../init | grep -c MountBinderfsEarly

If the anchor is missing the script changes nothing and exits 1.
"""
import sys

PATH = "/root/android/lineage/system/core/init/first_stage_init.cpp"

BEGIN = "// ---- sd51: mount binderfs before the mount namespaces are created ----------"
END = "// ---- end sd51 ---------------------------------------------------------------"

# The one line we know verbatim from this tree: our patched first stage mounts a
# fresh tmpfs on /dev (debug-init unmounts /dev before it execs /init.android, so
# something has to put one back).  The binderfs mount has to follow it
# immediately, while we are still in the only namespace that exists.
ANCHOR = 'CHECKCALL(mount("tmpfs", "/dev", "tmpfs", MS_NOSUID, "mode=0755"));\n'
CALL_LINE = "    MountBinderfsEarly();\n"

NEW = r'''// ---- sd51: mount binderfs before the mount namespaces are created ----------
// SetupMountNamespaces() clones the mount tree; anything mounted after that
// lives in one namespace only.  init.rc mounts binderfs in early-init, which is
// already after the clone, so if the sd50 probe shows an empty /dev/binderfs and
// a dangling /dev/binder in the default namespace, the mount belongs here
// instead: created before the clone, inherited by every namespace.  The later
// init.rc mount then fails with EBUSY, which is expected and harmless.
static void MountBinderfsEarly() {
    if (mkdir("/dev/binderfs", 0755) != 0 && errno != EEXIST) {
        LOG(ERROR) << "binderfs: mkdir failed: " << errno << " (" << strerror(errno) << ")";
        return;
    }
    if (mount("binder", "/dev/binderfs", "binder", 0, "stats=global") != 0 &&
        errno != EBUSY) {
        LOG(ERROR) << "binderfs: mount failed: " << errno << " (" << strerror(errno) << ")";
        return;
    }
    for (const char* name : {"binder", "hwbinder", "vndbinder"}) {
        std::string node = std::string("/dev/binderfs/") + name;
        std::string link = std::string("/dev/") + name;
        if (symlink(node.c_str(), link.c_str()) != 0 && errno != EEXIST) {
            LOG(ERROR) << "binderfs: symlink " << link << " failed: " << errno;
        }
        chmod(node.c_str(), 0666);
    }
    LOG(INFO) << "binderfs: mounted in the first stage, before the namespace clone";
}
// ---- end sd51 ---------------------------------------------------------------
'''


def main() -> int:
    try:
        with open(PATH, encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        print("cannot read %s: %s" % (PATH, e), file=sys.stderr)
        return 1

    block = NEW.strip("\n")

    begin = src.find(BEGIN)
    if begin >= 0:
        end = src.find(END, begin)
        if end < 0:
            print("END marker not found", file=sys.stderr)
            return 1
        end += len(END)
        out = src[:begin] + block + src[end:]
        print("replaced block: %d -> %d bytes" % (end - begin, len(block)))
    else:
        if ANCHOR not in src:
            print("MISSING anchor (first stage /dev tmpfs mount); nothing changed",
                  file=sys.stderr)
            return 1
        if "MountBinderfsEarly();" in src:
            print("call site already present; nothing to do")
            return 0
        out = src.replace(ANCHOR, ANCHOR + CALL_LINE, 1)
        out = out.replace("void FirstStageMain(", block + "\n\nvoid FirstStageMain(", 1) \
            if "void FirstStageMain(" in out else out
        if BEGIN not in out:
            # No FirstStageMain in this file layout: put the helper above the
            # call site instead, which is still file scope.
            out = out.replace(CALL_LINE, block + "\n\n" + CALL_LINE, 1)
        print("inserted block: %d bytes, %d -> %d lines"
              % (len(block), src.count("\n"), out.count("\n")))

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
