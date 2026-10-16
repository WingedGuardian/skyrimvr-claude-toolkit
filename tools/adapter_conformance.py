#!/usr/bin/env python3
"""Adapter conformance for ADAPTING.md: payload cases, an engine fault matrix and a structure check,
run against a SCRATCH copy of the install, never the real one. Stdlib only, Python >= 3.8."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
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
SHELL_NOTE = "SHELL UNGUARDED (profile lists no shell tool)"
# The profile names every tool of the agent that writes a file or runs a command; each needs its own decoy case
# (id decoy-plugin-write or decoy-plugin-write:<anything>, with "tool": <name>; the same for shell).
TOOL_LISTS = ("write_tools", "shell_tools")
DECOY = {"write_tools": "decoy-plugin-write", "shell_tools": "decoy-plugin-shell"}
UNMAPPED_NOTE = "UNMAPPED TOOLS ARE NOT GUARDED"
# The fake engines write BYTES: print() on Windows ends a line with CRLF, which would break the marker check
# for a reason that is not the fault being tested.
_W = "import sys\nw = sys.stdout.buffer.write\n"
ALLOW_V1 = _W + 'w(b\'SKYGUARD-OK\\n{"v":1,"decision":"allow","reason":"","context":"","status":"evaluated","code":"allow"}\\n\')'
# Faults that make the engine BREAK: the adapter must fail closed with GUARD INERT visible.
FAULTS = {
    "engine-crash": "raise SystemExit(1)\n",
    "engine-silent": "pass\n",
    "engine-garbage": _W + "w(b'hello\\n')\n",
    "engine-marker-garbage": _W + "w(b'SKYGUARD-OK\\nnot json\\n')\n",
    "engine-wrong-version": _W + "w(b'SKYGUARD-OK\\n{\"v\":2,\"decision\":\"allow\"}\\n')\n",
    "engine-allow-but-exit-1": ALLOW_V1 + "\nraise SystemExit(1)\n",
    # Valid verdict JSON with no marker, behind 12 filler bytes so that a missing marker CHECK still parses it.
    "engine-no-marker": _W + "w(b'            {\"v\":1,\"decision\":\"allow\",\"reason\":\"\",\"context\":\"\"}\\n')\n",
    "engine-unknown-decision": _W + "w(b'SKYGUARD-OK\\n{\"v\":1,\"decision\":\"maybe\",\"reason\":\"\",\"context\":\"\"}\\n')\n",
    "engine-hang": "import time; time.sleep(60)\n",
    "engine-missing": None,
}
for _name, _changes in (
        ("bool-version", {"v": True}), ("float-version", {"v": 1.0}),
        ("reason-type", {"reason": []}), ("context-type", {"context": {}}),
        ("status-type", {"status": []}), ("status-unknown", {"status": "unknown"}),
        ("code-type", {"code": None}), ("incomplete-verdict", None)):
    _verdict = dict(v=1, decision="allow", reason="", context="", status="evaluated", code="allow")
    if _changes is None:
        _verdict = {"v": 1, "decision": "allow"}
    else:
        _verdict.update(_changes)
    FAULTS["engine-" + _name] = _W + "w(" + repr(("SKYGUARD-OK\n" + json.dumps(_verdict) + "\n").encode("ascii")) + ")\n"
# Faults that make the engine DECIDE something the payload does not suggest: the adapter must obey the verdict,
# not its own opinion of the payload. Both are valid, well-formed engine answers.
VERDICT_FAULTS = ("engine-deny-nonce", "engine-allow-valid")


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
    if (Path(repo) / "tools" / "modding_harness").is_dir():    # the engine imports it
        shutil.copytree(Path(repo) / "tools" / "modding_harness", dest / "tools" / "modding_harness",
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
        if "has_shell" in p:
            return "has_shell is replaced by shell_tools (an empty list means the agent has no shell tool)"
        for key in TOOL_LISTS:
            tools = p.get(key)
            if not (isinstance(tools, list) and all(isinstance(t, str) and t.strip() for t in tools)
                    and len(set(tools)) == len(tools)):
                return "{} must be a list of distinct tool names, as your agent names them".format(key)
        if not p["write_tools"]:
            return "write_tools must list every tool that writes or edits a file (at least one)"
        if set(p["write_tools"]) & set(p["shell_tools"]):
            return "a tool cannot be in both write_tools and shell_tools"
        for key in ("stdout_regex", "stderr_regex"):
            if b.get(key):
                re.compile(b[key])
    except re.error as e:
        return "block regex does not compile: {}".format(e)
    except (KeyError, TypeError, AttributeError):
        return "profile needs block.exit and allow.exit"
    return None


def is_block(profile: dict, out: str, code: Optional[int], err: str) -> bool:
    """A profile regex is matched with re.fullmatch (DOTALL) against the WHOLE stream, stripped, with CRLF folded
    to LF. So it must describe the decision (e.g. '\\{"decision": "block".*'), not mention a word that an allow's
    free text could contain, and a block shape with junk in front of it is not a block."""
    b = profile["block"]
    if code != b["exit"]:
        return False
    out, err = out.replace("\r\n", "\n").strip(), err.replace("\r\n", "\n").strip()
    for key, text in (("stdout_regex", out), ("stderr_regex", err)):
        if b.get(key) and not re.fullmatch(b[key], text, re.DOTALL):
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
    kw = {} if os.name == "nt" else {"start_new_session": True}
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **kw)
    except OSError as e:
        return "", None, "cannot start the adapter: {}".format(e)
    try:
        so, se = p.communicate(data, timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=10)
        except Exception:
            pass
        return "", None, "TIMEOUT: the adapter did not answer within {}s".format(PROBE_TIMEOUT_S)
    return so.decode("utf-8", "replace"), p.returncode, se.decode("utf-8", "replace")


def _kill_tree(p: "subprocess.Popen") -> None:
    """Kill the adapter AND everything it started (its engine), by the PID this tool spawned: never by image
    name, and never only the top process, which would orphan the engine and keep the pipes open."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=30)
        else:
            import signal
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass


