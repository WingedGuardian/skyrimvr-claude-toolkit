#!/usr/bin/env python3
"""Adapter conformance for ADAPTING.md: payload cases, an engine fault matrix and a structure check,
run against a SCRATCH copy of the install, never the real one. Stdlib only, Python >= 3.8."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "tools" / "adapting" / "adapter_template.py"
BEGIN, END = "# SLOTS-BEGIN", "# SLOTS-END"
REQUIRED = ("decoy-plugin-write", "benign-write", "empty-payload", "garbage-payload")
SHELL_PAIR = ("decoy-plugin-shell", "benign-shell")
# A case id must carry the expectation its name promises, or a mislabelled case would pass a bad adapter.
EXPECTED = {"decoy-plugin-write": "block", "decoy-plugin-shell": "block", "empty-payload": "block",
            "garbage-payload": "block", "benign-write": "allow", "benign-shell": "allow"}
PROBE_TIMEOUT_S = float(os.environ.get("SKYGUARD_CONF_TIMEOUT_S", "15"))
# The engine's plugin-write refusal names "plugin/archive" (write_rules.DENY_PLUGIN, pinned in Task 0).
VISIBLE = re.compile(r"(?i)plugin|archive")
ALLOW_V1 = ('print("SKYGUARD-OK"); print(\'{"v":1,"decision":"allow","reason":"","context":"",'
            '"status":"evaluated","code":"allow"}\')')
FAULTS = {
    "engine-crash": "raise SystemExit(1)\n",
    "engine-silent": "pass\n",
    "engine-garbage": "print('hello')\n",
    "engine-marker-garbage": "print('SKYGUARD-OK'); print('not json')\n",
    "engine-wrong-version": "print('SKYGUARD-OK'); print('{\"v\":2,\"decision\":\"allow\"}')\n",
    "engine-allow-but-exit-1": ALLOW_V1 + "; raise SystemExit(1)\n",
    "engine-hang": "import time; time.sleep(60)\n",
    "engine-missing": None,
}


class Row(NamedTuple):
    id: str
    ok: bool
    why: str


# ---- template mechanics ---------------------------------------------------------------------------
def _split(text: str) -> Tuple[str, str, str]:
    text = text.replace("\r\n", "\n")
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
        raise ValueError("expected exactly one SLOTS-BEGIN before one SLOTS-END")
    i, j = text.index(BEGIN), text.index(END) + len(END)
    return text[:i], text[i:j], text[j:]


def compose(template_text: str, slots_text: str) -> str:
    head, _, tail = _split(template_text)
    _, slots, _ = _split(slots_text) if (BEGIN in slots_text) else ("", slots_text, "")
    return head + slots + tail


def outside_hash(text: str) -> str:
    head, _, tail = _split(text)
    return hashlib.sha256((head + tail).encode("utf-8")).hexdigest()


def make_install(adapter_dir: Path, dest: Path, repo: Path = REPO) -> Path:
    dest = Path(dest)
    shutil.copytree(Path(repo) / "tools" / "skyguard", dest / "tools" / "skyguard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(adapter_dir, dest / "tools" / "adapters" / Path(adapter_dir).name,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".claude").mkdir(exist_ok=True)
    return dest


# ---- shapes ---------------------------------------------------------------------------------------
def _profile_problem(p: dict) -> Optional[str]:
    try:
        b, a = p["block"], p["allow"]
        for v in (b["exit"], a["exit"]):
            if not isinstance(v, int) or isinstance(v, bool):
                return "exit codes must be integers"
        if not (b.get("stdout_regex") or b.get("stderr_regex")) and b["exit"] == a["exit"]:
            return "block cannot be told from allow: give a different exit code or a regex"
    except (KeyError, TypeError, AttributeError):
        return "profile needs block.exit and allow.exit"
    return None


def is_block(profile: dict, out: str, code: Optional[int], err: str) -> bool:
    b = profile["block"]
    if code != b["exit"]:
        return False
    # Windows writes stderr in text mode, so a reason can arrive with CRLF line endings: compare as LF.
    out, err = out.replace("\r\n", "\n"), err.replace("\r\n", "\n")
    for key, text in (("stdout_regex", out), ("stderr_regex", err)):
        if b.get(key) and not re.search(b[key], text):
            return False
    return True


def is_allow(profile: dict, out: str, code: Optional[int], err: str) -> bool:
    return code == profile["allow"]["exit"] and not is_block(profile, out, code, err)


def _payload_bytes(case: dict, root: Path) -> bytes:
    if "payload_hex" in case:
        return bytes.fromhex(case["payload_hex"])
    p = case.get("payload", "")
    text = p if isinstance(p, str) else json.dumps(p)
    # {ROOT} sits inside JSON strings, so a path with a backslash or quote must be escaped, not pasted.
    if not isinstance(p, str):
        return text.replace("{ROOT}", json.dumps(root.as_posix())[1:-1]).encode("utf-8")
    return text.replace("{ROOT}", root.as_posix()).encode("utf-8")


def _run(argv: list, data: bytes, env: dict) -> Tuple[str, Optional[int], str]:
    try:
        cp = subprocess.run(argv, input=data, capture_output=True, env=env, timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "", None, "TIMEOUT: the adapter did not answer within {}s".format(PROBE_TIMEOUT_S)
    except OSError as e:
        return "", None, "cannot start the adapter: {}".format(e)
    return cp.stdout.decode("utf-8", "replace"), cp.returncode, cp.stderr.decode("utf-8", "replace")


def _case_problems(by_id: dict) -> List[Row]:
    rows: List[Row] = []
    for rid in REQUIRED:
        if rid not in by_id:
            rows.append(Row("case:" + rid, False, "required case missing"))
    if (SHELL_PAIR[0] in by_id) != (SHELL_PAIR[1] in by_id):
        rows.append(Row("case:shell-pair", False, "give both decoy-plugin-shell and benign-shell, or neither"))
    for cid, want in EXPECTED.items():
        if cid in by_id and by_id[cid].get("expect") != want:
            rows.append(Row("case:" + cid, False, "this case must expect " + want))
    return rows


def run_conformance(adapter_dir, profile: dict, cases: dict, command: Optional[str] = None,
                    port: bool = False, repo: Path = REPO) -> List[Row]:
    rows: List[Row] = []
    adapter_dir = Path(adapter_dir)
    prob = _profile_problem(profile)
    if prob:
        return [Row("profile", False, prob)]
    by_id = {c.get("id"): c for c in cases.get("cases", []) if isinstance(c, dict)}
    rows = _case_problems(by_id)
    if rows:
        return rows
    entry = adapter_dir / "adapter.py"
    if port:
        print("UNVERIFIED STRUCTURE: this adapter is a port; the template-identical check was skipped", file=sys.stderr)
    elif not entry.is_file():
        rows.append(Row("template-identical", False, "no adapter.py in the adapter folder (use --port for a non-Python adapter)"))
    else:
        try:
            same = outside_hash(entry.read_text(encoding="utf-8")) == outside_hash(TEMPLATE.read_text(encoding="utf-8"))
            note = "" if same else "ADAPTER DIVERGES FROM THE TEMPLATE outside the SLOTS block: adapters only translate"
        except ValueError as e:
            same, note = False, "the SLOTS markers are damaged: {}".format(e)
        rows.append(Row("template-identical", same, "machinery identical to the shipped template" if same else note))

    with tempfile.TemporaryDirectory() as td:
        root = make_install(adapter_dir, Path(td) / "install", repo)
        sub = {"{DIR}": str(root / "tools" / "adapters" / adapter_dir.name), "{PY}": sys.executable}
        cmd = command or "{PY} {DIR}/adapter.py"
        for k, v in sub.items():
            cmd = cmd.replace(k, v)
        argv = shlex.split(cmd, posix=(os.name != "nt"))
        if os.name == "nt":
            argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
        env = dict(os.environ)
        env.pop("SKYGUARD_ADAPTER_TIMEOUT_S", None)
        hung = False
        for cid, case in by_id.items():
            out, code, err = _run(argv, _payload_bytes(case, root), env)
            hung = hung or code is None and err.startswith("TIMEOUT")
            want = case.get("expect")
            if want == "block":
                ok = blocked = is_block(profile, out, code, err)
                if ok and cid.startswith("decoy-"):
                    ok = bool(VISIBLE.search(out + err))
                if ok:
                    why = "blocked, reason visible"
                elif blocked:
                    why = "blocked, but the engine's plugin/archive reason did not reach the agent (stdout {!r}, stderr {!r})".format(
                        out[:80], err[:80])
                else:
                    why = "NOT BLOCKED (exit {}, stdout {!r}, stderr {!r})".format(code, out[:80], err[:80])
            elif want == "allow":
                ok = is_allow(profile, out, code, err)
                why = "allowed" if ok else "NOT ALLOWED (exit {}, stdout {!r}, stderr {!r})".format(code, out[:80], err[:80])
            else:
                ok, why = False, "expect must be block or allow"
            rows.append(Row("case:" + cid, ok, why))
        if hung:
            rows.append(Row("faults", False, "skipped: the adapter hung on a plain payload, so the fault matrix cannot mean anything"))
            return rows
        benign = _payload_bytes(by_id["benign-write"], root)
        run_py = root / "tools" / "skyguard" / "run.py"
        original = run_py.read_text(encoding="utf-8")
        for name, source in FAULTS.items():
            if source is None:
                run_py.unlink()
            else:
                run_py.write_text(source, encoding="utf-8", newline="\n")
            fenv = dict(env, SKYGUARD_ADAPTER_TIMEOUT_S="2")
            out, code, err = _run(argv, benign, fenv)
            ok = is_block(profile, out, code, err) and "GUARD INERT" in (out + err)
            rows.append(Row("fault:" + name, ok, "failed closed, cause visible" if ok else
                            "FAILED OPEN or hid the cause (exit {}, stdout {!r}, stderr {!r})".format(code, out[:80], err[:80])))
            run_py.write_text(original, encoding="utf-8", newline="\n")
    return rows


def summary(rows: List[Row]) -> str:
    bad = [r for r in rows if not r.ok]
    return "CONFORMANCE PASS ({}/{})".format(len(rows), len(rows)) if not bad else \
        "CONFORMANCE FAIL ({} of {})".format(len(bad), len(rows))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--adapter-dir", required=True)
    r.add_argument("--profile", required=True)
    r.add_argument("--cases", required=True)
    r.add_argument("--command")
    r.add_argument("--port", action="store_true")
    a = ap.parse_args(argv)
    rows = run_conformance(a.adapter_dir, json.loads(Path(a.profile).read_text(encoding="utf-8")),
                           json.loads(Path(a.cases).read_text(encoding="utf-8")), a.command, a.port)
    for row in rows:
        print("{} {}: {}".format("PASS" if row.ok else "FAIL", row.id, row.why))
    print(summary(rows))
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
