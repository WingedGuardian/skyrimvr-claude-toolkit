"""inventory-runtimes -- which of Python, Git and Node actually START here, and what to do about the rest.

Looks first, reports, and prints a plan. It NEVER installs, upgrades, repairs or removes anything:
its single use of `subprocess` is the `--version` launch in `launch()`. The only install command
it can ever print is the one the user's own task authorized, by id and exact version, and it is
printed for a human or agent to run -- never run here.

Every candidate for each runtime is launched, not just the first one on PATH, because the first
one is often a Windows Store alias that answers with a Store message instead of a version.

Usage:
    python tools/inventory-runtimes.py inventory
    python tools/inventory-runtimes.py plan [--auth-text "<sentence>" | --auth-file F] [--json]

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

PYTHON_FLOOR = (3, 8)  # tools/skyguard/run.py: version_info[:2] < (3, 8)
RUNTIMES = ("python", "git", "node")
LAUNCH_TIMEOUT = 10

AUTH_MARKER = "Host-software installs:"
LOOSE_MARKER_RE = re.compile(r"host[\W_]*software[\W_]*installs", re.I)
ITEM_RE =re.compile(r"(python|git|node)=([A-Za-z0-9][A-Za-z0-9.+-]*)==([0-9]+(?:\.[0-9]+){1,3})")
VERSION_RE = re.compile(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")

# Worst first: something that exists but is wrong must be reported before anything is installed over it.
_SEVERITY = ["too-old", "broken", "timeout", "store-alias", "missing"]

MANUAL_STEPS = {
    "python": "Download Python 3.8 or newer from https://www.python.org/downloads/ and install it, "
              "ticking 'Add python.exe to PATH'. Then open a new terminal.",
    "git": "Download Git for Windows from https://git-scm.com/download/win and install it (it includes Git Bash). "
           "Then open a new terminal.",
    "node": "Download the LTS release from https://nodejs.org/ and install it. Then open a new terminal.",
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
        elif runtime in authorization and platform == "win32":
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
    grp = pl.add_mutually_exclusive_group()
    grp.add_argument("--auth-text")
    grp.add_argument("--auth-file")
    args = ap.parse_args(argv)
    inv = inventory()
    if args.cmd == "inventory":
        print(json.dumps(inv, indent=2))
        return 0
    text = args.auth_text or ""
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