def _case_problems(by_id: dict, profile: dict) -> List[Row]:
    rows: List[Row] = []
    has_shell = bool(profile["shell_tools"])
    for rid in REQUIRED + (SHELL_PAIR if has_shell else ()):
        if rid not in by_id:
            rows.append(Row("case:" + rid, False, "required case missing"))
    if (SHELL_PAIR[0] in by_id) != (SHELL_PAIR[1] in by_id):
        rows.append(Row("case:shell-pair", False, "give both decoy-plugin-shell and benign-shell, or neither"))
    for cid, want in EXPECTED.items():
        if cid in by_id and by_id[cid].get("expect") != want:
            rows.append(Row("case:" + cid, False, "this case must expect " + want))
    # One decoy per listed tool: a write tool the slots leave unmapped is sent as `other` and ALLOWED unjudged,
    # which only a decoy case through that very tool can show.
    for key, base in DECOY.items():
        covered = set()
        for cid, case in by_id.items():
            if not (isinstance(cid, str) and (cid == base or cid.startswith(base + ":"))):
                continue
            tool = case.get("tool")
            if case.get("expect") != "block":
                rows.append(Row("case:" + cid, False, "a decoy case must expect block"))
            if not isinstance(tool, str) or not tool:
                rows.append(Row("case:" + cid, False, 'a decoy case must name its tool ("tool": one of the profile\'s '
                                "{})".format(key)))
            elif tool not in profile[key]:
                rows.append(Row("case:" + cid, False, "tool {!r} is not listed in the profile's {}".format(tool, key)))
            else:
                covered.add(tool)
        for tool in profile[key]:
            if tool not in covered:
                rows.append(Row("case:{}:{}".format(base, tool), False,
                                'no decoy case for {!r}: every tool in {} needs its own (id "{}:<name>", '
                                '"tool": {!r}, expect block)'.format(tool, key, base, tool)))
    return rows


def unmapped_note(profile: dict) -> str:
    return ("{}: conformance probed only write_tools {} and shell_tools {}. Any other tool is sent as kind other "
            "and allowed unjudged, so every tool that writes a file or runs a command must be in those lists"
            .format(UNMAPPED_NOTE, profile.get("write_tools"), profile.get("shell_tools")))


def build_argv(command: Optional[list], adapter_dir: Path) -> list:
    """The adapter's argv as a LIST, placeholders substituted per element after the list exists: a path is one
    argv word whatever it contains. Never a string that something must split."""
    if command is None:
        return [sys.executable, str(Path(adapter_dir) / "adapter.py")]
    sub = {"{DIR}": str(adapter_dir), "{PY}": sys.executable}
    out = []
    for word in command:
        for k, v in sub.items():
            word = word.replace(k, v)
        out.append(word)
    return out


