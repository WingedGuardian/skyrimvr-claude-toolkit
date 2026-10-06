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

import pytest

from conftest import BS, REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codex_definitions, codex_trust  # noqa: E402

TARGET = REPO / "agent" / "targets" / "codex"
HOOKS_JSON = TARGET / "hooks.json"
LEDGER = TARGET / "FROZEN.sha256"
ENTRY = REPO / "agent" / "guards" / "adapters" / "codex"
VECTORS = REPO / "tests" / "fixtures" / "codex" / "0.160.0" / "trust_vectors.json"
REL = "agent/targets/codex/hooks.json"
REAPPROVE = "re-approve your Codex hooks"
PLACEHOLDERS = {"{{CODEX_ROOT}}", "{{CODEX_ROOT_POSIX}}"}
ROLE_OF = {"SessionStart": "session-start", "PreToolUse": "pre-tool"}
# How far below a definition's timeout each wrapper deadline must sit. One call can cost: TWO PowerShell
# cold starts (pwsh, then the `||` rerun under 5.1 when pwsh ends non-zero; p95 up to ~1.6 s each on a
# loaded machine, Task 0), the wrapper's own deadline, ~5.5 s past it (MEASURED, Task 7: taskkill wait +
# bounded drain), and the 5.1 rerun's own answer. ~10 s on top of the deadline, doubled: Codex RUNS the
# command when the hook outlives its timeout.
MARGIN_S = 20
V1_SHA = "3cc0c07ede4e2f5786bd6d55008cd1fbde25cea9226de7856f036bfeb6e9b8db"   # the v1 definitions, forever
NEUTRAL_WIN, NEUTRAL_POSIX = "C:/SkyguardVectors/Skyrim Probe (x86)", "/c/SkyguardVectors/Skyrim Probe (x86)"


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
    text = HOOKS_JSON.read_text(encoding="utf-8")
    _assert_vectors(text.replace("{{CODEX_ROOT_POSIX}}", NEUTRAL_POSIX).replace("{{CODEX_ROOT}}", NEUTRAL_WIN))


def _assert_vectors(rendered: str) -> None:
    hooks = json.loads(rendered)["hooks"]
    vectors = {v["label"]: v for v in json.loads(VECTORS.read_text(encoding="utf-8"))}
    for event, label in (("SessionStart", "v1 SessionStart (Task 8 template)"),
                         ("PreToolUse", "v1 PreToolUse (Task 8 template)")):
        g = hooks[event][0]
        v = vectors[label]
        assert g["hooks"][0] == v["handler"] and {k: x for k, x in g.items() if k != "hooks"} == v["group"]
        assert codex_trust.hook_hash(codex_trust.snake(event), g, g["hooks"][0], True) == v["codex_hash"]


@pytest.mark.parametrize("win,posix", [
    (NEUTRAL_WIN, NEUTRAL_POSIX),
    (NEUTRAL_WIN.replace("/", BS), NEUTRAL_POSIX),                       # as Windows spells it
    (NEUTRAL_WIN + "/", NEUTRAL_POSIX + "/"),                            # a trailing slash
    (NEUTRAL_WIN.replace("/", BS) + BS, NEUTRAL_POSIX + "//"),
], ids=["forward", "backslashes", "trailing-slash", "backslash-trailing"])
def test_the_real_renderer_produces_the_measured_hashes(tmp_path, win, posix):
    """THE TASK 11 CONTRACT: setup.sh renders the definitions with tools/skyguard/codex_definitions.py, and
    that renderer is frozen with them -- a change to slash, trailing-slash or case handling changes every
    user's hash. Run through its CLI, as setup.sh will."""
    out = tmp_path / "hooks.json"
    r = subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "codex_definitions.py"), "render",
                        "--template", str(HOOKS_JSON), "--root-win", win, "--root-posix", posix, "--out", str(out)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    _assert_vectors(out.read_text(encoding="utf-8"))


def test_the_renderer_keeps_case():
    """Codex hashes the text it reads; case is not ours to change."""
    text = codex_definitions.render(HOOKS_JSON.read_text(encoding="utf-8"), "c:/skyguardvectors/x", "/c/x")
    assert "c:/skyguardvectors/x/.codex" in text and "C:/" not in text


