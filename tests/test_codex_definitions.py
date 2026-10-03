"""The two FROZEN Codex hook definitions (Plan 4 Task 8; R1, R2, R3, R6, R7, R14).

Codex hashes every field of a hook definition and runs a project hook only while the user's stored hash
still equals it (E18). So ANY byte change to a definition silently stops every approved copy from running
until each user re-approves it in /hooks. These tests pin what the definitions must be, and the freeze
gate makes a change impossible to ship without the CHANGELOG telling users to re-approve.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codex_trust  # noqa: E402

TARGET = REPO / "agent" / "targets" / "codex"
HOOKS_JSON = TARGET / "hooks.json"
LEDGER = TARGET / "FROZEN.sha256"
ENTRY = REPO / "agent" / "guards" / "adapters" / "codex"
VECTORS = REPO / "tests" / "fixtures" / "codex" / "0.160.0" / "trust_vectors.json"
REL = "agent/targets/codex/hooks.json"
REAPPROVE = "re-approve your Codex hooks"
PLACEHOLDERS = {"{{CODEX_ROOT}}", "{{CODEX_ROOT_POSIX}}"}
ROLE_OF = {"SessionStart": "session-start", "PreToolUse": "pre-tool"}
# How far below a definition's timeout each wrapper deadline must sit: a wrapper answered up to ~5.5 s
# past its deadline (MEASURED, Task 7: taskkill wait + bounded drain), plus a PowerShell cold start (p95
# up to ~1.6 s on a loaded machine, Task 0), roughly doubled.
MARGIN_S = 15


def _hooks() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]


def _handlers():
    for event, groups in _hooks().items():
        for g in groups:
            for h in g["hooks"]:
                yield event, g, h


def _commands():
    for event, _, h in _handlers():
        yield event, "command", h["command"]
        yield event, "commandWindows", h["commandWindows"]


def test_exactly_two_definitions():
    hooks = _hooks()
    assert sorted(hooks) == ["PreToolUse", "SessionStart"]
    assert len(hooks["SessionStart"]) == 1 and len(hooks["PreToolUse"]) == 1
    assert "matcher" not in hooks["SessionStart"][0]
    assert hooks["PreToolUse"][0]["matcher"] == ".*"
    assert all(len(g["hooks"]) == 1 for groups in hooks.values() for g in groups)


def test_only_measured_fields():
    """failureMode (R6) and additionalContextLimit (R7) were ruled OUT by Task 0: failureMode is ignored
    on 0.160.0, and the adapter caps its own context. Anything else unmeasured stays out too."""
    for event, g, h in _handlers():
        assert set(h) == {"type", "command", "commandWindows", "timeout", "statusMessage"}, (event, h)
        assert h["type"] == "command"
        assert set(g) <= {"matcher", "hooks"}


def test_no_command_starts_with_a_quote():
    """R2: a command starting with `"` failed to launch on Codex (MEASURED 1/1): a silent fail-open."""
    for event, field, cmd in _commands():
        assert cmd == cmd.strip() and not cmd.startswith(('"', "'")), (event, field)


def test_windows_commands_never_call_bash():
    """R2: under cmd.exe `bash` can resolve to the WSL stub, which fails: the command then RUNS."""
    for event, _, h in _handlers():
        win = h["commandWindows"]
        assert not re.search(r"(?i)\bbash(\.exe)?\b", win), (event, win)
        role = ROLE_OF[event]
        entry = '-File "{{CODEX_ROOT}}/.codex/hooks/skyguard-entry.ps1" ' + role
        flags = "-NoProfile -NonInteractive -ExecutionPolicy Bypass "
        assert win == "pwsh " + flags + entry + " || powershell " + flags + entry, (event, win)


def test_every_command_runs_the_entry_with_a_known_role():
    for event, _, h in _handlers():
        role = ROLE_OF[event]
        assert h["command"] == 'bash "{{CODEX_ROOT_POSIX}}/.codex/hooks/skyguard-entry.sh" ' + role, event
        assert h["commandWindows"].count("/.codex/hooks/skyguard-entry.ps1\" " + role) == 2, event
        # the role names the entries accept
        for name in ("skyguard-entry.ps1", "skyguard-entry.sh"):
            assert role in (ENTRY / name).read_text(encoding="ascii"), (name, role)


def test_definitions_carry_no_python_path_and_no_other_placeholder():
    """R3: the interpreter path lives in the entry scripts; a Python upgrade must not touch a definition."""
    text = HOOKS_JSON.read_text(encoding="utf-8")
    assert set(re.findall(r"\{\{[^}]*\}\}", text)) == PLACEHOLDERS
    assert not re.search(r"(?i)python|\.exe\b|py -3", text), "an interpreter in a definition"
    # nothing machine-specific: no drive path other than the placeholders, no user folder
    assert not re.search(r"(?i)[a-z]:[\\/]|/users/|/home/", text.replace("{{CODEX_ROOT}}", "")), text


def _deadline(name: str, role: str) -> int:
    text = (ENTRY / name).read_text(encoding="ascii")
    if name.endswith(".ps1"):
        pre = int(re.search(r"^\s*\$limit = (\d+)\s*$", text, re.M).group(1))
        ses = int(re.search(r"session-start'\) \{ \$limit = (\d+) \}", text).group(1))
    else:
        m = re.search(r'^LIMIT=(\d+); \[ "\$ROLE" = "session-start" \] && LIMIT=(\d+)$', text, re.M)
        pre, ses = int(m.group(1)), int(m.group(2))
    return ses if role == "session-start" else pre


def test_timeouts_exceed_the_wrappers_own_deadlines():
    """Codex RUNS the command when a hook outlives its timeout, so each wrapper must answer first."""
    for event, _, h in _handlers():
        role = ROLE_OF[event]
        for name in ("skyguard-entry.ps1", "skyguard-entry.sh"):
            d = _deadline(name, role)
            assert h["timeout"] - d >= MARGIN_S, (event, name, h["timeout"], d)


def test_the_template_hashes_to_the_measured_vectors():
    """Rendered for the neutral root, the template's two hashes are vectors 1 and 4 -- the ones Codex
    0.160.0 itself listed (Task 0). A field Codex hashes differently would show here first."""
    root, posix = "C:/SkyguardVectors/Skyrim Probe (x86)", "/c/SkyguardVectors/Skyrim Probe (x86)"
    text = HOOKS_JSON.read_text(encoding="utf-8")
    hooks = json.loads(text.replace("{{CODEX_ROOT_POSIX}}", posix).replace("{{CODEX_ROOT}}", root))["hooks"]
    vectors = {v["label"]: v for v in json.loads(VECTORS.read_text(encoding="utf-8"))}
    for event, label in (("SessionStart", "v1 SessionStart (Task 8 template)"),
                         ("PreToolUse", "v1 PreToolUse (Task 8 template)")):
        g = hooks[event][0]
        v = vectors[label]
        assert g["hooks"][0] == v["handler"] and {k: x for k, x in g.items() if k != "hooks"} == v["group"]
        assert codex_trust.hook_hash(codex_trust.snake(event), g, g["hooks"][0], True) == v["codex_hash"]


def test_the_codex_target_is_lf_and_pinned_lf():
    """An autocrlf checkout must not rewrite these: the freeze gate and the generator compare bytes."""
    for p in sorted(TARGET.iterdir()):
        assert b"\r" not in p.read_bytes(), p.name
        if (REPO / ".git").exists():
            attr = _git("check-attr", "eol", "--", p.relative_to(REPO).as_posix()).stdout.decode()
            assert attr.strip().endswith("eol: lf"), attr


def test_project_config_sets_only_the_doc_budget():
    text = (TARGET / "config.toml").read_text(encoding="utf-8")
    keys = [ln.split("=")[0].strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert keys == ["project_doc_max_bytes"]
    assert "project_doc_max_bytes = 65536" in text


# --------------------------------------------------------------------------------------------------
# the freeze (R14)
# --------------------------------------------------------------------------------------------------
def _lf_sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _changelog_top() -> str:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    heads = [m.start() for m in re.finditer(r"^## ", text, re.M)]
    assert heads, "CHANGELOG.md has no section"
    return text[heads[0]:heads[1] if len(heads) > 1 else len(text)]


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True)


def freeze_problems(ledger_text: str, template: bytes, changelog_top: str, released) -> list:
    """What is wrong with the freeze, as sentences (empty = fine).

    `released` is (tag, template bytes) of the newest tag carrying the definitions, or None.
    - the template's LF sha256 must be the ledger's LAST line;
    - a ledger with more than one line means a definition changed after v1: the CHANGELOG's top section must
      say REAPPROVE -- until a release carries that change (then the tag half below takes over, so a later
      Unreleased section is not forced to repeat it forever);
    - a template that differs from the newest release carrying one also requires that line.
    """
    out = []
    ledger = [ln.split() for ln in ledger_text.splitlines() if ln.strip()]
    if not ledger or not all(len(x) == 2 and re.fullmatch(r"[0-9a-f]{64}", x[0]) for x in ledger):
        return ["FROZEN.sha256 is not '<sha256> <label>' lines: {!r}".format(ledger_text[:200])]
    now = _lf_sha(template)
    if now != ledger[-1][0]:
        out.append(
            "agent/targets/codex/hooks.json changed but FROZEN.sha256 does not record it. Changing a Codex hook "
            "definition makes every user re-approve their hooks (until then NOTHING is checked). If that is "
            "really intended: append '<sha> <label>' to FROZEN.sha256 and add '" + REAPPROVE + "' to the "
            "CHANGELOG's top section.")
    unreleased_change = released is None or _lf_sha(released[1]) != now
    if len(ledger) > 1 and unreleased_change and REAPPROVE not in changelog_top:
        out.append("the definitions changed since v1 (FROZEN.sha256 has {} lines): the CHANGELOG's top "
                   "section must say '{}'".format(len(ledger), REAPPROVE))
    if released is not None and _lf_sha(released[1]) != now and REAPPROVE not in changelog_top:
        out.append("hooks.json differs from release {}: the CHANGELOG's top section must say '{}'".format(
            released[0], REAPPROVE))
    return out


def _released():
    """(tag, bytes) of the newest tag whose tree has the definitions; None if none (or no git here)."""
    if not (REPO / ".git").exists():
        print("freeze gate: no git checkout here -- the tag comparison did not run")
        return None
    tags = _git("tag", "--sort=-creatordate").stdout.decode().split()
    for tag in tags:
        blob = _git("show", "{}:{}".format(tag, REL))
        if blob.returncode == 0:
            return tag, blob.stdout
    print("freeze gate: no tag carries {} yet -- only the ledger half ran".format(REL))
    return None


def test_codex_hook_definitions_are_frozen():
    """Change a definition and every user's approved copy silently stops running (E18)."""
    problems = freeze_problems(LEDGER.read_text(encoding="utf-8"), HOOKS_JSON.read_bytes(),
                               _changelog_top(), _released())
    assert not problems, "\n".join(problems)