def run_conformance(adapter_dir, profile: dict, cases: dict, command: Optional[list] = None,
                    port: bool = False, repo: Path = REPO) -> List[Row]:
    rows: List[Row] = []
    adapter_dir = Path(adapter_dir)
    prob = _profile_problem(profile)
    if prob:
        return [Row("profile", False, prob)]
    listed = [c for c in cases.get("cases", []) if isinstance(c, dict)]
    by_id = {c.get("id"): c for c in listed}
    if command is not None and not port:
        # The identity check hashes <adapter-dir>/adapter.py. A --command may run some OTHER file, so it would
        # certify one file and run another. Only a declared port (identity skipped, loudly) may bring a command.
        return [Row("command", False, "--command needs --port: the template-identity check hashes adapter.py, "
                    "which a custom command does not necessarily run")]
    if command is not None and not (isinstance(command, list) and command
                                    and all(isinstance(a, str) and a for a in command)):
        # A command STRING would have to be split, and splitting cuts a path with a space (C:/Program Files,
        # C:/Users/John Smith) or an apostrophe into several argv words: a false FAILED OPEN on every row.
        return [Row("command", False, 'the command must be a JSON list of argv strings, e.g. ["{PY}", "{DIR}/run.py"]; '
                    "a command string is never split")]
    ids = [c.get("id") for c in listed]
    rows = [Row("case:{}".format(i), False, "duplicate case id: one case would silently shadow the other")
            for i in sorted({i for i in ids if ids.count(i) > 1}, key=str)]
    rows += _case_problems(by_id, profile)
    if rows:
        return rows
    if not profile["shell_tools"]:
        rows.append(Row("shell-unguarded", True, SHELL_NOTE))
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
        argv = build_argv(command, root / "tools" / "adapters" / adapter_dir.name)
        env = dict(os.environ)
        env.pop("SKYGUARD_ADAPTER_TIMEOUT_S", None)
        hung = False
        for cid, case in by_id.items():
            out, code, err = _run(argv, _payload_bytes(case, root), env)
            hung = hung or code is None and err.startswith("TIMEOUT")
            want = case.get("expect")
            if want == "block":
                ok = is_block(profile, out, code, err)
                why = "blocked" if ok else "NOT BLOCKED (exit {}, stdout {!r}, stderr {!r})".format(code, out[:80], err[:80])
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
        # Verdict fidelity: the engine answers with a valid verdict the payload does not suggest. An adapter
        # that decides from the payload, or drops the reason, is caught here and nowhere else.
        nonce = "NONCE" + secrets.token_hex(8)
        deny = _W + ('w(b\'SKYGUARD-OK\\n{"v": 1, "decision": "deny", "reason": "%s", "context": "", '
                     '"status": "evaluated", "code": "x"}\\n\')\n' % nonce)
        for name, source, payload in (("engine-deny-nonce", deny, benign),
                                      ("engine-allow-valid", ALLOW_V1 + "\n", _payload_bytes(by_id["decoy-plugin-write"], root))):
            run_py.write_text(source, encoding="utf-8", newline="\n")
            out, code, err = _run(argv, payload, env)
            if name == "engine-deny-nonce":
                blocked = is_block(profile, out, code, err)
                ok = blocked and nonce in (out + err)
                why = ("blocked and the engine's own reason reached the agent" if ok else
                       "blocked, but the engine's reason (the nonce) never reached the agent" if blocked else
                       "NOT BLOCKED though the engine denied (exit {}, stdout {!r}, stderr {!r})".format(code, out[:80], err[:80]))
            else:
                ok = is_allow(profile, out, code, err)
                why = ("allowed what the engine allowed" if ok else
                       "did not allow though the engine allowed: the adapter decides for itself (exit {}, stdout {!r}, stderr {!r})"
                       .format(code, out[:80], err[:80]))
            rows.append(Row("fault:" + name, ok, why))
        run_py.write_text(original, encoding="utf-8", newline="\n")
    return rows


# ---- the capability report (ADAPTING.md section 3) ---------------------------------------------------
REPORT_TEMPLATE = REPO / "tools" / "adapting" / "capability-report.template.json"
# Every leaf the validator requires, as dotted paths. The template's keys must equal this tuple (a test says so).
LEAVES = (
    "v",
    "agent.name", "agent.version", "agent.surface",
    "instruction_file.name", "instruction_file.limit_bytes", "instruction_file.truncation_behaviour",
    "skills_dir.path",
    "pre_tool_hook.exists", "pre_tool_hook.fires_on_this_surface", "pre_tool_hook.can_block",
    "pre_tool_hook.block_mechanism", "pre_tool_hook.can_ask", "pre_tool_hook.failed_hook_behaviour",
    "pre_tool_hook.hook_timeout_s", "pre_tool_hook.every_write_and_shell_tool_mapped",
    "session_start.exists", "session_start.injects_context",
    "payload_capture.method", "payload_capture.files",
    "proof.conformance_passed", "proof.live_canary_blocked", "proof.live_shell_canary_blocked",
    "proof.canary_evidence",
)
PROOF_BOOLS = ("proof.conformance_passed", "proof.live_canary_blocked", "proof.live_shell_canary_blocked")
# Leaves that are NOT {"value","tier","evidence"} data: the version, the agent identity, the file list, the proof.
PLAIN_LEAVES = ("v", "agent.name", "agent.version", "agent.surface", "payload_capture.files",
                "proof.canary_evidence") + PROOF_BOOLS