@pytest.mark.parametrize("bad", list('%!$`"'))
def test_the_renderer_refuses_a_root_the_hooks_cannot_run_from(tmp_path, bad):
    out = tmp_path / "hooks.json"
    for win, posix in ((NEUTRAL_WIN + bad, NEUTRAL_POSIX), (NEUTRAL_WIN, NEUTRAL_POSIX + bad)):
        r = subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "codex_definitions.py"), "render",
                            "--template", str(HOOKS_JSON), "--root-win", win, "--root-posix", posix,
                            "--out", str(out)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 2 and "NOT rendered" in r.stderr and repr(bad) in r.stderr, (bad, r.stderr)
        assert not out.exists()


@pytest.mark.parametrize("win,posix", [("SkyrimVR", "/c/x"), ("C:/x", "c/x"), ("C:/x", "/c/x" + BS + "y")],
                         ids=["relative-win", "relative-posix", "posix-backslash"])
def test_the_renderer_refuses_a_root_it_cannot_spell(win, posix):
    with pytest.raises(codex_definitions.RootRefused):
        codex_definitions.render(HOOKS_JSON.read_text(encoding="utf-8"), win, posix)


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


def _top_section(changelog: str) -> str:
    heads = [m.start() for m in re.finditer(r"^## ", changelog, re.M)]
    if not heads:
        return ""
    return changelog[heads[0]:heads[1] if len(heads) > 1 else len(changelog)]


def _git(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd or REPO), capture_output=True)


def freeze_problems(ledger_text: str, template: bytes, changelog: str, released, tags_seen: bool,
                    v1: str = V1_SHA) -> list:
    """What is wrong with the freeze, as sentences (empty = fine).

    `released` is (tag, template bytes) of the newest tag carrying the definitions, or None; `tags_seen` says
    whether this checkout has ANY tag (a shallow, tagless or git-less copy has none, and would otherwise pass
    the release half vacuously).
    - the ledger is append-only: its FIRST line is the v1 sha, hard-coded here, so a ledger rewritten to one
      new line cannot hide a change;
    - the template's LF sha256 must be the ledger's LAST line;
    - a ledger with more than one line means a definition changed after v1: the CHANGELOG's top section must
      say REAPPROVE -- until a release carries that change (then the tag half below takes over, so a later
      Unreleased section is not forced to repeat it forever);
    - a template that differs from the newest release carrying one also requires that line;
    - a CHANGELOG that lists a released version in a checkout without tags cannot run the tag half: fail.
    """
    out = []
    changelog_top = _top_section(changelog)
    ledger = [ln.split() for ln in ledger_text.splitlines() if ln.strip()]
    if not ledger or not all(len(x) == 2 and re.fullmatch(r"[0-9a-f]{64}", x[0]) for x in ledger):
        return ["FROZEN.sha256 is not '<sha256> <label>' lines: {!r}".format(ledger_text[:200])]
    if ledger[0][0] != v1:
        out.append("FROZEN.sha256 is append-only: its first line must stay the v1 definitions ({}).".format(v1))
    if re.search(r"^## v[0-9]", changelog, re.M) and not tags_seen:
        out.append("the CHANGELOG lists released versions but this checkout has no git tags (a shallow or "
                   "tagless clone, or no git): the release half of the freeze gate cannot run. Fetch the tags "
                   "(CI: actions/checkout with fetch-depth: 0).")
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


def _released(repo=None):
    """((tag, bytes) of the newest tag whose tree has the definitions, or None; does any tag exist?)

    A tag on HEAD itself is skipped (Plan 8 T10.5): release.yml runs this gate on the tag being released, and
    comparing that tag's definitions with themselves passes by construction. The baseline of a release is the
    release BEFORE it -- and for the first release carrying the definitions (v4.0.0, a "tagless release" for
    this file) there is none, so FROZEN.sha256 decides."""
    repo = repo or REPO
    if not (repo / ".git").exists():
        return None, False
    tags = _git("tag", "--sort=-creatordate", cwd=repo).stdout.decode().split()
    head = _git("rev-parse", "HEAD", cwd=repo).stdout.decode().strip()
    for tag in tags:
        if _git("rev-parse", tag + "^{commit}", cwd=repo).stdout.decode().strip() == head:
            continue
        blob = _git("show", "{}:{}".format(tag, REL), cwd=repo)
        if blob.returncode == 0:
            return (tag, blob.stdout), True
    if tags:
        print("freeze gate: no earlier tag carries {} -- the ledger (FROZEN.sha256) decides".format(REL))
    return None, bool(tags)