def test_the_freeze_gate_can_fail():
    """One twin per clause of freeze_problems (each guard shadows the ones behind it)."""
    t1, t2 = b'{"a": 1}\n', b'{"a": 2}\n'
    s1, s2 = _lf_sha(t1), _lf_sha(t2)
    v1 = s1 + " v1\n"
    v2 = v1 + s2 + " v2\n"
    say, quiet = "## Unreleased\n- " + REAPPROVE + "\n", "## Unreleased\n- other\n"
    assert freeze_problems(v1, t1, quiet, None) == []
    assert freeze_problems(v1, t1.replace(b"\n", b"\r\n"), quiet, None) == []    # line endings do not count
    assert freeze_problems("nonsense\n", t1, say, None)[0].startswith("FROZEN.sha256 is not")
    # the template changed, the ledger did not
    assert "does not record it" in freeze_problems(v1, t2, say, None)[0]
    # the ledger grew (v2) but the CHANGELOG is silent: before any release, and when the release is older
    assert "has 2 lines" in freeze_problems(v2, t2, quiet, None)[0]
    assert any("has 2 lines" in p for p in freeze_problems(v2, t2, quiet, ("v4.0.0", t1)))
    assert freeze_problems(v2, t2, say, ("v4.0.0", t1)) == []
    # once a release carries v2, a later Unreleased section need not repeat the line
    assert freeze_problems(v2, t2, quiet, ("v4.1.0", t2)) == []
    # the tag half alone: the template differs from the release (ledger rewritten to hide it)
    assert "differs from release v4.0.0" in freeze_problems(s2 + " v1\n", t2, quiet, ("v4.0.0", t1))[0]
