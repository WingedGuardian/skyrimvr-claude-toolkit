"""Plan 6 Task 1: apply / remove / show the four candidate lock mechanisms (R2).

    python lockprobe.py apply  <M-A|M-B|M-C|M-D> <file>
    python lockprobe.py remove <file>
    python lockprobe.py show   <file>

    M-A  read-only attribute                      attrib +R
    M-B  file-level, non-inherited deny (WD,AD)   icacls /deny *<SID>:(WD,AD)
    M-C  M-A + M-B
    M-D  M-A + deny (WD,AD,WA,WEA)                denying write-attributes stops -Force clearing +R

Every call is sandbox-asserted on the RESOLVED path (and apply also requires a
single-link file that is not a real-install file ID). Masks are whitelisted to
{WD, AD, WA, WEA}; anything else raises before icacls runs. Never on a folder.
Removal order matters: the deny ACE is removed FIRST (with WA denied, attrib -R
would itself be refused), then the bit. After every action the state is re-read.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sandbox  # noqa: E402

ALLOWED = frozenset({"WD", "AD", "WA", "WEA"})
MECHANISMS = {
    "M-A": (True, None),
    "M-B": (False, ("WD", "AD")),
    "M-C": (True, ("WD", "AD")),
    "M-D": (True, ("WD", "AD", "WA", "WEA")),
}
_SID = None


class MaskNotAllowed(ValueError):
    pass


def check_mask(mask) -> str:
    tokens = [t.strip().upper() for t in mask]
    if not tokens:
        raise MaskNotAllowed("empty mask")
    bad = [t for t in tokens if t not in ALLOWED]
    if bad:
        raise MaskNotAllowed("mask tokens outside the whitelist %s: %s" % (sorted(ALLOWED), bad))
    return ",".join(tokens)


def user_sid() -> str:
    global _SID
    if _SID is None:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
        m = re.search(r'"(S-1-[0-9-]+)"', out)
        if not m:
            raise RuntimeError("could not read the current user's SID: %r" % out)
        _SID = m.group(1)
    return _SID


def _win(p: str) -> str:
    return os.path.normpath(p)


def _run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def state(path: str) -> dict:
    st = os.stat(path)
    rc, acl = _run(["icacls", _win(path)])
    lines = [ln.strip() for ln in acl.splitlines() if ln.strip() and not ln.startswith("Successfully")]
    deny = [ln for ln in lines if "(DENY)" in ln]
    explicit = [ln for ln in lines if "(I)" not in ln]
    return {
        "readonly": bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY),
        "attrs": st.st_file_attributes,
        "nlink": st.st_nlink,
        "deny_aces": deny,
        "explicit_aces": explicit,
    }


def apply(mech: str, path: str) -> dict:
    target = sandbox.assert_lockable(path)
    if not os.path.isfile(target):
        raise SystemExit("not a regular file (never lock a folder): %s" % target)
    ro, mask = MECHANISMS[mech]
    before = state(target)
    # MEASURED 2026-10-04: the bit must go on BEFORE the deny. With (WA) denied first,
    # `attrib +R` itself prints "Access denied" -- and exits 0.
    if ro:
        rc, out = _run(["attrib", "+R", _win(target)])
        if rc != 0 or out:
            raise RuntimeError("attrib +R failed rc=%d: %s" % (rc, out))
    if mask:
        m = check_mask(mask)
        rc, out = _run(["icacls", _win(target), "/deny", "*%s:(%s)" % (user_sid(), m)])
        if rc != 0:
            raise RuntimeError("icacls deny failed rc=%d: %s" % (rc, out))
    after = state(target)
    return {"mech": mech, "before": before, "after": after}


def remove(path: str) -> dict:
    target = sandbox.assert_inside(path)
    rc1, out1 = _run(["icacls", _win(target), "/remove:d", "*%s" % user_sid()])
    rc2, out2 = _run(["attrib", "-R", _win(target)])
    after = state(target)
    ok = (not after["readonly"]) and not after["deny_aces"]
    return {"icacls_rc": rc1, "icacls_out": out1, "attrib_rc": rc2, "attrib_out": out2,
            "after": after, "clean": ok}


def is_locked(path: str) -> bool:
    s = state(path)
    return s["readonly"] or bool(s["deny_aces"])


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "apply" and a[1] in MECHANISMS:
        print(json.dumps(apply(a[1], a[2]), indent=1))
    elif len(a) == 2 and a[0] == "remove":
        res = remove(a[1])
        print(json.dumps(res, indent=1))
        sys.exit(0 if res["clean"] else 1)
    elif len(a) == 2 and a[0] == "show":
        print(json.dumps(state(sandbox.assert_inside(a[1])), indent=1))
    else:
        print(__doc__)
        sys.exit(2)
