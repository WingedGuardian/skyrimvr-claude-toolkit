"""inventory-runtimes -- which of Python, Git and Node actually START here, and what to do about the rest.

Looks first, reports, and prints a plan. It NEVER installs, upgrades, repairs or removes anything:
its single use of `subprocess` is the `--version` launch in `launch()`. The only install command
it can ever print is the one the user's own task authorized, by id and exact version, and it is
printed for a human or agent to run -- never run here.

Every candidate for each runtime is launched, not just the first one on PATH, because the first
one is often a Windows Store alias that answers with a Store message instead of a version.

Usage:
    python tools/inventory-runtimes.py inventory
    python tools/inventory-runtimes.py plan [--auth-file F] [--json]

F holds the user's WHOLE task text, never a sentence picked out of it: a denial anywhere in that text voids
every authorization, which only works when the parser sees all of it.

Exit code: 0 always (reporting is the job); 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

# Plan 8 T6 fix round 1 (review M2): the tested range is 3.10-3.13 (user ruling 2026-10-05), so 3.10 is the floor
# the inventory reports and the authorization parser accepts. The shared harness and engine require 3.10+.
PYTHON_FLOOR = (3, 10)
RUNTIMES = ("python", "git", "node")
LAUNCH_TIMEOUT = 10

AUTH_MARKER = "Host-software installs:"
LOOSE_MARKER_RE = re.compile(r"host[\W_]*software[\W_]*installs", re.I)
ITEM_RE =re.compile(r"(python|git|node)=([A-Za-z0-9][A-Za-z0-9.+-]*)==([0-9]+(?:\.[0-9]+){1,3})")
VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")
# The only winget ids each runtime may be installed as (exact, case-sensitive: winget --exact matches case). Without
# this, "node=Contoso.Backdoor==1.0" was a valid item and the plan printed that id as the node install.
# Python's id carries its minor version; the minor must meet PYTHON_FLOOR and the version must be that minor's.
ALLOWED_IDS = {"python": ("Python.Python.3.<minor>",), "git": ("Git.Git",),
               "node": ("OpenJS.NodeJS", "OpenJS.NodeJS.LTS")}
_PYTHON_ID_RE = re.compile(r"Python\.Python\.3\.([0-9]+)")

# Worst first: something that exists but is wrong must be reported before anything is installed over it.
_SEVERITY = ["too-old", "broken", "timeout", "store-alias", "missing"]

# The exact versions the toolkit was tested with (Plan 8 reproducibility ruling): the same pins README.md and
# docs/getting-started.md give, each with the installer's SHA-256 (MEASURED 2026-10-05: downloaded and hashed; equal
# to the hash in winget's manifest, which winget itself checks). Git moved to 2.55.0.5 on 2026-10-13 (MEASURED the same
# way, and equal to the digest GitHub publishes for the release asset; docs/installing-tools.md *Git for Windows*).
# scripts/thirdparty-audit.py holds these to the docs.
_VERIFY = "before running it, check that `Get-FileHash <file> -Algorithm SHA256` prints "
# Steps for the USER to follow: a download plus its hash, never a package-manager command (an agent reading these must
# not find a command to run; tests/test_no_host_installs_in_new_files.py holds this file to that).
MANUAL_STEPS = {
    "python": "Install Python 3.12.10 (inside the tested 3.10-3.13 range): download python-3.12.10-amd64.exe from "
              "https://www.python.org/ftp/python/3.12.10/ -- " + _VERIFY
              + "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb -- run it and tick "
              "'Add python.exe to PATH'. Then open a new terminal.",
    "git": "Install Git for Windows 2.55.0.5 (it includes Git Bash): download Git-2.55.0.5-64-bit.exe from "
           "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/ -- " + _VERIFY
           + "d065a4e23c3d9a6b5073d609b5be0830227ec3ca053c083ba385061ddfaf94c6 -- and run it. Then open a new terminal.",
    "node": "Install Node.js 24.19.0: download node-v24.19.0-x64.msi from https://nodejs.org/dist/v24.19.0/ -- "
            + _VERIFY + "f0f66c2a80c08a30a5ab5179ee9ea9e45f9b46289436a8cc87ff833b852db351 -- and run it. "
            "Then open a new terminal.",
}


def launch(argv, timeout=LAUNCH_TIMEOUT):
    """Run `argv` (always a --version launch). Returns (returncode | 'timeout' | 'missing', text).

    This is the tool's ONLY subprocess use.
    """
    # python.org's Python Install Manager can INSTALL Python from `py -3` / the python.exe alias when none
    # exists; this switch makes a version probe unable to do that.
    env = dict(os.environ, PYTHON_MANAGER_AUTOMATIC_INSTALL="false")
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL, errors="replace")
    except subprocess.TimeoutExpired:
        return "timeout", ""
    except OSError:
        return "missing", ""
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _parse_version(text):
    m = VERSION_RE.search(text)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def classify(argv, runtime, timeout=LAUNCH_TIMEOUT):
    """Launch one candidate and say what it is. Never raises."""
    row = {"argv": list(argv), "path": argv[0] if argv else "", "status": "broken", "version": None, "output": ""}
    rc, text = launch(argv, timeout=timeout)
    row["output"] = text.strip()[:300]
    if rc == "timeout":
        row["status"] = "timeout"
        return row
    if rc == "missing":
        # A file that exists but will not launch (zero-byte python.exe) is broken, not absent.
        row["status"] = "broken" if os.path.isfile(str(row["path"])) else "missing"
        return row
    version = _parse_version(text)
    in_windowsapps = "windowsapps" in str(row["path"]).lower()
    if "Microsoft Store" in text or "was not found" in text or (in_windowsapps and version is None):
        row["status"] = "store-alias"
        return row
    if rc != 0 or version is None:
        return row  # broken
    row["version"] = ".".join(str(p) for p in version)
    row["status"] = "too-old" if runtime == "python" and version[:2] < PYTHON_FLOOR else "ok"
    return row


def _path_matches(name):
    """Every executable called `name` on PATH, in PATH order, de-duplicated."""
    exts = [""]
    if sys.platform == "win32":
        exts = [e.lower() for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if e]
    found, seen = [], set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        for ext in exts:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p) and (sys.platform == "win32" or os.access(p, os.X_OK)):
                key = os.path.normcase(os.path.realpath(p))
                if key not in seen:
                    seen.add(key)
                    found.append(p)
    return found


def discover():
    """Candidate argv lists per runtime, each already ending in the version flag."""
    cands = {"python": [], "git": [], "node": []}
    for name in ("python", "python3"):
        cands["python"] += [[p, "--version"] for p in _path_matches(name)]
    cands["python"] += [[p, "-3", "--version"] for p in _path_matches("py")]
    cands["git"] = [[p, "--version"] for p in _path_matches("git")]
    cands["node"] = [[p, "--version"] for p in _path_matches("node")]
    return cands


def inventory(candidates=None, timeout=LAUNCH_TIMEOUT):
    """{runtime: {"status", "best", "candidates": [row, ...]}} -- every candidate is launched and reported."""
    if candidates is None:
        candidates = discover()
    result = {}
    for runtime in RUNTIMES:
        rows = [classify(argv, runtime, timeout=timeout) for argv in candidates.get(runtime, [])]
        best = next((r for r in rows if r["status"] == "ok"), None)
        if best is not None:
            status = "ok"
        else:
            present = {r["status"] for r in rows}
            status = next((s for s in _SEVERITY if s in present), "missing")
        result[runtime] = {"status": status, "best": best, "candidates": rows}
    return result


def id_problem(runtime, pkg_id, version):
    """None when `pkg_id` at `version` is an allow-listed install of `runtime`; else why it is not."""
    if runtime == "python":
        m = _PYTHON_ID_RE.fullmatch(pkg_id)
        if not m or int(m.group(1)) < PYTHON_FLOOR[1]:
            return "%s is not on the allowlist for python (Python.Python.3.<minor>, 3.%d or newer)" % (
                pkg_id, PYTHON_FLOOR[1])
        if version.split(".")[:2] != ["3", m.group(1)]:
            return "version %s is not a version of %s, so it is not on the allowlist" % (version, pkg_id)
        return None
    if pkg_id not in ALLOWED_IDS.get(runtime, ()):
        return "%s is not on the allowlist for %s (%s)" % (pkg_id, runtime, ", ".join(ALLOWED_IDS.get(runtime, ())))
    return None


def _classify_occurrence(rest):
    """Classify the text after ONE marker as exactly one of: auth, template, denial, void.

    auth     -- uppercase AUTHORIZED, a single space, valid items joined by ", ", then a sentence end.
    template -- the prompt's unfilled form (every item is a <placeholder>); ignored.
    denial   -- exactly "NOT AUTHORIZED" plus a sentence end.
    void     -- anything else. Unknown means no.
    Returns (kind, authorized, note).
    """
    m = re.match(r" ([^\n]*?)\.(?=\s|$)", rest)
    if not m:
        return "void", {}, "marker with no sentence end: " + rest[:60].strip()
    body = m.group(1)
    if body == "NOT AUTHORIZED":
        return "denial", {}, None
    if body.startswith("AUTHORIZED "):
        items = body[len("AUTHORIZED "):].split(", ")
        if all("<" in i or ">" in i for i in items):
            return "template", {}, None
        authorized = {}
        for item in items:
            im = ITEM_RE.fullmatch(item)
            if not im or im.group(1) in authorized:
                return "void", {}, "bad or repeated item: " + item
            problem = id_problem(im.group(1), im.group(2), im.group(3))
            if problem:
                return "void", {}, problem
            authorized[im.group(1)] = (im.group(2), im.group(3))
        return "auth", authorized, None
    return "void", {}, "unrecognised wording: " + body  # unclassified-is-void


def parse_authorization(text):
    """Fail-closed read of every 'Host-software installs:' marker (case-insensitive). Returns (authorized, rejected).

    authorized: {runtime: (id, version)}, non-empty only when EVERY occurrence is a clean AUTHORIZED list
    or the unfilled template, and the clean ones agree. Any other occurrence, a denial included, voids it.
    """
    # Normalise first: drop zero-width / format characters (category Cf: ZWSP, soft hyphen, word joiner ...)
    # and collapse every whitespace run (newline, NBSP ...) to one space, so a damaged marker cannot hide.
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = re.sub(r"\s+", " ", text)
    # Any loose spelling of the marker that is not the exact marker voids everything: unknown means no.
    for m in LOOSE_MARKER_RE.finditer(text):
        if text[m.start():m.start() + len(AUTH_MARKER)].lower() != AUTH_MARKER.lower():
            return {}, ["damaged marker: " + text[m.start():m.start() + 40]]
    auths = []
    for m in re.finditer(re.escape(AUTH_MARKER), text, re.I):
        kind, authorized, note = _classify_occurrence(text[m.end():])
        if kind == "void":
            return {}, [note]
        if kind == "denial":
            return {}, []
        if kind == "auth":
            auths.append(authorized)
    if not auths:
        return {}, []
    if any(a != auths[0] for a in auths):
        return {}, ["conflicting Host-software installs sentences"]
    return auths[0], []


def install_command(pkg_id, version):
    """The exact, non-upgrading command shape. Printed only; this module never runs it."""
    return ["winget", "install", "--id", pkg_id, "--exact", "--version", version, "--no-upgrade",
            "--accept-package-agreements", "--accept-source-agreements"]


def _winget_present():
    path = shutil.which("winget")
    if not path:
        return False
    rc, _ = launch([path, "--version"])
    return rc == 0


def plan(inv, authorization=None, platform=None, winget_ok=None):
    """{runtime: {"action", "status", "command", "steps"}}; action is none|report|manual|install-authorized."""
    authorization = authorization or {}
    platform = sys.platform if platform is None else platform
    out = {}
    for runtime in RUNTIMES:
        status = inv[runtime]["status"]
        row = {"action": "manual", "status": status, "command": None, "steps": MANUAL_STEPS[runtime]}
        if status == "ok":
            row.update(action="none", steps="")
        elif status in ("too-old", "broken", "timeout"):
            row.update(action="report", steps=(
                "It is present but %s. Do NOT upgrade, repair, reinstall or remove it yourself; tell the user "
                "and wait. Manual steps: %s" % (status, MANUAL_STEPS[runtime])))
        elif runtime in authorization and platform == "win32" and not id_problem(runtime, *authorization[runtime]):
            # id_problem again HERE, where the command is built: a caller that skips the parser inherits the check.
            if winget_ok is None:
                winget_ok = _winget_present()
            if winget_ok:
                pkg_id, version = authorization[runtime]
                row.update(action="install-authorized", command=" ".join(install_command(pkg_id, version)),
                           steps="The user authorized exactly this id and version. Run only this command, unchanged.")
        out[runtime] = row
    return out


def _format_plan(inv, plans, rejected):
    lines = []
    for runtime in RUNTIMES:
        p, i = plans[runtime], inv[runtime]
        best = i["best"]
        lines.append("%-6s %-12s action=%s%s" % (runtime, p["status"], p["action"],
                                                  "  (%s)" % best["version"] if best else ""))
        for r in i["candidates"]:
            lines.append("         candidate %s -> %s%s" % (" ".join(r["argv"]), r["status"],
                                                            " " + r["version"] if r["version"] else ""))
        if p["command"]:
            lines.append("         command: " + p["command"])
        if p["steps"]:
            lines.append("         " + p["steps"])
    for item in rejected:
        lines.append("authorization item rejected (authorizes nothing): " + item)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Runtime inventory and install plan. Never installs anything.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    pl = sub.add_parser("plan")
    pl.add_argument("--json", action="store_true")
    pl.add_argument("--auth-file", help="a file holding the user's WHOLE task text (never one chosen sentence)")
    args = ap.parse_args(argv)
    inv = inventory()
    if args.cmd == "inventory":
        print(json.dumps(inv, indent=2))
        return 0
    text = ""
    if args.auth_file:
        try:
            with open(args.auth_file, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            ap.error("cannot read --auth-file: %s" % exc)
    authorization, rejected = parse_authorization(text)
    plans = plan(inv, authorization)
    if args.json:
        print(json.dumps({"inventory": inv, "plan": plans, "rejected": rejected}, indent=2))
    else:
        print(_format_plan(inv, plans, rejected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