def _tagged_repo(tmp_path, steps):
    """A throwaway repo: each step is (hooks.json text, tag or None), committed in order."""
    r = tmp_path / "repo"
    (r / "agent" / "targets" / "codex").mkdir(parents=True)
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(g + ["init", "-q"], cwd=str(r), check=True, capture_output=True)
    for text, tag in steps:
        (r / REL).write_bytes(text.encode("utf-8"))      # exact bytes, whatever git's autocrlf says
        subprocess.run(g + ["add", "-A"], cwd=str(r), check=True, capture_output=True)
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "x"], cwd=str(r), check=True, capture_output=True)
        if tag:
            subprocess.run(g + ["tag", tag], cwd=str(r), check=True, capture_output=True)
    return r


def test_the_release_being_cut_is_not_its_own_baseline(tmp_path):
    """Plan 8 T10.5, both branches. The first release carrying the definitions has NO baseline (the ledger
    decides); a later release compares against the previous tag, never the tag at HEAD."""
    # tagless release: the only tag carrying hooks.json is the one being released (HEAD)
    first = _tagged_repo(tmp_path / "a", [('{"v": 1}\n', "v3.9.1"), ('{"v": 2}\n', "v4.0.0")])
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "tag", "-d", "v3.9.1"], cwd=str(first),
                   capture_output=True)
    released, tags_seen = _released(first)
    assert released is None and tags_seen, released
    # a later release: v4.1.0 at HEAD is skipped, v4.0.0 (the previous) is the baseline
    later = _tagged_repo(tmp_path / "b", [('{"v": 1}\n', "v4.0.0"), ('{"v": 2}\n', "v4.1.0")])
    released, tags_seen = _released(later)
    assert released is not None and released[0] == "v4.0.0" and released[1] == b'{"v": 1}\n', released
    # ...and once HEAD moves past the tag (ordinary master CI), the newest tag is the baseline again
    on = _tagged_repo(tmp_path / "c", [('{"v": 1}\n', "v4.0.0"), ('{"v": 1}\n', None)])
    assert _released(on)[0][0] == "v4.0.0"


def test_codex_hook_definitions_are_frozen():
    """Change a definition and every user's approved copy silently stops running (E18)."""
    released, tags_seen = _released()
    problems = freeze_problems(LEDGER.read_text(encoding="utf-8"), HOOKS_JSON.read_bytes(),
                               (REPO / "CHANGELOG.md").read_text(encoding="utf-8"), released, tags_seen)
    assert not problems, "\n".join(problems)


def test_the_freeze_gate_can_fail():
    """One twin per clause of freeze_problems (each guard shadows the ones behind it)."""
    t1, t2 = b'{"a": 1}\n', b'{"a": 2}\n'
    s1, s2 = _lf_sha(t1), _lf_sha(t2)
    v1 = s1 + " v1\n"
    v2 = v1 + s2 + " v2\n"
    old = "\n## v3.9.1\n- old\n"
    say, quiet = "## Unreleased\n- " + REAPPROVE + "\n" + old, "## Unreleased\n- other\n" + old

    def fp(ledger, tpl, changelog, rel, tags=True):
        return freeze_problems(ledger, tpl, changelog, rel, tags, v1=s1)

    assert fp(v1, t1, quiet, None) == []
    assert fp(v1, t1.replace(b"\n", b"\r\n"), quiet, None) == []    # line endings do not count
    assert fp("nonsense\n", t1, say, None)[0].startswith("FROZEN.sha256 is not")
    # append-only: a one-line ledger rewritten to the new template's sha
    rewritten = fp(s2 + " v1\n", t2, say, None)
    assert len(rewritten) == 1 and "append-only" in rewritten[0], rewritten
    # the template changed, the ledger did not
    assert "does not record it" in fp(v1, t2, say, None)[0]
    # the ledger grew (v2) but the CHANGELOG is silent: before any release, and when the release is older
    assert "has 2 lines" in fp(v2, t2, quiet, None)[0]
    assert any("has 2 lines" in p for p in fp(v2, t2, quiet, ("v4.0.0", t1)))
    assert fp(v2, t2, say, ("v4.0.0", t1)) == []
    # once a release carries v2, a later Unreleased section need not repeat the line
    assert fp(v2, t2, quiet, ("v4.1.0", t2)) == []
    # the tag half alone: the ledger and template agree, the newest release differs
    assert fp(v1, t1, quiet, ("v4.0.0", t2)) == [
        "hooks.json differs from release v4.0.0: the CHANGELOG's top section must say '{}'".format(REAPPROVE)]
    # no tags where the CHANGELOG lists releases: the tag half would pass vacuously
    assert "no git tags" in fp(v1, t1, quiet, None, tags=False)[0]
    assert fp(v1, t1, "## Unreleased\n- first\n", None, tags=False) == []     # nothing released yet