TIERS = ("MEASURED", "READ", "INFERRED", "ASSUMED")
STATUS_FULL = "locally adapted, self-verified"
STATUS_PARTIAL = "partially supported: Layers 0-2"
_RANK = {STATUS_PARTIAL: 0, STATUS_FULL: 1}
FAILED_HOOK_VALUES = ("allows", "blocks", "unknown")
# The leaves that gate the strong status (all must be MEASURED); the value each must hold is in _gate_holds().
GATING = ("pre_tool_hook.fires_on_this_surface", "pre_tool_hook.can_block", "pre_tool_hook.failed_hook_behaviour",
          "pre_tool_hook.every_write_and_shell_tool_mapped")
# The agent's hook timeout gates too, unless a failed hook BLOCKS: a hook the agent gives up on before the adapter
# answers lets the call RUN on an agent whose failed hook allows. Read from the template, so the two cannot drift.
TIMEOUT_GATE = "pre_tool_hook.hook_timeout_s"
ADAPTER_TIMEOUT_S = int(re.search(r"^ADAPTER_TIMEOUT_S = (\d+)", TEMPLATE.read_text(encoding="utf-8"), re.M).group(1))
HOOK_TIMEOUT_MARGIN_S = 5    # Python start-up and the engine's own spawn, on a loaded machine
_MISSING = object()


def _get(report, path: str):
    node = report
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _data(report, path: str):
    """-> (value, tier) of a wrapped leaf; (None, None) when it is missing, unfilled or malformed."""
    leaf = _get(report, path)
    if isinstance(leaf, dict):
        return leaf.get("value"), leaf.get("tier")
    return None, None


def _has_evidence(report, path: str) -> bool:
    leaf = _get(report, path)
    ev = leaf.get("evidence") if isinstance(leaf, dict) else None
    return isinstance(ev, str) and bool(ev.strip())


def _is_seconds(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _measured(report, path: str, ok_value) -> bool:
    value, tier = _data(report, path)
    return tier == "MEASURED" and ok_value(value) and _has_evidence(report, path)


def _gate_holds(report) -> bool:
    for path, ok_value in (("pre_tool_hook.fires_on_this_surface", lambda v: v is True),
                           ("pre_tool_hook.can_block", lambda v: v is True),
                           ("pre_tool_hook.failed_hook_behaviour", lambda v: v in ("allows", "blocks")),
                           ("pre_tool_hook.every_write_and_shell_tool_mapped", lambda v: v is True)):
        if not _measured(report, path, ok_value):
            return False
    if _data(report, "pre_tool_hook.failed_hook_behaviour")[0] == "blocks":
        return True
    return _measured(report, TIMEOUT_GATE,
                     lambda v: _is_seconds(v) and v >= ADAPTER_TIMEOUT_S + HOOK_TIMEOUT_MARGIN_S)


def derive_status(report) -> str:
    """The ONLY place a status string is produced. A report can never declare its own."""
    if not isinstance(report, dict) or not _gate_holds(report):
        return STATUS_PARTIAL
    proof = report.get("proof")
    if not isinstance(proof, dict):
        return STATUS_PARTIAL
    evidence = proof.get("canary_evidence")
    if (all(proof.get(p.split(".")[1]) is True for p in PROOF_BOOLS)
            and isinstance(evidence, str) and evidence.strip()):
        return STATUS_FULL
    return STATUS_PARTIAL


def validate_report(report) -> List[str]:
    if not isinstance(report, dict):
        return ["report: must be a JSON object"]
    problems: List[str] = []
    sections = {l.split(".")[0] for l in LEAVES}
    for key in report:
        if key not in sections and key != "claimed_status":
            problems.append("{}: unknown key (a report has only the template's keys and an optional claimed_status)".format(key))
        elif key in sections and isinstance(report[key], dict):
            known = {l.split(".", 1)[1] for l in LEAVES if l.startswith(key + ".")}
            for sub in report[key]:
                if sub not in known:
                    problems.append("{}.{}: unknown key".format(key, sub))
    for leaf in LEAVES:
        value = _get(report, leaf)
        if value is _MISSING:
            problems.append("{}: missing".format(leaf))
            continue
        if leaf == "v":
            if type(value) is not int or value != 1:
                problems.append("v: must be the integer 1")
        elif leaf in PROOF_BOOLS:
            if not isinstance(value, bool):
                problems.append("{}: must be true or false".format(leaf))
        elif leaf == "payload_capture.files":
            if not isinstance(value, list):
                problems.append("{}: must be a list".format(leaf))
        elif leaf in PLAIN_LEAVES:
            if not isinstance(value, str):
                problems.append("{}: must be a string".format(leaf))
        elif isinstance(value, dict):
            for extra in sorted(set(value) - {"value", "tier", "evidence"}, key=str):
                problems.append("{}: unknown key '{}' (only value, tier, evidence)".format(leaf, extra))
            if "value" not in value:
                problems.append("{}: no 'value'".format(leaf))
            if value.get("tier") not in TIERS:
                problems.append("{}: tier must be one of {}".format(leaf, "|".join(TIERS)))
            ev = value.get("evidence")
            if not isinstance(ev, str) or not ev.strip():
                problems.append("{}: evidence must name a file or command".format(leaf))
            if leaf == "pre_tool_hook.failed_hook_behaviour" and value.get("value") not in FAILED_HOOK_VALUES:
                problems.append("{}: value must be one of {}".format(leaf, "|".join(FAILED_HOOK_VALUES)))
            if leaf == TIMEOUT_GATE and not _is_seconds(value.get("value")):
                problems.append("{}: value must be the agent's hook timeout in seconds, a positive number".format(leaf))
        elif value not in ("", None):
            problems.append("{}: a filled leaf must be {{value, tier, evidence}}".format(leaf))
    exists, _ = _data(report, "pre_tool_hook.exists")
    fires, _ = _data(report, "pre_tool_hook.fires_on_this_surface")
    if exists is False and fires is True:
        problems.append("pre_tool_hook.exists: false, yet fires_on_this_surface is true; a hook that does not exist cannot fire")
    derived = derive_status(report)
    if "claimed_status" in report:
        claim = report["claimed_status"]
        if not isinstance(claim, str) or claim not in _RANK:
            problems.append("claimed_status: must be one of '{}' or '{}'; the toolkit never lets a report say "
                            "'supported'".format(STATUS_FULL, STATUS_PARTIAL))
        elif _RANK[claim] > _RANK[derived]:
            problems.append("claimed_status: claims '{}' but the measured leaves only derive '{}'".format(claim, derived))
            for path in GATING + (TIMEOUT_GATE,):
                _, tier = _data(report, path)
                if tier in ("INFERRED", "ASSUMED"):
                    problems.append("{}: tier {} cannot support the stronger status".format(path, tier))
    return problems


def report_main(path: str) -> int:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("cannot read the report: {}".format(e))
        return 1
    problems = validate_report(report)
    status = derive_status(report)
    # The strong status is printed only for a report with no problems at all.
    print(STATUS_PARTIAL if problems and status == STATUS_FULL else status)
    for p in problems:
        print("PROBLEM " + p)
    return 1 if problems else 0


def summary(rows: List[Row]) -> str:
    bad = [r for r in rows if not r.ok]
    s = "CONFORMANCE PASS ({}/{})".format(len(rows), len(rows)) if not bad else \
        "CONFORMANCE FAIL ({} of {})".format(len(bad), len(rows))
    return s + (" - " + SHELL_NOTE if any(r.id == "shell-unguarded" for r in rows) else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--adapter-dir", required=True)
    r.add_argument("--profile", required=True)
    r.add_argument("--cases", required=True)
    r.add_argument("--command")
    r.add_argument("--port", action="store_true")
    rp = sp.add_parser("report", help="validate a capability report and print the status it derives")
    rp.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        return report_main(a.file)
    command = a.command
    if command is not None:
        try:
            command = json.loads(command)     # a JSON argv list; anything else is refused by run_conformance
        except ValueError:
            pass
    profile = json.loads(Path(a.profile).read_text(encoding="utf-8"))
    rows = run_conformance(a.adapter_dir, profile, json.loads(Path(a.cases).read_text(encoding="utf-8")), command, a.port)
    for row in rows:
        print("{} {}: {}".format("PASS" if row.ok else "FAIL", row.id, row.why))
    if not any(r.id == "profile" for r in rows):
        print(unmapped_note(profile))
    print(summary(rows))
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
