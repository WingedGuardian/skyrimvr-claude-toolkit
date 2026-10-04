# Plan 7 — `ADAPTING.md`, toy-agent CI test, agent-neutral setup runbook, universal setup prompt

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent the toolkit has never heard of wire the toolkit into itself, and let CI prove that
the written instructions are enough. Deliver: (1) a small agent-neutral engine entry (`run.py neutral
pre-tool`) that speaks the spec §5 request/verdict contract over stdin/stdout; (2) a fail-closed adapter
template whose only agent-specific part is a marked SLOTS block, plus a conformance tool that checks any
adapter against it; (3) `ADAPTING.md`, in which every factual claim cites code, a spec evidence row or a
measurement, and a gate that resolves those citations; (4) a toy-agent CI test that follows `ADAPTING.md`
end to end (a decoy `.esp` write is blocked, a broken engine fails closed, and the test can be shown
to go red); (5) an agent-neutral setup runbook skill with a runtime-inventory tool that never installs
anything on its own; (6) one universal setup prompt.

**Architecture:** The adapter is **outside** the engine. It is a ~150-line script that the agent's hook
runs. It translates the agent's payload into a Request dict, runs
`python tools/skyguard/run.py neutral pre-tool`, validates the answer (`SKYGUARD-OK` marker + JSON verdict
with `v == 1`), renders the verdict as the agent's own hard block, and on *any* failure renders that block
anyway. Nothing about a new agent is registered inside `run.py`: the engine learns exactly one new role,
`neutral`, whose parsing is strict and whose failures are denials. `ADAPTING.md` tells the agent which
four slots to fill (`map_payload`, `render_block`, `render_allow`, optionally `render_ask`, plus a literal
`STATIC_BLOCK`), and `tools/adapter_conformance.py` proves the result against a scratch copy of the
install: payload cases, a fault matrix on the engine, a byte-identity check of everything outside the
slots. The toy-agent test composes two worked-example slot files (a JSON-deny agent and an exit-code agent)
into the template and drives them from a fake agent runtime that fails *open* like Codex does.

**Tech Stack:** Python 3.8 syntax floor for everything the adapter or engine runs (stdlib only; 3.10 locally,
3.11 in CI), pytest 8, Markdown. No new dependency. No Node, no jq, no bash in the toy test (it uses
`sys.executable` only), so it runs on all three OS legs of the existing CI matrix.

**Spec:** `docs/superpowers/specs/2026-09-30-universal-agent-support-design.md` — §3 unknown-agent row, §5
contract and adapter duties, §11 `ADAPTING.md`, D7, D14, D17. Roadmap row 7:
`C:/Users/Moona/.claude/plans/looks-good-lovely-fern.md`.

## Assumed base (state this before starting; re-verify in Task 0)

Plan 7 starts **after Plans 4 and 5 are merged to `master`**. As observed 2026-10-04 (heads move; Task 0
re-reads the real ones):

- Plan 4 (`plan4`, head `106d9e7`): `tools/skyguard/{request,verdict,engine,run,codes,health,patch_paths,
  adapters/claude,adapters/codex}.py`; `agent/guards/adapters/codex/skyguard-entry.{sh,ps1}`;
  `tools/skyguard/codex_definitions.py`; `tests/fixtures/payloads/{claude,codex}`; `tests/test_codex_*`;
  `agent/{instructions,skills,targets}` + `scripts/gen_agent_trees.py`; 12 skills; `README.md` "Using with Codex".
- Plan 5 (`plan5`, head `058b0e2`): `shellparse.py`, `targets.py`, `deletes.py`, `writes.py` and friends;
  **E21 shell writes to plugins are denied** (`dffda1a`); relative paths resolve against the payload `cwd`;
  `Bash|PowerShell` matcher; `shell_rules.decide(..., cwd=, shell=)`.
- Plan 6 (Layer 2 lock, `guards off`, `skydoctor`) is **not** assumed. Nothing here may call `skydoctor`.
- Plan 8 owns `setup.sh --agent`, the product rename, README release text and the release. Plan 7 does not
  edit `setup.sh`.

If a name below (`engine.pre_tool`, `Request`, `health.record`, …) differs on the merged base, Task 0 pins
the real one in its measurement note and **every later task uses the pinned name**; do not guess.

Work happens in a worktree: `git worktree add ../skyrimvr-toolkit-plan7 -b plan7 master`.

## Global Constraints

- **Adapters translate; the engine decides.** No rule, corpus row or engine verdict is edited to make an
  adapter pass. The only engine change is the new `neutral` role (Task 1), additive, with its own tests.
- **Fail closed, everywhere the adapter can fail** (spec §5.1, E8, E17): no stdin, unparseable payload, a
  slot that raises (or calls `sys.exit`), engine missing / crashing / silent / garbled / wrong `v` / wrong
  exit code / hung — each ends in the agent's block, never in an allow. *Only a mechanism the agent honours
  as a hard block counts*; the adapter's block shape is a slot the author must have measured.
- **Honest status vocabulary.** `ADAPTING.md` uses exactly: *supported* (only the Claude Code CLI and the
  Codex CLI, as the README lists them), *partially supported: Layers 0–2* (default for every other agent
  and surface), and *locally adapted, self-verified* (this install only, after §7 proof). The toolkit
  **never** says an adapted third agent is "supported" (D14: a surface is claimed only after a live probe
  proves its hooks fire).
- **No claim in `ADAPTING.md` without a citation** in the form `[src: path::needle]`, `[spec: E17]`,
  `[measured: path#needle]` or `[repo: path::needle]`; the claims gate resolves every one. A third-party
  agent is never named except in a sentence that says "not measured".
- **The runbook installs nothing without explicit task authorization.** The only tool that can *print* an
  install command prints it only for a runtime that is missing, only with an exact version the user's
  prompt names, only on Windows with `winget` present, and only in the form
  `--exact --version <V> --no-upgrade`. Nothing in this plan ever *runs* an install; the agent runs a
  printed command or the user does.
- Python files: 3.8 syntax (pinned by an `ast.parse(feature_version=(3, 8))` test), stdlib only, LF, ASCII
  in the adapter template (its output goes to an agent's stdout).
- Commit each task on branch `plan7`; commit messages end with the attribution line from the session. **Never
  push, never tag.** Do not edit the tree while a test or mutation run is executing. Test command:
  `python -m pytest tests/ -q -p no:cacheprovider`; use targeted files while iterating.
- Skills stay neutral: `tests/test_instruction_neutrality.py` judges every skill body (no `Claude`,
  `CLAUDE.md`, `/hooks`, `.claude/...` paths, `Bash tool`, ...). `ADAPTING.md` and `tools/` are not skills and
  may name agents, but only as the claims gate allows.

## Review Focus

1. **An adapter whose failure path itself fails** (a slot with a typo in `render_block`, a closed stdout)
   must still end in a block → `STATIC_BLOCK` literal, `_block_for_failure` try/except, and
   `test_unfilled_template_blocks_everything`, `test_hostile_slots_cannot_exit_zero`.
2. **A fix in the wrong wiring** (the project's recurring failure class): the template can be correct while
   the agent never runs it, or runs it with a hook timeout *shorter* than the adapter's. → `ADAPTING.md` §7
   makes the live canary mandatory before any claim, and `test_hook_timeout_shorter_than_adapter_fails_open`
   demonstrates the hazard with the toy rather than asserting it.
3. **A check that cannot fail.** Every conformance row and the end-to-end assertions have a named mutant that
   must turn them red (`MUTANTS` in Task 5), plus a control in which the toy writes the decoy when the hook is
   naive. Baseline green first.
4. **Documentation that says more than was measured.** `ADAPTING.md` is gated: uncited fact rows fail, an
   unresolvable citation fails, an unmeasured third-party agent named without "not measured" fails, and the gate
   itself has tests with a deliberately bad document.
5. **The runbook doing something to the host.** The inventory launches only `<exe> --version`; a recorder test
   proves no other argv is ever launched; the skill's fenced code blocks are scanned for installer/upgrade
   tokens; the shipped prompt must parse to *no* authorization.
6. **Prompt drift.** The setup prompt lives in three files that `test_setup_prompt_is_identical_everywhere`
   keeps byte-identical; the old prompt said "Install any missing prerequisites ... for me", which this plan
   reverses on purpose (ruling R5).

## Rulings (made while planning; the plan builds on them)

- **R1 — The adapter lives outside the engine; one new engine role.** `run.py neutral pre-tool` is the only
  engine change. Registering a *generator target* for an adapted agent is an upstreaming step, documented in
  `ADAPTING.md` §9, not part of self-adaptation.
- **R2 — `ADAPTING.md` is executable.** The template and two worked-example slot files are real files under
  `tools/adapting/`; the toy test composes them. The doc's worked examples are therefore tested, not pasted.
- **R3 — Status vocabulary** as in Global Constraints; the capability-report validator *derives* the status
  and rejects a self-reported claim above it.
- **R4 — No certification tooling for adapted agents in this plan.** `hook-canary` and `health.COUNTING_HOOKS`
  know only `claude` and `codex`; `skydoctor` is Plan 6. The template writes heartbeats under
  `.claude/backups/.hook-heartbeat/adapted/<name>/` for a human or a later plan; `ADAPTING.md` says plainly
  that no toolkit command certifies them yet. (Deferred item D-1.)
- **R5 — Setup runbook supersedes "install any missing prerequisites for me".** Default prompt text is
  `Host-software installs: NOT AUTHORIZED.` Authorization is a sentence the *user* edits, parsed by
  `parse_authorization`; anything unparsable is "not authorized". This also supersedes nothing in D17: Python is
  still never auto-installed by `setup.sh`.
- **R6 — Existing runtimes are never upgraded or repaired**, even when authorization is present: too-old and
  broken runtimes produce a report plus manual steps. Install commands are printed only for *missing* (or
  Microsoft-Store-alias) runtimes.
- **R7 — Unknown agents never receive an `ask`.** The template converts `ask` to a deny with a "run it
  yourself" prefix unless the author sets `render_ask` after measuring that their agent honours an ask. (Spec
  D2: an agent that cannot ask gets deny.)
- **R8 — `setup.sh` is not touched.** It currently auto-installs Node through `winget` (line ~64 on `plan4`);
  roadmap Plan 5 item (b) removes it. Task 0 checks; if it is still there it is recorded as deferred item D-2
  for Plan 8, and the new tripwire covers only the files Plan 7 adds.
- **R9 — Python startup cost is accepted and measured, not claimed.** An adapter adds one interpreter start
  per tool call. Task 5 measures the median and 95th percentile on this machine; `ADAPTING.md` cites that
  measurement and states its caveats.

---

### Task 0: Pin the base, de-risk the three unknowns (commit a measurement note only)

**Files:**
- Create: `docs/superpowers/measurements/2026-10-05-plan7-preflight.md`

Nothing but that note lands in the repo. If a check contradicts a later task, **stop and fix the plan text
first**.

- [ ] **Step 1: Verify the base.** In the worktree:
  `git log --oneline -3`, `git merge-base --is-ancestor plan4 HEAD && git merge-base --is-ancestor plan5 HEAD`
  (both must succeed; if not, stop: the plan's base is not met). Record the two heads.
- [ ] **Step 2: Pin interfaces.** Read, in full, and record in the note the exact signatures and field lists:
  `tools/skyguard/request.py` (`Request`), `engine.py` (`pre_tool`, `RootsUnavailable`, `NO_PATHS`),
  `run.py` (`_codex`, `_declared_env`, `_telemetry`, the `ADAPTERS`/`CODEX_ROLES` dispatch, argparse choices),
  `verdict.py`, `codes.py` (`classify`), `health.py` (`record`, `COUNTING_HOOKS`), `write_rules.DENY_PLUGIN`
  text, and how `shell_rules.decide` receives `cwd`/`shell` after Plan 5. Specifically answer: **(a)** does
  `engine.pre_tool` honour `Request.shell=None` as "both dialects", and `Request.cwd`? **(b)** does it deny
  `echo x > <dir>/Decoy.esp` (kind `shell`) and an `Add File` write to `<dir>/Decoy.esp` (kind `write`)?
  Answer (b) *by running them*: build a throwaway install in the scratchpad (copy `tools/skyguard`), run
  `python run.py codex pre-tool ...` against the Codex fixtures `codex-PreToolUse-apply_patch-1.json` /
  `codex-PreToolUse-Bash-1.json` with the command text replaced (the pattern in `tests/test_codex_entry.py:
  _plugin_write`). Record the deny texts verbatim. **(c)** does `generator --check` tolerate a new top-level
  `tools/adapting/` directory (it should: `tools/` is not generator input) and does the release bundle
  manifest (`tools/verify-release.sh`, `.github/workflows/release.yml`) ship `tools/*` and root `*.md`
  by rule or by list? If by list, record the exact lines Task 10 must extend.
- [ ] **Step 3: Check the runbook preconditions.** `grep -n "winget install\|choco\|apt-get\|brew install" setup.sh
  tools/*.sh` → record every hit (Node auto-install expected; deferred item D-2 if present). Read
  `winget install --help` on this machine (read-only) and record `winget --version` plus whether `--exact`,
  `--version`, `--no-upgrade`, `--source`, `--accept-package-agreements`, `--accept-source-agreements` exist.
  (Observed 2026-10-04 on v1.29.380: `-e/--exact`, `-v/--version`, `--no-upgrade`, both accept flags; `--source`
  **not confirmed**. Task 7's printed command includes `--source winget` only if confirmed here.)
- [ ] **Step 4: Check the prompt and skill plumbing.** `grep -n "PROMPT_START" tests/test_repo_invariants.py`;
  `grep -n "Copy this entire prompt" README.md docs/getting-started.md`; list `agent/skills`; confirm
  `python scripts/gen_agent_trees.py --check` is green and `--budget` prints headroom for `core.md` plus the
  largest addendum (record the byte headroom; Task 10 adds one table row).
- [ ] **Step 5: Write the note and commit.** `git add docs/superpowers/measurements/2026-10-05-plan7-preflight.md`
  `git commit -m "Plan 7 Task 0: pin the base and the three unknowns"`.

**Expected:** every answer recorded with its tier (MEASURED for runs, READ for source). Any "no" in (a) or (b)
changes Task 1 (add the missing handling to `neutral.py`, never to a rule module) or Task 5 (drop the shell
decoy row and say so in `ADAPTING.md`).

---

### Task 1: The agent-neutral engine entry

**Files:**
- Create: `tools/skyguard/neutral.py`, `tests/test_skyguard_neutral.py`
- Modify: `tools/skyguard/run.py` (dispatch only)

**Interfaces:**
- `neutral.parse(raw: str) -> Request` raises `neutral.BadRequest(str)`.
- `neutral.render(v: Verdict) -> str` (one line, ASCII JSON).
- `neutral.handle(raw, project, roots_fn, audit) -> (text, Verdict)`; `text` = `"SKYGUARD-OK\n" + json + "\n"`.
- CLI: `python tools/skyguard/run.py neutral pre-tool --root R --root-win R --project R [--invocation I --telemetry D]`;
  stdin = request JSON; stdout = marker + verdict JSON, exit 0 whenever the engine *decided* (including a deny
  for a bad request); **exit 1 with nothing on stdout** on any internal failure (so an adapter can tell
  "decided" from "broke", exactly as the Codex wrappers do with the same marker).
- Request JSON (spec §5): `v` (=1), `event` (="pre_tool"), `kind` (`shell|write|mcp|other`), plus optional
  `agent`, `shell` (`bash|powershell|null`), `command`, `paths[]`, `ops[]` (parallel to `paths`; default
  `"update"`, which asks the engine for a backup), `mcp_tool`, `mcp_args`, `cwd`, `tool_name`, `session_id`.
  **Unknown keys are refused** (a typo such as `path` for `paths` is the likeliest adapter bug and must be
  loud).

- [ ] **Step 1: Write the failing tests** (`tests/test_skyguard_neutral.py`):

```python
"""The neutral engine entry: strict request parsing, one verdict out, failures are denials."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import neutral  # noqa: E402
from skyguard.audit import Audit  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402


def req(**kw):
    d = {"v": 1, "event": "pre_tool", "kind": "write", "paths": ["/x/Data/Decoy.esp"]}
    d.update(kw)
    return json.dumps(d)


GOOD = [
    req(),
    req(kind="shell", paths=[], command="echo hi", shell=None),
    req(kind="shell", paths=[], command="Get-Date", shell="powershell"),
    req(kind="other", paths=[], tool_name="web_fetch"),
    req(kind="mcp", paths=[], mcp_tool="mcp__x__y", mcp_args="{}"),
    req(ops=["add"]),
]

BAD = {  # row -> (request text, fragment the refusal must name)
    "not-json": ("not json", "not JSON"),
    "array": ("[]", "JSON object"),
    "bom-less-empty": ("", "not JSON"),
    "unknown-key": (req(path="/x"), "unknown field"),
    "wrong-version": (req(v=2), "v must be 1"),
    "bool-version": (req(v=True), "v must be 1"),
    "string-version": (req(v="1"), "v must be 1"),
    "wrong-event": (req(event="post_tool"), "event must be pre_tool"),
    "bad-kind": (req(kind="file"), "kind must be"),
    "bad-shell": (req(kind="shell", shell="zsh", paths=[]), "shell must be"),
    "shell-on-write": (req(shell="bash"), "shell is only for kind shell"),
    "paths-not-list": (req(paths="/x"), "paths must be a list"),
    "path-not-string": (req(paths=[3]), "paths must be a list of non-empty strings"),
    "empty-path": (req(paths=[""]), "paths must be a list of non-empty strings"),
    "ops-length": (req(ops=["add", "add"]), "ops must be parallel"),
    "bad-op": (req(ops=["rewrite"]), "ops must be parallel"),
    "command-not-string": (req(kind="shell", paths=[], command=7), "command must be a string"),
}


@pytest.mark.parametrize("text", GOOD)
def test_valid_requests_parse(text):
    assert neutral.parse(text).event == "pre_tool"


@pytest.mark.parametrize("row", sorted(BAD))
def test_malformed_requests_are_refused_with_the_reason(row):
    text, fragment = BAD[row]
    with pytest.raises(neutral.BadRequest) as e:
        neutral.parse(text)
    assert fragment in str(e.value), f"{row}: refusal was {e.value!r}"


def test_ops_default_to_update_so_the_engine_takes_a_backup():
    assert neutral.parse(req(paths=["/a", "/b"])).ops == ("update", "update")


def test_nul_and_bom_are_tolerated_like_the_other_adapters():
    assert neutral.parse("\ufeff" + req().replace("{", "{\x00", 1)).kind == "write"


def test_render_is_ascii_one_line_and_total():
    v = Verdict("deny", "caf\u00e9 \u2013 no", "", "evaluated", "write.DENY_PLUGIN")
    out = neutral.render(v)
    out.encode("ascii")
    assert "\n" not in out and json.loads(out)["decision"] == "deny"
    bad = neutral.render(Verdict("maybe", "r"))
    assert json.loads(bad)["decision"] == "deny" and json.loads(bad)["status"] == "failed"


def test_handle_denies_a_decoy_plugin_write(tmp_path):
    text, v = neutral.handle(req(paths=[str(tmp_path / "Data" / "Decoy.esp")]), str(tmp_path),
                             lambda: None, Audit(str(tmp_path)))
    assert text.startswith("SKYGUARD-OK\n") and v.decision == "deny"
    assert json.loads(text.split("\n", 1)[1])["code"].startswith("write.")


def test_handle_turns_a_bad_request_into_a_failed_deny_never_a_raise(tmp_path):
    text, v = neutral.handle("{", str(tmp_path), lambda: None, Audit(str(tmp_path)))
    assert v.decision == "deny" and v.status == "failed" and v.code == "neutral.BAD_REQUEST"
    assert "NOTHING" in v.reason


def test_a_write_request_without_paths_is_denied_by_the_engine_not_allowed(tmp_path):
    _, v = neutral.handle(req(paths=[]), str(tmp_path), lambda: None, Audit(str(tmp_path)))
    assert v.decision == "deny" and "checked NOTHING" in v.reason


# ---- through the real CLI -------------------------------------------------------------------------
def _cli(root, stdin: bytes, *extra):
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "skyguard" / "run.py"), "neutral", "pre-tool",
         "--root", str(root), "--root-win", str(root), "--project", str(root), *extra],
        input=stdin, capture_output=True, timeout=60)


def test_cli_decoy_write_is_denied_with_marker_and_exit_zero(tmp_path):
    cp = _cli(tmp_path, req(paths=[str(tmp_path / "Data" / "Decoy.esp")]).encode())
    assert cp.returncode == 0 and cp.stdout.startswith(b"SKYGUARD-OK\n")
    body = json.loads(cp.stdout.split(b"\n", 1)[1])
    assert body["v"] == 1 and body["decision"] == "deny"


def test_cli_benign_write_is_allowed(tmp_path):
    cp = _cli(tmp_path, req(paths=[str(tmp_path / "notes.txt")]).encode())
    assert json.loads(cp.stdout.split(b"\n", 1)[1])["decision"] == "allow"


def test_cli_rejects_a_role_other_than_pre_tool_with_exit_1_and_no_stdout(tmp_path):
    cp = subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "run.py"), "neutral",
                         "protect-bash", "--project", str(tmp_path)], input=b"{}", capture_output=True, timeout=60)
    assert cp.returncode == 1 and cp.stdout == b""


def test_cli_writes_telemetry_when_asked(tmp_path):
    hb = tmp_path / "hb"
    _cli(tmp_path, req(paths=[str(tmp_path / "n.txt")]).encode(), "--invocation", "1.1-a", "--telemetry", str(hb))
    assert (hb / "pre-tool.evaluated").is_file()


def test_the_engine_still_parses_on_python_3_8():
    import ast
    src = (REPO / "tools" / "skyguard" / "neutral.py").read_text(encoding="utf-8")
    ast.parse(src, feature_version=(3, 8))
```

- [ ] **Step 2: Run to red.** `python -m pytest tests/test_skyguard_neutral.py -q -p no:cacheprovider`
  Expected: collection error `cannot import name 'neutral'`.
- [ ] **Step 3: Implement `tools/skyguard/neutral.py`:**

```python
"""The agent-neutral engine entry: spec section 5's request/verdict contract over stdin/stdout.

An adapter for ANY agent (written in any language, living outside this package) builds the request below,
runs `run.py neutral pre-tool`, and renders the verdict as its agent's hard block. See ADAPTING.md.

Strictness is the point: an adapter that mistypes a field must be told, loudly, not silently allowed.
A request that cannot be understood becomes a DENY with status=failed (the toolkit checked nothing), never
an exception and never an allow.
"""
from __future__ import annotations

import json
from typing import Tuple

from . import codes, engine
from .request import Request
from .verdict import DECISIONS, STATUSES, Verdict

OK = "SKYGUARD-OK"
KINDS = ("shell", "write", "mcp", "other")
SHELLS = ("bash", "powershell")
OPS = ("add", "update", "delete", "move_from", "move_to", "write", "edit")
FIELDS = frozenset(("v", "agent", "event", "kind", "shell", "command", "paths", "ops", "mcp_tool",
                    "mcp_args", "cwd", "tool_name", "session_id"))
BAD = ("GUARD INERT: the adapter's request was refused ({}), so the toolkit checked NOTHING. "
       "This is a refusal, not a decision.")


class BadRequest(ValueError):
    """The request is not a valid contract request; the message names the field."""


def _str(d: dict, key: str) -> str:
    v = d.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise BadRequest("{} must be a string".format(key))
    return v


def parse(raw: str) -> Request:
    text = raw.replace("\x00", "")
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        d = json.loads(text)
    except ValueError:
        raise BadRequest("request is not JSON")
    if not isinstance(d, dict):
        raise BadRequest("request is not a JSON object")
    extra = sorted(k for k in d if k not in FIELDS)
    if extra:
        raise BadRequest("unknown field {}".format(", ".join(extra)))
    if isinstance(d.get("v"), bool) or d.get("v") != 1:
        raise BadRequest("v must be 1")
    if d.get("event") != "pre_tool":
        raise BadRequest("event must be pre_tool")
    kind = d.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise BadRequest("kind must be one of {}".format(", ".join(KINDS)))
    shell = d.get("shell")
    if shell is not None:
        if kind != "shell":
            raise BadRequest("shell is only for kind shell")
        if not isinstance(shell, str) or shell not in SHELLS:
            raise BadRequest("shell must be one of {} or null".format(", ".join(SHELLS)))
    paths = d.get("paths", [])
    if not isinstance(paths, list):
        raise BadRequest("paths must be a list")
    if not all(isinstance(p, str) and p for p in paths):
        raise BadRequest("paths must be a list of non-empty strings")
    ops = d.get("ops")
    if ops is None:
        ops = ["update"] * len(paths)
    if not (isinstance(ops, list) and len(ops) == len(paths)
            and all(isinstance(o, str) and o in OPS for o in ops)):
        raise BadRequest("ops must be parallel to paths, each one of {}".format(", ".join(OPS)))
    return Request(agent=_str(d, "agent") or "neutral", event="pre_tool", kind=kind, shell=shell,
                   command=_str(d, "command"), paths=tuple(paths), ops=tuple(ops),
                   mcp_tool=_str(d, "mcp_tool"), mcp_args=_str(d, "mcp_args"), cwd=_str(d, "cwd"),
                   tool_name=_str(d, "tool_name") or "neutral", session_id=_str(d, "session_id"))


def render(v: Verdict) -> str:
    if v.decision not in DECISIONS:
        v = Verdict("deny", "GUARD INERT: the engine produced an unknown decision; refusing.",
                    status="failed", code="neutral.BAD_DECISION")
    status = v.status if v.status in STATUSES else "failed"
    return json.dumps({"v": 1, "decision": v.decision, "reason": v.reason, "context": v.context,
                       "status": status, "code": v.code}, ensure_ascii=True, separators=(",", ":"))


def handle(raw: str, project: str, roots_fn, audit) -> Tuple[str, Verdict]:
    try:
        req = parse(raw)
    except BadRequest as e:
        audit.log("neutral", "REFUSE", str(e))
        v = Verdict("deny", BAD.format(e), status="failed", code="neutral.BAD_REQUEST")
    else:
        v = codes.classify(engine.pre_tool(req, project, roots_fn, audit))
    return OK + "\n" + render(v) + "\n", v
```

- [ ] **Step 4: Wire `run.py`** (dispatch only; re-read the merged `run.py` first and keep its style). Add
  `"neutral"` to the agent choices without giving it an adapter module, handle it **before** the
  `(a.agent == "codex") != (a.role in CODEX_ROLES)` check, and reuse the engine's own roots helper:

```python
# in run.py, beside _codex():
def _neutral(a, raw: str) -> int:
    """Role pre-tool for an adapter this repo has no module for (ADAPTING.md). Output is SKYGUARD-OK + a
    verdict line; the adapter owns the failure deny, exactly as the Codex wrappers do."""
    from skyguard import neutral
    if a.role != "pre-tool":
        return 1
    audit = Audit(a.project)

    def roots_fn():
        env = _declared_env(a)
        if env is None:
            raise engine.RootsUnavailable(ENV_UNREADABLE)
        return paths.roots(a.root, a.root_win, env)

    text, v = neutral.handle(raw, a.project, roots_fn, audit)
    _telemetry(a, v)
    _emit(text)
    return 0
```
  and in `main`, `ap.add_argument("agent", choices=sorted(ADAPTERS) + ["neutral"])`, then after reading
  `raw`: `if a.agent == "neutral": return _neutral(a, raw)`. (The role choice list already contains
  `pre-tool`.) `ENV_UNREADABLE` is the Claude wording; if Task 0 shows the merged base moved it, import the
  neutral-safe text from wherever it now lives.
- [ ] **Step 5: Green + mutation spot-check.** Run the test file: all pass. Then prove two checks can fail
  (restore after, verify with `git diff --stat`): (a) change `if isinstance(d.get("v"), bool) or d.get("v") != 1`
  to `if d.get("v") != 1` → `test_bool_version` row goes red; (b) make `handle` return `("", None)` on
  `BadRequest` → `test_handle_turns_a_bad_request_into_a_failed_deny` goes red.
- [ ] **Step 6: Existing engine tests untouched.** `python -m pytest tests/test_skyguard_engine.py
  tests/test_skyguard_core.py tests/test_codex_entry.py -q -p no:cacheprovider` stays green (the dispatch edit
  must not change either existing path).
- [ ] **Step 7: Commit.** `git add tools/skyguard/neutral.py tools/skyguard/run.py tests/test_skyguard_neutral.py`
  `git commit -m "skyguard: agent-neutral engine entry (run.py neutral pre-tool), strict request, deny on bad input"`.

**Expected:** ~30 test items green; the CLI answers `SKYGUARD-OK` + one JSON line.

---

### Task 2: Adapter template and the two worked-example slot sets

**Files:**
- Create: `tools/adapting/adapter_template.py`, `tools/adapting/examples/json_deny.slots.py`,
  `tools/adapting/examples/exit2.slots.py`, `tests/test_adapter_template.py`

**Interfaces:**
- The template's SLOTS block (between `# SLOTS-BEGIN` and `# SLOTS-END`) holds: `AGENT_NAME`, `ASK_PREFIX`,
  `STATIC_BLOCK` (a *literal* tuple `(stdout_text, exit_code, stderr_text)`), `map_payload(raw: bytes) -> dict`,
  `render_block(reason) -> tuple`, `render_allow(context) -> tuple`, `render_ask = None`.
- **Unfilled, the template blocks every call** (exit 2, stderr text). A new adapter is closed before it is
  correct.
- Everything outside the block is the machinery and must stay byte-identical (Task 3 enforces).

- [ ] **Step 1: Write the template** (`tools/adapting/adapter_template.py`; ASCII only, Python 3.8):

```python
#!/usr/bin/env python3
"""Adapter template (ADAPTING.md, section 6). tools/adapting/adapter_template.py

Copy to tools/adapters/<name>/adapter.py and edit ONLY the block between SLOTS-BEGIN and SLOTS-END.
Everything else is the fail-closed machinery; tools/adapter_conformance.py checks that it is unchanged.

What it does, per tool call: read the agent's payload from stdin -> map_payload() -> run the toolkit
engine (`run.py neutral pre-tool`) -> check the answer -> render the verdict as THIS agent's hard block.
If anything at all goes wrong it renders the same hard block, because an agent that treats a failed hook
as "allow" would otherwise run the command (that is how Codex behaves: spec E1, E17).
Python >= 3.8, standard library only.
"""
import json
import os
import re
import subprocess
import sys
import time

# SLOTS-BEGIN
AGENT_NAME = "unnamed"   # letters, digits, dot, dash: names the heartbeat folder

# What this agent obeys as a HARD block, as a literal: (stdout text, exit code, stderr text).
# MEASURE that your agent really stops the call on this shape. Unfilled, this is exit 2 + stderr, which
# many agents honour, so an unfilled adapter refuses every call.
STATIC_BLOCK = ("", 2, "GUARD INERT: this adapter is not set up, so every call is refused.")

ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")


def map_payload(raw):
    """The agent's payload (bytes) -> the request dict of ADAPTING.md section 4."""
    raise NotImplementedError("fill map_payload")


def render_block(reason):
    """-> (stdout text, exit code, stderr text) that your agent honours as a hard block, with reason shown."""
    raise NotImplementedError("fill render_block")


def render_allow(context):
    """-> (stdout text, exit code, stderr text) that lets the call run; context (maybe '') is advice."""
    raise NotImplementedError("fill render_allow")


render_ask = None   # set to a function(reason) only after measuring that your agent pauses for the user
# SLOTS-END

ENGINE_BUDGET_S = 20      # the engine's own watchdog (SKYGUARD_ENGINE_TIMEOUT)
ADAPTER_TIMEOUT_S = 25    # this adapter's deadline. Your agent's hook timeout MUST be longer than this.
MARKER = "SKYGUARD-OK\n"
_UNSAFE = re.compile(r"[^A-Za-z0-9 ._:/()=,+-]")


def _root():
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isfile(os.path.join(d, "tools", "skyguard", "run.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def _hb_dir(root):
    return os.path.join(root, ".claude", "backups", ".hook-heartbeat", "adapted",
                        re.sub(r"[^A-Za-z0-9._-]", "_", AGENT_NAME))


def _hb(root, stage, inv, detail=""):
    """Best-effort heartbeat; never decides anything."""
    try:
        d = _hb_dir(root)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pre-tool." + stage), "w") as f:
            f.write("{:.6f} {} {}\n".format(time.time(), inv, detail))
    except Exception:
        pass


def _engine(req_json, root, inv):
    run_py = os.path.join(root, "tools", "skyguard", "run.py")
    env = dict(os.environ)
    env.setdefault("SKYGUARD_ENGINE_TIMEOUT", str(ENGINE_BUDGET_S))
    cp = subprocess.run(
        [sys.executable, run_py, "neutral", "pre-tool", "--root", root, "--root-win", root,
         "--project", root, "--invocation", inv, "--telemetry", _hb_dir(root)],
        input=req_json.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=float(os.environ.get("SKYGUARD_ADAPTER_TIMEOUT_S", ADAPTER_TIMEOUT_S)), env=env)
    if cp.returncode != 0:
        raise RuntimeError("engine exited {}".format(cp.returncode))
    text = cp.stdout.decode("utf-8")
    if not text.startswith(MARKER):
        raise RuntimeError("engine answered without the marker")
    v = json.loads(text[len(MARKER):])
    if not isinstance(v, dict) or v.get("v") != 1 or v.get("decision") not in ("allow", "ask", "deny"):
        raise RuntimeError("engine verdict malformed")
    return v


def _checked(t):
    ok = (isinstance(t, tuple) and len(t) == 3 and isinstance(t[0], str)
          and isinstance(t[1], int) and not isinstance(t[1], bool) and isinstance(t[2], str))
    if not ok:
        raise RuntimeError("a render slot did not return (stdout text, exit code, stderr text)")
    return t


def _render(v):
    decision = v["decision"]
    reason = str(v.get("reason", ""))
    context = str(v.get("context", ""))
    if decision == "deny":
        return _checked(render_block(reason))
    if decision == "ask":
        if render_ask is not None:
            return _checked(render_ask(reason))
        return _checked(render_block(ASK_PREFIX + reason))
    return _checked(render_allow(context))


def _block_for_failure(why):
    reason = ("GUARD INERT: the adapter could not get a verdict from the toolkit engine ({}). NOTHING was "
              "checked: this is a refusal, not a decision. Tell the user.").format(_UNSAFE.sub("", why)[:120])
    try:
        return _checked(render_block(reason))
    except BaseException:
        return STATIC_BLOCK


def _emit(out, code, err):
    sys.stdout.write(out)
    sys.stdout.flush()
    if err:
        sys.stderr.write(err)
        sys.stderr.flush()
    sys.exit(code)


def main():
    root = _root()
    inv = "1.{}-{}".format(os.getpid(), int(time.time() * 1000) % 100000)
    try:
        _hb(root, "invoked", inv)
        raw = sys.stdin.buffer.read()
        if not root:
            raise RuntimeError("cannot locate the toolkit root")
        if not raw.strip():
            raise RuntimeError("empty payload")
        req = map_payload(raw)
        if not isinstance(req, dict):
            raise RuntimeError("map_payload did not return a dict")
        v = _engine(json.dumps(req), root, inv)
        out, code, err = _render(v)
        _hb(root, "emitted", inv, v["decision"])
    except BaseException as e:
        out, code, err = _block_for_failure(e.__class__.__name__ + " " + str(e))
        _hb(root, "failed", inv, e.__class__.__name__)
    _emit(out, code, err)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the two worked-example slot files** (each is *only* a SLOTS block; `json` is imported by
  the template). `tools/adapting/examples/json_deny.slots.py` models Codex's mechanism (JSON deny, exit 0) for a
  toy agent whose native payload is `{"toy_event","tool","args","cwd"}`:

```python
# SLOTS-BEGIN
AGENT_NAME = "toy-json"
STATIC_BLOCK = ('{"toy_decision":"block","reason":"GUARD INERT: adapter failed"}', 0, "")
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")


def map_payload(raw):
    d = json.loads(raw.decode("utf-8"))
    tool, args = d["tool"], d.get("args", {})
    base = {"v": 1, "agent": "toy-json", "event": "pre_tool", "cwd": d.get("cwd", "")}
    if tool == "write_file":
        return dict(base, kind="write", paths=[args["path"]], ops=["update"])
    if tool == "run_shell":
        return dict(base, kind="shell", shell=None, command=args["cmd"])
    return dict(base, kind="other", tool_name=tool)


def render_block(reason):
    return (json.dumps({"toy_decision": "block", "reason": reason}), 0, "")


def render_allow(context):
    return (json.dumps({"toy_decision": "allow", "context": context}), 0, "")


render_ask = None
# SLOTS-END
```
  and `exit2.slots.py` models an exit-code agent with a *different* payload shape
  (`{"hook":"pre","call":{"name":"edit"|"sh","file"|"cmdline":...},"cwd":...}`), blocks with exit 2 and the
  reason on stderr, and allows with exit 0 / empty stdout:

```python
# SLOTS-BEGIN
AGENT_NAME = "toy-exit2"
STATIC_BLOCK = ("", 2, "GUARD INERT: adapter failed")
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")


def map_payload(raw):
    d = json.loads(raw.decode("utf-8"))
    call = d["call"]
    base = {"v": 1, "agent": "toy-exit2", "event": "pre_tool", "cwd": d.get("cwd", "")}
    if call["name"] == "edit":
        return dict(base, kind="write", paths=[call["file"]], ops=["update"])
    if call["name"] == "sh":
        return dict(base, kind="shell", shell=None, command=call["cmdline"])
    return dict(base, kind="other", tool_name=call["name"])


def render_block(reason):
    return ("", 2, reason)


def render_allow(context):
    return ("", 0, "")


render_ask = None
# SLOTS-END
```

- [ ] **Step 3: Tests** (`tests/test_adapter_template.py`). These need `compose` from Task 3, so write the
  template-only ones now and finish this file in Task 3 Step 4; the ones that need only the template:

```python
"""The adapter template itself: parses on 3.8, is closed when unfilled, markers are unique."""
from __future__ import annotations

import ast
import subprocess
import sys

from conftest import REPO

TPL = REPO / "tools" / "adapting" / "adapter_template.py"


def test_template_and_examples_parse_on_python_3_8_and_are_ascii():
    for p in [TPL, *sorted((REPO / "tools" / "adapting" / "examples").glob("*.py"))]:
        text = p.read_bytes().decode("ascii")          # raises on any non-ASCII byte
        ast.parse(text, feature_version=(3, 8))


def test_slot_markers_appear_exactly_once_in_the_template_and_every_example():
    for p in [TPL, *sorted((REPO / "tools" / "adapting" / "examples").glob("*.slots.py"))]:
        t = p.read_text(encoding="ascii")
        assert t.count("# SLOTS-BEGIN") == 1 and t.count("# SLOTS-END") == 1, p.name
        assert t.index("# SLOTS-BEGIN") < t.index("# SLOTS-END"), p.name


def test_an_unfilled_template_refuses_every_call(tmp_path):
    """Closed before correct: no slot is filled, so even a benign call is blocked (exit 2 + stderr)."""
    root = tmp_path / "inst"
    (root / "tools" / "adapters" / "x").mkdir(parents=True)
    (root / "tools" / "skyguard").mkdir(parents=True)
    (root / "tools" / "skyguard" / "run.py").write_text("print('would allow')\n")
    adapter = root / "tools" / "adapters" / "x" / "adapter.py"
    adapter.write_text(TPL.read_text(encoding="ascii"), encoding="ascii", newline="\n")
    cp = subprocess.run([sys.executable, str(adapter)], input=b'{"anything": 1}', capture_output=True, timeout=60)
    assert cp.returncode == 2 and b"GUARD INERT" in cp.stderr and cp.stdout == b""
```

- [ ] **Step 4: Run.** `python -m pytest tests/test_adapter_template.py -q -p no:cacheprovider` → 3 pass.
- [ ] **Step 5: Commit.** `git add tools/adapting tests/test_adapter_template.py`
  `git commit -m "adapting: fail-closed adapter template, slot examples for a JSON-deny and an exit-code agent"`.

---

### Task 3: The conformance tool

**Files:**
- Create: `tools/adapter_conformance.py`
- Modify: `tests/test_adapter_template.py` (append the composed-template tests)

**Interfaces** (all used by Task 5):
- `compose(template_text, slots_text) -> str` replaces the template's SLOTS region with the one in `slots_text`.
- `outside_hash(text) -> str` sha256 of everything outside the region, CRLF folded to LF.
- `make_install(adapter_dir: Path, dest: Path) -> Path` copies `tools/skyguard` and the adapter folder to
  `dest/tools/adapters/<name>/`, returns `dest`.
- `run_conformance(adapter_dir, profile, cases, command=None, port=False, repo=REPO) -> list[Row]`;
  `Row = (id, ok, why)`.
- CLI `python tools/adapter_conformance.py run --adapter-dir D --profile P.json --cases C.json [--command
  "node {DIR}/adapter.js"] [--port]` prints `PASS|FAIL <id>: <why>` per row and a final line
  `CONFORMANCE PASS (n/n)` or `CONFORMANCE FAIL (k of n)`; exit 0/1. `{DIR}` and `{PY}` are substituted in
  `--command`; the default is `{PY} {DIR}/adapter.py`.
- **Profile** (`profile.json`): `{"block": {"exit": int, "stdout_regex": str|null, "stderr_regex": str|null},
  "allow": {"exit": int}}`. A profile in which a block cannot be told from an allow is invalid.
- **Cases** (`cases.json`): `{"cases": [{"id", "expect": "block"|"allow", "payload": <object|string> |
  "payload_hex": "..."}]}` with native payloads **captured from the real agent**; the placeholder `{ROOT}` in a
  payload is replaced by the scratch install's root (forward slashes). **Required ids:** `decoy-plugin-write`,
  `benign-write`, `empty-payload`, `garbage-payload`; if the agent has a shell tool also `decoy-plugin-shell`,
  `benign-shell` (both or neither).
- **Rows the tool adds itself:** `template-identical` (the non-slot text hashes equal to the shipped
  template; skipped with a printed `UNVERIFIED STRUCTURE` note under `--port` for a non-Python adapter), and one
  `fault:<name>` row per engine fault below. Each fault row replaces the **scratch** `run.py`, feeds the
  `benign-write` payload, and requires the agent's block shape with `GUARD INERT` visible in stdout or stderr.

Engine faults (`FAULTS`): `engine-crash` (exits 1), `engine-silent` (prints nothing, exit 0), `engine-garbage`
(prints `hello`), `engine-marker-garbage` (marker then non-JSON), `engine-wrong-version` (marker + `{"v":2,
"decision":"allow"}`), `engine-allow-but-exit-1` (marker + a valid allow, then exit 1), `engine-hang`
(sleeps 60 s; run with `SKYGUARD_ADAPTER_TIMEOUT_S=2`; a non-Python port must honour that variable), and
`engine-missing` (the file is deleted).

- [ ] **Step 1: Write the tool.**

```python
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
PROBE_TIMEOUT_S = float(os.environ.get("SKYGUARD_CONF_TIMEOUT_S", "15"))
# The engine's plugin-write refusal names "plugin/archive" (write_rules.DENY_PLUGIN, pinned in Task 0).
VISIBLE = re.compile(r"(?i)plugin|archive")
ALLOW_V1 = 'print("SKYGUARD-OK"); print(\'{"v":1,"decision":"allow","reason":"","context":"","status":"evaluated","code":"allow"}\')'
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


def make_install(adapter_dir: Path, dest: Path) -> Path:
    dest = Path(dest)
    shutil.copytree(REPO / "tools" / "skyguard", dest / "tools" / "skyguard",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(adapter_dir, dest / "tools" / "adapters" / Path(adapter_dir).name,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".claude").mkdir(exist_ok=True)
    return dest


# ---- shapes ---------------------------------------------------------------------------------------
def _profile_problem(p: dict) -> Optional[str]:
    try:
        b, a = p["block"], p["allow"]
        if not isinstance(b["exit"], int) or not isinstance(a["exit"], int):
            return "exit codes must be integers"
        if not (b.get("stdout_regex") or b.get("stderr_regex")) and b["exit"] == a["exit"]:
            return "block cannot be told from allow: give a different exit code or a regex"
    except (KeyError, TypeError):
        return "profile needs block.exit and allow.exit"
    return None


def is_block(profile: dict, out: str, code: Optional[int], err: str) -> bool:
    b = profile["block"]
    if code != b["exit"]:
        return False
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
    return text.replace("{ROOT}", root.as_posix()).encode("utf-8")


def _run(argv: list, data: bytes, env: dict) -> Tuple[str, Optional[int], str]:
    try:
        cp = subprocess.run(argv, input=data, capture_output=True, env=env, timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "", None, "TIMEOUT: the adapter did not answer within {}s".format(PROBE_TIMEOUT_S)
    except OSError as e:
        return "", None, "cannot start the adapter: {}".format(e)
    return cp.stdout.decode("utf-8", "replace"), cp.returncode, cp.stderr.decode("utf-8", "replace")


def run_conformance(adapter_dir, profile: dict, cases: dict, command: Optional[str] = None,
                    port: bool = False, repo: Path = REPO) -> List[Row]:
    rows: List[Row] = []
    adapter_dir = Path(adapter_dir)
    prob = _profile_problem(profile)
    if prob:
        return [Row("profile", False, prob)]
    by_id = {c.get("id"): c for c in cases.get("cases", [])}
    for rid in REQUIRED:
        if rid not in by_id:
            rows.append(Row("case:" + rid, False, "required case missing"))
    if (SHELL_PAIR[0] in by_id) != (SHELL_PAIR[1] in by_id):
        rows.append(Row("case:shell-pair", False, "give both decoy-plugin-shell and benign-shell, or neither"))
    if rows:
        return rows
    entry = adapter_dir / "adapter.py"
    if entry.is_file() and not port:
        same = outside_hash(entry.read_text(encoding="utf-8")) == outside_hash(TEMPLATE.read_text(encoding="utf-8"))
        rows.append(Row("template-identical", same,
                        "machinery identical to the shipped template" if same else
                        "ADAPTER DIVERGES FROM THE TEMPLATE outside the SLOTS block: adapters only translate"))
    else:
        print("UNVERIFIED STRUCTURE: this adapter is a port; the template-identical check was skipped", file=sys.stderr)

    with tempfile.TemporaryDirectory() as td:
        root = make_install(adapter_dir, Path(td) / "install")
        sub = {"{DIR}": str(root / "tools" / "adapters" / adapter_dir.name), "{PY}": sys.executable}
        cmd = command or "{PY} {DIR}/adapter.py"
        for k, v in sub.items():
            cmd = cmd.replace(k, v)
        argv = shlex.split(cmd, posix=(os.name != "nt"))
        env = dict(os.environ)
        env.pop("SKYGUARD_ADAPTER_TIMEOUT_S", None)
        for cid, case in by_id.items():
            out, code, err = _run(argv, _payload_bytes(case, root), env)
            want = case.get("expect")
            if want == "block":
                ok = is_block(profile, out, code, err)
                if ok and cid.startswith("decoy-"):
                    ok = bool(VISIBLE.search(out + err))
                why = "blocked, reason visible" if ok else "NOT BLOCKED (exit {}, stdout {!r}, stderr {!r})".format(
                    code, out[:80], err[:80])
            elif want == "allow":
                ok = is_allow(profile, out, code, err)
                why = "allowed" if ok else "NOT ALLOWED (exit {}, stdout {!r}, stderr {!r})".format(code, out[:80], err[:80])
            else:
                ok, why = False, "expect must be block or allow"
            rows.append(Row("case:" + cid, ok, why))
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
```

- [ ] **Step 2: Append to `tests/test_adapter_template.py`** the checks that need `compose`:

```python
import importlib.util
import json

EX = REPO / "tools" / "adapting" / "examples"


def _conf():
    spec = importlib.util.spec_from_file_location("adapter_conformance", REPO / "tools" / "adapter_conformance.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_compose_replaces_only_the_slots_region():
    c = _conf()
    t = TPL.read_text(encoding="ascii")
    s = (EX / "json_deny.slots.py").read_text(encoding="ascii")
    out = c.compose(t, s)
    assert c.outside_hash(out) == c.outside_hash(t)
    assert 'AGENT_NAME = "toy-json"' in out and out.count("# SLOTS-BEGIN") == 1
    ast.parse(out, feature_version=(3, 8))


def test_outside_hash_sees_a_one_byte_change_in_the_machinery_but_not_in_the_slots():
    c = _conf()
    t = TPL.read_text(encoding="ascii")
    assert c.outside_hash(t.replace("ADAPTER_TIMEOUT_S = 25", "ADAPTER_TIMEOUT_S = 26")) != c.outside_hash(t)
    assert c.outside_hash(t.replace('AGENT_NAME = "unnamed"', 'AGENT_NAME = "other"')) == c.outside_hash(t)


def test_hostile_slots_cannot_exit_zero_or_print_an_allow(tmp_path):
    """A slot that calls sys.exit(0) must still end in the block: the machinery catches BaseException."""
    c = _conf()
    hostile = (EX / "json_deny.slots.py").read_text(encoding="ascii").replace(
        "d = json.loads(raw.decode(\"utf-8\"))\n    tool,", "sys.exit(0)\n    tool,", 1)
    assert "sys.exit(0)" in hostile
    d = tmp_path / "toy"
    d.mkdir()
    (d / "adapter.py").write_text(c.compose(TPL.read_text(encoding="ascii"), hostile), encoding="ascii", newline="\n")
    root = c.make_install(d, tmp_path / "inst")
    cp = subprocess.run([sys.executable, str(root / "tools" / "adapters" / "toy" / "adapter.py")],
                        input=b'{"tool":"write_file","args":{"path":"x"}}', capture_output=True, timeout=60)
    assert json.loads(cp.stdout)["toy_decision"] == "block" and b"GUARD INERT" in cp.stdout


def test_ask_becomes_a_block_unless_the_author_set_render_ask(tmp_path):
    """The engine cannot be made to emit ask by a conformance case, so the template's _render is unit-tested."""
    c = _conf()
    text = c.compose(TPL.read_text(encoding="ascii"), (EX / "json_deny.slots.py").read_text(encoding="ascii"))
    ns = {"__name__": "adapter_under_test"}
    exec(compile(text, "adapter.py", "exec"), ns)
    out, code, err = ns["_render"]({"decision": "ask", "reason": "delete save?", "context": ""})
    assert json.loads(out)["toy_decision"] == "block" and "NEEDS YOUR CONFIRMATION" in out and "delete save?" in out
    ns["render_ask"] = lambda reason: ("ASKED " + reason, 0, "")
    assert ns["_render"]({"decision": "ask", "reason": "r", "context": ""})[0] == "ASKED r"
```
  (Using `exec` of the composed text is deliberate: it tests the real machinery's `_render` with real slots.)

- [ ] **Step 3: Run** `python -m pytest tests/test_adapter_template.py -q -p no:cacheprovider` → all pass.
- [ ] **Step 4: Commit.** `git add tools/adapter_conformance.py tests/test_adapter_template.py`
  `git commit -m "adapting: conformance tool (payload cases, engine fault matrix, template identity) + template tests"`.

---

### Task 4: Capability report format and validator

**Files:**
- Create: `tools/adapting/capability-report.template.json`, `tests/test_capability_report.py`
- Modify: `tools/adapter_conformance.py` (add `report` subcommand and `derive_status`)

The fixed-format report an agent fills in `ADAPTING.md` §3 (spec §11 item 1). Every datum is
`{"value": ..., "tier": "MEASURED|READ|INFERRED|ASSUMED", "evidence": "<file or command>"}`.

**Template** (`capability-report.template.json`):

```json
{
  "v": 1,
  "agent": {"name": "", "version": "", "surface": ""},
  "instruction_file": {"name": "", "limit_bytes": null, "truncation_behaviour": ""},
  "skills_dir": {"path": ""},
  "pre_tool_hook": {
    "exists": null,
    "fires_on_this_surface": null,
    "can_block": null,
    "block_mechanism": "",
    "can_ask": null,
    "failed_hook_behaviour": "",
    "hook_timeout_s": null
  },
  "session_start": {"exists": null, "injects_context": null},
  "payload_capture": {"method": "", "files": []},
  "proof": {"conformance_passed": false, "live_canary_blocked": false, "canary_evidence": ""}
}
```
Every leaf except `proof.*`, `payload_capture.files`, and the `agent` block is wrapped as above when filled.
`failed_hook_behaviour` is one of `allows`, `blocks`, `unknown`. `block_mechanism` free text.

**Derived status** (`derive_status(report) -> str`), the *only* place a status string is produced:
- `"locally adapted, self-verified"` iff: `pre_tool_hook.fires_on_this_surface`, `can_block` both
  `true` with tier `MEASURED`; `failed_hook_behaviour` tier `MEASURED` (either `allows` or `blocks`); and
  `proof.conformance_passed` and `proof.live_canary_blocked` both true with a non-empty `canary_evidence`.
- otherwise `"partially supported: Layers 0-2"`.
The validator also fails a report whose `claimed_status` field (optional) is stronger than the derived one,
and any report with a missing leaf, a non-enum tier, or `INFERRED`/`ASSUMED` on a leaf that gates the status
while claiming the stronger status.

- [ ] **Step 1: Tests** (`tests/test_capability_report.py`): a fully measured report → derived
  `locally adapted, self-verified`; the same report with `can_block.tier` = `INFERRED` → `partially
  supported: Layers 0-2`; with `live_canary_blocked: false` → partially; with `claimed_status` =
  `locally adapted, self-verified` over a partial one → validator exit 1 naming `claimed_status`; a leaf
  missing → exit 1 naming the path; the unfilled template → partially supported (never an error: an unfilled
  report is simply the default). Include `test_the_template_has_every_leaf_the_validator_requires`
  (loads the template and asserts the validator's leaf list equals its keys: a drift tripwire).
- [ ] **Step 2: Implement** `derive_status`, `validate_report(report) -> list[str]` and CLI
  `python tools/adapter_conformance.py report FILE` (prints the derived status, then any problems; exit 1 on
  problems). Keep leaf-path list as one tuple constant `LEAVES` next to the template path.
- [ ] **Step 3: Run** `python -m pytest tests/test_capability_report.py tests/test_adapter_template.py -q -p no:cacheprovider`.
- [ ] **Step 4: Commit.** `git add tools/adapting/capability-report.template.json tools/adapter_conformance.py tests/test_capability_report.py`
  `git commit -m "adapting: capability report template; status is derived, never self-declared"`.

---

### Task 5: The toy agent and the CI test

**Files:**
- Create: `tests/toyagent/toy_agent.py`, `tests/toyagent/__init__.py` (empty), `tests/test_toy_agent.py`,
  `docs/superpowers/measurements/2026-10-05-plan7-adapter-latency.md`

**The toy agent** is a fake coding-agent runtime with two hook dialects and the **worst-case failure
semantics** (Codex's, spec E1/E17): it blocks a call only on its one native block shape, and treats a hook that
times out, crashes, exits non-zero in the wrong way, or prints garbage as *allow*.

- [ ] **Step 1: Write the runtime** (`tests/toyagent/toy_agent.py`):

```python
"""A deliberately tiny fake coding agent: the test double for ADAPTING.md.

    toy_agent.py --profile json-deny|exit2 --hook '<json argv list>' --script calls.json --cwd DIR
                 [--hook-timeout S]

For each call in the script ({"op":"write","path":..} or {"op":"shell","cmd":..}) it builds ITS OWN native
payload, runs the hook with that payload on stdin, and executes the call unless the hook blocked it in the
one way this profile honours. Everything else -- timeout, crash, wrong exit code, garbage -- lets the call
RUN (fail open), like the Codex CLI. Prints a JSON list of {op, blocked, executed, reason}.
"""
import argparse
import json
import os
import subprocess
import sys


def native(profile, call, cwd):
    if profile == "json-deny":
        if call["op"] == "write":
            return {"toy_event": "before_tool", "tool": "write_file", "args": {"path": call["path"], "content": "x"}, "cwd": cwd}
        return {"toy_event": "before_tool", "tool": "run_shell", "args": {"cmd": call["cmd"]}, "cwd": cwd}
    if call["op"] == "write":
        return {"hook": "pre", "call": {"name": "edit", "file": call["path"]}, "cwd": cwd}
    return {"hook": "pre", "call": {"name": "sh", "cmdline": call["cmd"]}, "cwd": cwd}


def verdict(profile, cp):
    """-> (blocked, reason). cp is None when the hook timed out or could not start: that RUNS the call."""
    if cp is None:
        return False, ""
    if profile == "json-deny":
        if cp.returncode != 0:
            return False, ""
        try:
            d = json.loads(cp.stdout.decode("utf-8"))
        except ValueError:
            return False, ""
        return (d.get("toy_decision") == "block", str(d.get("reason", "")))
    return (cp.returncode == 2, cp.stderr.decode("utf-8", "replace"))


def execute(call, cwd):
    if call["op"] == "write":
        os.makedirs(os.path.dirname(call["path"]), exist_ok=True)
        with open(call["path"], "w") as f:
            f.write("x")
    else:
        subprocess.run(call["cmd"], shell=True, cwd=cwd, capture_output=True, timeout=30)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["json-deny", "exit2"], required=True)
    ap.add_argument("--hook", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--hook-timeout", type=float, default=30.0)
    a = ap.parse_args(argv)
    hook = json.loads(a.hook)
    results = []
    for call in json.load(open(a.script)):
        data = json.dumps(native(a.profile, call, a.cwd)).encode("utf-8")
        try:
            cp = subprocess.run(hook, input=data, capture_output=True, timeout=a.hook_timeout, cwd=a.cwd)
        except (subprocess.TimeoutExpired, OSError):
            cp = None
        blocked, reason = verdict(a.profile, cp)
        if not blocked:
            execute(call, a.cwd)
        results.append({"op": call["op"], "blocked": blocked, "executed": not blocked, "reason": reason})
    print(json.dumps(results))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the test** (`tests/test_toy_agent.py`). Structure and the assertions that matter:

```python
"""ADAPTING.md, executed: a fake agent follows the document's steps and must end with a working block.

Steps mirrored from ADAPTING.md section 6: copy the template, fill the slots, run conformance, then run a
decoy .esp write through the AGENT (not through the adapter) and require that the file does not exist.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

TOY = REPO / "tests" / "toyagent" / "toy_agent.py"
EX = REPO / "tools" / "adapting" / "examples"
TPL = REPO / "tools" / "adapting" / "adapter_template.py"
PROFILES = {   # profile -> (slots file, adapter profile, native payload builders)
    "json-deny": ("json_deny.slots.py", {"block": {"exit": 0, "stdout_regex": "\"toy_decision\": ?\"block\"", "stderr_regex": None},
                                         "allow": {"exit": 0}}),
    "exit2": ("exit2.slots.py", {"block": {"exit": 2, "stdout_regex": None, "stderr_regex": None}, "allow": {"exit": 0}}),
}


def _conf():
    spec = importlib.util.spec_from_file_location("adapter_conformance", REPO / "tools" / "adapter_conformance.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


conf = _conf()


def cases_for(profile: str) -> dict:
    def n(op, **kw):
        if profile == "json-deny":
            return ({"toy_event": "before_tool", "tool": "write_file", "args": {"path": kw["path"], "content": "x"}, "cwd": "{ROOT}"}
                    if op == "write" else {"toy_event": "before_tool", "tool": "run_shell", "args": {"cmd": kw["cmd"]}, "cwd": "{ROOT}"})
        return ({"hook": "pre", "call": {"name": "edit", "file": kw["path"]}, "cwd": "{ROOT}"}
                if op == "write" else {"hook": "pre", "call": {"name": "sh", "cmdline": kw["cmd"]}, "cwd": "{ROOT}"})
    return {"cases": [
        {"id": "decoy-plugin-write", "expect": "block", "payload": n("write", path="{ROOT}/Data/Decoy.esp")},
        {"id": "benign-write", "expect": "allow", "payload": n("write", path="{ROOT}/notes.txt")},
        {"id": "decoy-plugin-shell", "expect": "block", "payload": n("shell", cmd="echo x > {ROOT}/Data/Decoy.esp")},
        {"id": "benign-shell", "expect": "allow", "payload": n("shell", cmd="echo hello")},
        {"id": "empty-payload", "expect": "block", "payload": ""},
        {"id": "garbage-payload", "expect": "block", "payload_hex": "6e6f74206a736f6e00ff"},
    ]}


def staged(tmp_path: Path, profile: str, template_text: str = None):
    slots = (EX / PROFILES[profile][0]).read_text(encoding="ascii")
    d = tmp_path / "src" / "toy"
    d.mkdir(parents=True)
    (d / "adapter.py").write_text(conf.compose(template_text or TPL.read_text(encoding="ascii"), slots),
                                  encoding="ascii", newline="\n")
    root = conf.make_install(d, tmp_path / "install")
    return root, root / "tools" / "adapters" / "toy" / "adapter.py"


def drive(root: Path, adapter: Path, profile: str, calls: list, hook_timeout: float = 30, env: dict = None):
    script = root / "calls.json"
    script.write_text(json.dumps(calls), encoding="utf-8")
    import os
    e = dict(os.environ, **(env or {}))
    cp = subprocess.run([sys.executable, str(TOY), "--profile", profile, "--hook", json.dumps([sys.executable, str(adapter)]),
                         "--script", str(script), "--cwd", str(root), "--hook-timeout", str(hook_timeout)],
                        capture_output=True, env=e, timeout=300)
    assert cp.returncode == 0, cp.stderr.decode()
    return json.loads(cp.stdout)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_conformance_passes_on_the_adapter_a_fake_agent_builds_from_the_document(tmp_path, profile):
    root, adapter = staged(tmp_path, profile)
    rows = conf.run_conformance(adapter.parent, PROFILES[profile][1], cases_for(profile))
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]
    assert {r.id for r in rows} >= {"template-identical", "case:decoy-plugin-write", "fault:engine-hang",
                                    "fault:engine-allow-but-exit-1", "fault:engine-missing"}


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_end_to_end_a_decoy_plugin_write_is_blocked_by_the_agent(tmp_path, profile):
    root, adapter = staged(tmp_path, profile)
    decoy, ok = root / "Data" / "Decoy.esp", root / "notes.txt"
    r = drive(root, adapter, profile, [{"op": "write", "path": str(decoy)}, {"op": "write", "path": str(ok)}])
    assert r[0]["blocked"] and not decoy.exists() and "plugin" in r[0]["reason"].lower() + r[0]["reason"]
    assert not r[1]["blocked"] and ok.exists()


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_end_to_end_a_shell_redirect_into_a_plugin_is_blocked(tmp_path, profile):
    root, adapter = staged(tmp_path, profile)
    decoy = root / "Data" / "Decoy.esp"
    (root / "Data").mkdir()
    r = drive(root, adapter, profile, [{"op": "shell", "cmd": "echo x > \"{}\"".format(decoy)}])
    assert r[0]["blocked"] and not decoy.exists()


FAULT_SOURCES = {k: v for k, v in conf.FAULTS.items()}


@pytest.mark.parametrize("profile", sorted(PROFILES))
@pytest.mark.parametrize("fault", sorted(FAULT_SOURCES))
def test_a_broken_engine_fails_closed_through_the_agent(tmp_path, profile, fault):
    root, adapter = staged(tmp_path, profile)
    run_py = root / "tools" / "skyguard" / "run.py"
    if FAULT_SOURCES[fault] is None:
        run_py.unlink()
    else:
        run_py.write_text(FAULT_SOURCES[fault], encoding="utf-8", newline="\n")
    decoy = root / "Data" / "Decoy.esp"
    r = drive(root, adapter, profile, [{"op": "write", "path": str(decoy)}], env={"SKYGUARD_ADAPTER_TIMEOUT_S": "2"})
    assert r[0]["blocked"] and not decoy.exists()
    if profile == "json-deny":
        assert "GUARD INERT" in r[0]["reason"]


def test_control_a_naive_hook_lets_the_toy_write_the_decoy(tmp_path):
    """The toy must be able to fail open, or every block above proves nothing."""
    root, adapter = staged(tmp_path, "json-deny")
    (root / "tools" / "skyguard" / "run.py").write_text("raise SystemExit(1)\n")
    naive = root / "naive.py"
    naive.write_text("import subprocess, sys\n"
                     "cp = subprocess.run([sys.executable, %r], input=sys.stdin.buffer.read(), capture_output=True)\n"
                     "sys.exit(0)\n" % str(adapter.with_name("nonexistent.py")))
    decoy = root / "Data" / "Decoy.esp"
    script = root / "calls.json"
    script.write_text(json.dumps([{"op": "write", "path": str(decoy)}]))
    cp = subprocess.run([sys.executable, str(TOY), "--profile", "json-deny", "--hook", json.dumps([sys.executable, str(naive)]),
                         "--script", str(script), "--cwd", str(root)], capture_output=True, timeout=120)
    assert json.loads(cp.stdout)[0]["executed"] and decoy.exists()


def test_hook_timeout_shorter_than_the_adapter_fails_open(tmp_path):
    """ADAPTING.md section 6 step 5: the agent's hook timeout must exceed the adapter's, or a hung engine runs the call."""
    root, adapter = staged(tmp_path, "json-deny")
    (root / "tools" / "skyguard" / "run.py").write_text("import time; time.sleep(60)\n")
    decoy = root / "Data" / "Decoy.esp"
    r = drive(root, adapter, "json-deny", [{"op": "write", "path": str(decoy)}], hook_timeout=1,
              env={"SKYGUARD_ADAPTER_TIMEOUT_S": "5"})
    assert r[0]["executed"] and decoy.exists()
    root2, adapter2 = staged(tmp_path / "ok", "json-deny")
    (root2 / "tools" / "skyguard" / "run.py").write_text("import time; time.sleep(60)\n")
    decoy2 = root2 / "Data" / "Decoy.esp"
    r2 = drive(root2, adapter2, "json-deny", [{"op": "write", "path": str(decoy2)}], hook_timeout=30,
               env={"SKYGUARD_ADAPTER_TIMEOUT_S": "2"})
    assert r2[0]["blocked"] and not decoy2.exists()


# ---- the check must be able to fail: each mutant of the machinery has to turn a NAMED row red --------------
MUTANTS = {  # id -> (exact text in the template, replacement, the row that must go red)
    "fail-open": ("out, code, err = _block_for_failure(e.__class__.__name__ + \" \" + str(e))",
                  "out, code, err = render_allow(\"\")", "fault:engine-crash"),
    "ignore-exit-code": ("    if cp.returncode != 0:\n        raise RuntimeError(\"engine exited {}\".format(cp.returncode))\n",
                         "", "fault:engine-allow-but-exit-1"),
    "ignore-version": ("v.get(\"v\") != 1 or ", "", "fault:engine-wrong-version"),
    "deny-to-allow": ("return _checked(render_block(reason))", "return _checked(render_allow(context))", "case:decoy-plugin-write"),
    "no-timeout": ("        timeout=float(os.environ.get(\"SKYGUARD_ADAPTER_TIMEOUT_S\", ADAPTER_TIMEOUT_S)), env=env)",
                   "        env=env)", "fault:engine-hang"),
}


@pytest.mark.parametrize("mutant", sorted(MUTANTS))
def test_each_mutant_of_the_machinery_turns_the_named_row_red(tmp_path, mutant, monkeypatch):
    old, new, row = MUTANTS[mutant]
    tpl = TPL.read_text(encoding="ascii")
    assert tpl.count(old) == 1, "mutation anchor drifted: edit MUTANTS, do not weaken the template"
    monkeypatch.setattr(conf, "PROBE_TIMEOUT_S", 6.0)
    root, adapter = staged(tmp_path, "json-deny", tpl.replace(old, new))
    rows = conf.run_conformance(adapter.parent, PROFILES["json-deny"][1], cases_for("json-deny"), port=True)
    red = {r.id for r in rows if not r.ok}
    assert row in red, "mutant {} was not caught by {} (red rows: {})".format(mutant, row, sorted(red))


def test_a_template_edit_outside_the_slots_is_reported_by_conformance(tmp_path):
    tpl = TPL.read_text(encoding="ascii").replace("ENGINE_BUDGET_S = 20", "ENGINE_BUDGET_S = 21")
    root, adapter = staged(tmp_path, "json-deny", tpl)
    rows = conf.run_conformance(adapter.parent, PROFILES["json-deny"][1], cases_for("json-deny"))
    assert [r for r in rows if r.id == "template-identical" and not r.ok]


def test_conformance_rejects_an_adapter_that_drops_the_paths(tmp_path):
    slots = (EX / "json_deny.slots.py").read_text(encoding="ascii").replace("paths=[args[\"path\"]]", "paths=[]")
    d = tmp_path / "src" / "toy"
    d.mkdir(parents=True)
    (d / "adapter.py").write_text(conf.compose(TPL.read_text(encoding="ascii"), slots), encoding="ascii", newline="\n")
    rows = conf.run_conformance(d, PROFILES["json-deny"][1], cases_for("json-deny"))
    assert "case:decoy-plugin-write" in {r.id for r in rows if not r.ok}


def test_this_module_has_no_skips():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("skip", "skipif", "xfail"):
            raise AssertionError("a skipped toy-agent test is a green that examined nothing")
```
  Two details to settle against the merged base at implementation time and then **keep the assertion, not
  the guess**: (i) the `"plugin"` text assertion in the end-to-end test is the Task 0-pinned wording of
  `write_rules.DENY_PLUGIN` (`...plugin/archive files...`); if the merged text differs, assert the pinned text.
  (ii) the shell decoy needs Plan 5's E21 rule; if Task 0 (b) found it absent, delete only that test and
  the shell pair from `cases_for`, and say so in `ADAPTING.md` §7.
- [ ] **Step 3: Run to green, then run the mutants in isolation** (they cost one 6-second timeout each):
  `python -m pytest tests/test_toy_agent.py -q -p no:cacheprovider`. Expected: all pass; the five mutant tests
  are the proof that the conformance rows can fail. Record the wall time of the file.
- [ ] **Step 4: Measure the adapter's cost** (R9). Not a CI assertion. In the scratchpad, stage the json-deny
  adapter as in `staged`, then run it 40 times with the benign native payload via `subprocess.run` and take
  `time.perf_counter` around each call; compute median and 95th percentile, plus the same for
  `python tools/skyguard/run.py neutral pre-tool` alone. Write both, with `python --version`, the OS, the run
  count and the caveat that other branches may have been loading the machine, to
  `docs/superpowers/measurements/2026-10-05-plan7-adapter-latency.md` under headings `## adapter median` and
  `## adapter p95`. (`ADAPTING.md` cites those headings.)
- [ ] **Step 5: Commit.** `git add tests/toyagent tests/test_toy_agent.py docs/superpowers/measurements/2026-10-05-plan7-adapter-latency.md`
  `git commit -m "Toy-agent CI test: two fake agents follow ADAPTING.md end to end; fault matrix; mutants turn conformance red"`.

---

### Task 6: `ADAPTING.md` and its claims gate

**Files:**
- Create: `ADAPTING.md` (repo root, so it ships next to `README.md`), `tests/test_adapting_claims.py`

The document is written *to the agent*. Facts live between `<!-- facts:begin -->` and `<!-- facts:end -->`;
every table row and bullet inside is a claim and carries at least one citation. Guidance (steps, rules, the
contract) lives outside the markers and states what the *toolkit* does, which the tests above pin.

- [ ] **Step 1: Write the gate first** (`tests/test_adapting_claims.py`) and watch it fail on the missing file:

```python
"""ADAPTING.md may not say more than the code and the measurements say."""
from __future__ import annotations

import re
from pathlib import Path

from conftest import REPO

DOC = REPO / "ADAPTING.md"
SPEC = REPO / "docs" / "superpowers" / "specs" / "2026-09-30-universal-agent-support-design.md"
CITE = re.compile(r"\[(src|repo|spec|measured): ([^\]]+)\]")
OTHER_AGENTS = re.compile(r"\b(OpenCode|Cursor|Gemini|Aider|Cline|Copilot|Windsurf|Zed|Continue)\b")
UNMEASURED = re.compile(r"not measured|unmeasured", re.I)


def facts(text: str):
    regions = re.findall(r"<!-- facts:begin -->(.*?)<!-- facts:end -->", text, re.S)
    assert regions, "ADAPTING.md has no facts region"
    lines = "
".join(regions).splitlines()
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("| ---") or s.startswith("|---"):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if s.startswith("|") and (nxt.startswith("| ---") or nxt.startswith("|---")):
            continue                                   # a table header row
        if s.startswith("|") or s.startswith("- "):
            out.append(s)
    return out


def resolve(kind: str, target: str, repo: Path = REPO):
    """-> None when it resolves, else the problem."""
    if kind == "spec":
        text = SPEC.read_text(encoding="utf-8")
        return None if re.search(r"^\| %s \|" % re.escape(target.strip()), text, re.M) else "no spec row " + target
    sep = "::" if kind in ("src", "repo") else "#"
    path, _, needle = target.partition(sep)
    f = repo / path.strip()
    if not f.is_file():
        return "no file " + path
    return None if (not needle or needle.strip() in f.read_text(encoding="utf-8", errors="replace")) else \
        "{} does not contain {!r}".format(path, needle.strip())


def problems(text: str, repo: Path = REPO):
    out = []
    for line in facts(text):
        cites = CITE.findall(line)
        if not cites:
            out.append("uncited fact: " + line[:80])
        for kind, target in cites:
            p = resolve(kind, target, repo)
            if p:
                out.append("{} [{}: {}]".format(p, kind, target))
    for ln in text.splitlines():
        if OTHER_AGENTS.search(ln) and not UNMEASURED.search(ln):
            out.append("names an agent the toolkit has not measured without saying so: " + ln[:80])
    return out


def test_adapting_md_has_no_unbacked_claim():
    assert problems(DOC.read_text(encoding="utf-8")) == []


def test_every_surface_and_agent_row_cites_the_decision_that_governs_it():
    t = DOC.read_text(encoding="utf-8")
    assert "[spec: D14]" in t and "[spec: D8]" in t


def test_the_status_words_in_the_doc_are_exactly_the_ones_the_validator_derives():
    t = DOC.read_text(encoding="utf-8")
    for word in ("partially supported: Layers 0-2", "locally adapted, self-verified"):
        assert word in t


def test_the_gate_can_fail():
    bad = ("<!-- facts:begin -->\n| Claim | Source |\n| --- | --- |\n| an uncited claim | none |\n"
           "| a bad cite | [spec: E999] [src: tools/skyguard/nope.py::x] |\n<!-- facts:end -->\n"
           "OpenCode blocks writes.\n")
    got = problems(bad)
    assert any("uncited" in g for g in got) and any("E999" in g for g in got)
    assert any("nope.py" in g for g in got) and any("OpenCode" in g for g in got)


def test_the_gate_accepts_a_well_cited_row():
    good = ("<!-- facts:begin -->\n| Claim | Source |\n| --- | --- |\n"
            "| Codex turns ask into deny | [src: tools/skyguard/adapters/codex.py::def render] |\n<!-- facts:end -->\n"
            "OpenCode: not measured.\n")
    assert problems(good) == []


def test_every_file_the_doc_tells_the_agent_to_use_exists():
    t = DOC.read_text(encoding="utf-8")
    for rel in re.findall(r"`((?:tools|tests|agent|docs)/[A-Za-z0-9_./-]+\.[a-z]+)`", t):
        assert (REPO / rel).exists() or "<" in rel, "ADAPTING.md names a file that does not exist: " + rel
```

- [ ] **Step 2: Write `ADAPTING.md`.** Full text (the executor pastes it, then runs the gate and fixes any
  citation that does not resolve **by correcting the document, never the gate**):

````markdown
# ADAPTING.md — you are an agent this toolkit does not support yet

This file is written to you, the coding agent. If you are Claude Code or the Codex CLI, stop: the toolkit
is already wired for you (see `README.md`). Otherwise you can wire it into yourself. Be honest about how
far you get: the toolkit gives you three layers without any hook, and a fourth only if you can *prove* a hook
of yours blocks.

## 0. The only status words you may use

| Say | When |
|---|---|
| *supported* | never about yourself: only the Claude Code CLI and the Codex CLI are, as `README.md` lists them |
| **partially supported: Layers 0-2** | the default. You read the instructions and skills and use the tools; nothing stops a destructive command except the read-only attribute (Layer 2, where installed) |
| **locally adapted, self-verified** | only after section 7. It describes *this install on this machine*. It is not a claim by the toolkit |

## 1. Rules

1. **Never edit the engine, the guard corpus, a rule module or a test to make your adapter pass.** An
   adapter translates; the engine decides. If a rule looks wrong, report it (section 9).
2. **Find out how your agent fails.** A hook that errors, times out or prints garbage must not allow. Some agents
   treat all of those as "allow" (section 5). Measure yours (section 3) before trusting any wrapper.
3. **Only a mechanism your agent honours as a hard block counts.** Prove it with a harmless action.
4. **Do not install, upgrade, repair or remove host software** (Python, Git, Node, anything) unless the user's
   task says so in so many words. Use the `toolkit-setup` skill.
5. **Report every gap** in the capability report. A gap you did not write down will be assumed absent.

## 2. Layers 0-2: what you get without any hook

1. Read `AGENTS.md` completely. It is the toolkit's instruction file. If your instruction limit is below 32 KiB
   it may be cut silently: say so in the report (section 3) and read the file yourself in pieces.
2. Skills are plain `SKILL.md` files in `.agents/skills/<name>/`. Read one *before* the work it names.
3. Use `tools/` (the wrappers, `tools/skyguard`, `tools/hook-canary.sh`) through your shell tool.
4. Read the `## My preferences` section of `KNOWLEDGEBASE.local.md` every session; named agents get it injected
   by a hook, you must read it.

## 3. Self-assessment: produce the capability report

Copy `tools/adapting/capability-report.template.json` to `capability-report.json` and fill **every** leaf as
`{"value": ..., "tier": "MEASURED|READ|INFERRED|ASSUMED", "evidence": "..."}`. Measure, do not recall:

- **Payload capture.** Install a hook that only writes its stdin to a file and exits 0 (or your agent's
  allow). Run one shell command, one file write and, if you have it, one MCP call. Keep the files: they are
  your conformance cases (`payload_capture.files`).
- **Can the hook block?** Hook: create `probe-ran.flag`, then emit your candidate block. Action: a command that
  creates `probe-action.flag`. If `probe-action.flag` exists, that block shape does **not** stop your agent.
- **Does a failed hook fail open?** Same, but the hook exits 1; then prints garbage; then sleeps past its
  timeout; then points at a script that does not exist. Record each. Any row where the action ran means
  that *the wrapper, not the agent*, must carry the failure.
- **Which surface?** Terminal, desktop app and IDE extension are different surfaces. Probe the one you are
  running in; do not generalise.
- **Can it ask?** Only if a block shape pauses and shows the user a prompt. Default: no.

Validate with `python tools/adapter_conformance.py report capability-report.json`. The tool *derives* your
status; a `claimed_status` stronger than the derived one is an error.

## 4. The contract

Request (JSON on stdin of `python tools/skyguard/run.py neutral pre-tool --root R --root-win R --project R`;
`R` is the install folder):

| Field | Meaning |
|---|---|
| `v` | must be `1` |
| `event` | must be `"pre_tool"` |
| `kind` | `shell` (a command string), `write` (a file write or edit, with `paths`), `mcp`, or `other` |
| `shell` | `"bash"`, `"powershell"` or `null` (both dialects); only with `kind: "shell"` |
| `command` | the command text, for `shell` |
| `paths` | absolute paths, for `write` (and for a shell call you can already see writes files) |
| `ops` | parallel to `paths`: `add`, `update`, `delete`, `move_from`, `move_to`, `write`, `edit`; default `update` (asks for a backup) |
| `cwd` | the call's working directory, **raw** (do not normalise) |
| `mcp_tool`, `mcp_args`, `tool_name`, `session_id`, `agent` | optional strings |

Unknown fields are refused. A tool call you cannot classify is `other` (allowed and logged), **except** that any
call carrying a command string must be `shell`. A write whose paths you cannot extract must not be sent as an
empty `write`: the engine denies it, and so should you.

Answer (stdout): the line `SKYGUARD-OK`, then one line of JSON:
`{"v":1,"decision":"allow|ask|deny","reason":...,"context":...,"status":"evaluated|partial|failed","code":...}`.
Exit 0 whenever the engine decided. **Exit 1 with nothing on stdout means the engine broke**, and that is a deny
for you. A verdict is yours to render: `deny` -> your hard block; `ask` -> a deny that tells the user to run
the command themselves, unless you proved your agent pauses and shows a prompt; `allow` -> let it run, passing
`context` as advice if you can.

## 5. What is known about the two supported agents

Copy their *mechanism*, not their wording. Every row is cited.

<!-- facts:begin -->
| Fact | Tier | Source |
|---|---|---|
| Codex blocks a tool call on exactly one thing: a parsed JSON `permissionDecision: "deny"`. `ask`, exit 2, exit 1, malformed JSON, a timeout and a missing script all let the command run | MEASURED | [spec: E17] [measured: docs/superpowers/measurements/2026-10-02-plan4-codex-derisk.md#EXIT2ME] |
| Codex skips a project's unapproved hooks silently | MEASURED | [spec: E2] [measured: docs/superpowers/measurements/2026-10-02-plan4-codex-derisk.md#skipped silently] |
| Codex shell commands arrive as tool `Bash` although they run under PowerShell | MEASURED | [spec: E3] |
| Codex `apply_patch` carries paths only inside the patch text | MEASURED | [spec: E4] [src: tools/skyguard/patch_paths.py::def parse_patch] |
| A user-typed `!` command in the Codex prompt fires no pre-tool hook | MEASURED | [src: tools/skyguard/adapters/codex.py::fires no PreToolUse] |
| The Codex adapter turns every `ask` into a deny that says the user must run it themselves | READ | [src: tools/skyguard/adapters/codex.py::def render] |
| The Claude adapter passes `ask` and `deny` through | READ | [src: tools/skyguard/adapters/claude.py::def render] |
| A deny that arrives after the agent's own hook timeout is discarded, so every wrapper bounds its own work below that timeout | READ | [src: agent/guards/adapters/codex/skyguard-entry.sh::DISCARDS a deny] |
| The engine refuses a write request that names no file, rather than allowing it | READ | [src: tools/skyguard/engine.py::NO_PATHS] |
| The engine's neutral entry refuses unknown fields and answers with the `SKYGUARD-OK` marker | READ | [src: tools/skyguard/neutral.py::unknown field] |
| The engine's own health tooling knows only the agents `claude` and `codex`; it does not certify an adapted agent | READ | [src: tools/skyguard/health.py::COUNTING_HOOKS] |
| `AGENTS.md` past 32 KiB is silently truncated by Codex | MEASURED | [spec: E7] |
| A skill with Claude-only frontmatter loads and runs in Codex | MEASURED | [spec: E6] |
| The adapter template adds one Python start to every call; measured median and p95 are recorded | MEASURED | [measured: docs/superpowers/measurements/2026-10-05-plan7-adapter-latency.md#adapter median] |
<!-- facts:end -->

## 6. Wire the adapter

1. **Instructions.** Make your agent read `AGENTS.md` at the start of every session (or copy it to the file name
   your agent reads; do not edit the copy).
2. **Skills.** Point your agent's skills folder at `.agents/skills/` or copy the folders.
3. **Adapter folder.** `mkdir tools/adapters/<name>` and copy `tools/adapting/adapter_template.py` to
   `tools/adapters/<name>/adapter.py`. Edit **only** the block between `# SLOTS-BEGIN` and `# SLOTS-END`:
   `AGENT_NAME`, `STATIC_BLOCK` (a literal in your agent's measured block shape), `map_payload`,
   `render_block`, `render_allow`, and `render_ask` only if you proved your agent asks. The worked examples are
   `tools/adapting/examples/json_deny.slots.py` (a JSON-deny agent, the Codex mechanism) and
   `tools/adapting/examples/exit2.slots.py` (an exit-code agent). The Claude and Codex adapters in
   `tools/skyguard/adapters/` are the reference for payload mapping; read them, do not import them.
   An unfilled adapter refuses every call.
4. **Register the hook** in your agent's own configuration: run `python tools/adapters/<name>/adapter.py` before every
   tool call that runs a command or writes a file, matching **all** such tools. Use the absolute interpreter path.
5. **Timeouts.** The adapter's own deadline is 25 s. Your agent's hook timeout **must be longer**: if it is
   shorter, a hung engine makes the agent give up and *run the call*. (`tests/test_toy_agent.py::
   test_hook_timeout_shorter_than_the_adapter_fails_open` shows it happening.)
6. **Conformance.** Write `cases.json` and `profile.json` from your captured payloads (format: the docstring of
   `tools/adapter_conformance.py`), then run
   `python tools/adapter_conformance.py run --adapter-dir tools/adapters/<name> --profile profile.json --cases cases.json`.
   It runs against a scratch copy and must print `CONFORMANCE PASS`. If your agent's adapter is not
   Python, pass `--port`; the structure check is then skipped and the tool says so (`UNVERIFIED STRUCTURE`), and your port must
   honour `SKYGUARD_ADAPTER_TIMEOUT_S`.

## 7. Required proof before any claim

1. `CONFORMANCE PASS` from section 6.
2. **The live canary.** In a *real* session of your agent, on the surface you will use, ask yourself to write
   `Data/Canary.esp` (a decoy; never an existing plugin). It must be refused with the reason visible, and the
   file must not exist afterwards. Then do the same with the hook deliberately broken (rename `run.py`
   temporarily in a scratch copy, not the real install) and see the call refused.
3. Fill `proof` in the capability report and run its validator. Only its derived status may be quoted.

No toolkit command certifies an adapted agent yet: `tools/hook-canary.sh` and the engine's health report know
only the two named agents. Your adapter writes heartbeats to
`.claude/backups/.hook-heartbeat/adapted/<name>/` so that a later toolkit release, or the user, can.
Until section 7 passes, say **partially supported: Layers 0-2**.

## 8. Surfaces

A surface (terminal, desktop app, IDE extension) is claimed only after a live probe shows its hooks fire.

<!-- facts:begin -->
| Surface | Status | Source |
|---|---|---|
| Codex CLI (interactive and `codex exec`) | claimed | [src: README.md::Surfaces] [spec: D14] |
| Codex desktop app and IDE extensions | not claimed: some Windows Codex executions emit no hook calls | [spec: E24] [src: README.md::Layers 0-2 only] |
| Claude Code CLI | claimed | [spec: E27] |
| Any other agent or surface | not measured; shipped only once a live test passes | [spec: D8] [spec: D14] |
<!-- facts:end -->

## 9. Gaps and upstreaming

List every gap in the report. To offer your adapter upstream, open an issue with: the agent's name and
version, the surface, the capability report, the conformance output, the canary evidence and the slots block.
Never ask the toolkit to call you supported on the strength of your own report.
````

  `facts()` gathers **every** facts region (sections 5 and 8), so the surface table is gated too.
- [ ] **Step 3: Run the gate** `python -m pytest tests/test_adapting_claims.py -q -p no:cacheprovider`. Fix the
  document (not the gate) for every unresolved citation. Check the three needles that depend on earlier tasks:
  `neutral.py::unknown field`, the latency headings, and `README.md::Layers 0-2 only`.
- [ ] **Step 4: Mutation-check the gate** by temporarily changing one cite target in the doc (restore, hash-verify):
  the gate goes red naming that line.
- [ ] **Step 5: Commit.** `git add ADAPTING.md tests/test_adapting_claims.py`
  `git commit -m "ADAPTING.md for unknown agents, with a gate that resolves every cited claim"`.

---

### Task 7: Runtime inventory tool (the runbook's only code)

**Files:**
- Create: `tools/inventory-runtimes.py`, `tests/test_inventory_runtimes.py`

**Behaviour:** `inventory()` finds **every** candidate for `python` (`python`, `python3`, `py -3`), `git`
and `node` on `PATH`, launches each with `--version` only (10 s limit), and classifies it `ok | too-old |
store-alias | broken | timeout`; a runtime with no candidate is `missing`. `plan()` turns an inventory plus
an authorization into, per runtime, an `action` of `none | report | manual | install-authorized` and, only
for the last, the exact command. **The tool never runs an install**: its single use of `subprocess` is the
version launch.

- Python floor `(3, 8)` [src: `tools/skyguard/run.py::version_info[:2] < (3, 8)`]; git and node have no floor
  here (Node's need is documented in `docs/installing-tools.md`, not duplicated).
- Store alias: a candidate whose path contains `WindowsApps` and which does not print a version, or whose
  output contains `Microsoft Store` / `was not found`.
- `parse_authorization(text) -> (dict, list)` reads **only** a sentence of the form
  `Host-software installs: AUTHORIZED python=<Id>==<X.Y[.Z[.W]]>[, node=<Id>==<ver>][, git=<Id>==<ver>].`
  `Id` matches `[A-Za-z0-9][A-Za-z0-9.+-]*`, `ver` matches `\d+(\.\d+){1,3}`; runtime keys are
  `python|git|node`. `NOT AUTHORIZED`, any other text, or any malformed item gives `{}` for that item and
  lists it as rejected. Absence means no authorization.
- Install command (printed only if: runtime `missing` or `store-alias`; authorization names it with an exact
  version; `platform == "win32"`; `winget` launches): 
  `winget install --id <Id> --exact --version <V> --no-upgrade --accept-package-agreements --accept-source-agreements`
  plus `--source winget` **only if Task 0 confirmed that flag**.
- `too-old` / `broken` / `timeout`: action `report`, with manual steps, **even when authorized** (R6).
- Everything else without authorization: action `manual` with the vendor-download steps.

- [ ] **Step 1: Tests first** (`tests/test_inventory_runtimes.py`): load the tool via `importlib`; build
  candidates from `sys.executable -c ...` argv lists so no OS-specific fake exists. Rows:

```python
def fake(code, rc=0): return [sys.executable, "-c", code.replace("RC", str(rc))]
OK311 = fake("print('Python 3.11.9')")
OLD37 = fake("print('Python 3.7.9')")
STORE = fake("import sys; sys.stderr.write('Python was not found; run without arguments to install from the Microsoft Store'); sys.exit(9009)")
HANG  = fake("import time; time.sleep(60)")
GONE  = ["no-such-runtime-xyz", "--version"]
```
  Assertions: `ok`/`too-old`/`store-alias`/`timeout` (with `launch(argv, timeout=1)`)/`broken`/`missing`
  classification; **every** candidate is reported, not only the first (`inventory(candidates={"python":
  [STORE, OK311]})` → `best` is the second); the plan table:
  - missing + no authorization → `manual`, no command;
  - missing + valid authorization + win32 + winget present → `install-authorized` whose command **contains**
    `--exact --version 3.12.8 --no-upgrade` and **never** contains `upgrade` as a word other than inside
    `--no-upgrade`;
  - missing + authorization for a *different* runtime → `manual`;
  - missing + authorization on `linux`/`darwin` → `manual` (R6 scope: winget only);
  - `too-old` + authorization → `report`, no command; `broken` likewise;
  - `store-alias` + authorization → `install-authorized`; without → `manual`;
  - authorization with a version-less item (`python=Python.Python.3.12`) → rejected, `manual`;
  - authorization text `Host-software installs: NOT AUTHORIZED.` → `{}`;
  - an injection-shaped id (`python=Foo;rm -rf x==1.2.3`) → rejected;
  - **the recorder test**: monkeypatch `subprocess.run` (and `Popen`) to record argv and return a fake
    process; run `inventory()` + `plan()` with a fake `winget` on PATH; assert every recorded argv's tail is
    exactly `["--version"]` (or `["-3", "--version"]` for `py`), that no argv's executable stem is `winget`
    except `["winget", "--version"]`, and that the string `install` appears in **no** recorded argv;
  - a static test: the tool's source text contains the word `install` only in strings that are printed or in
    comments (AST scan: no `subprocess` call has a non-constant argv element containing `install`).
  - **the shipped prompt authorizes nothing**: `parse_authorization(<prompt text read from SETUP_PROMPT.txt>)`
    is `({}, [])` (this row fails until Task 9; mark it with the Task 9 commit, do not skip it).
- [ ] **Step 2: Implement** `tools/inventory-runtimes.py` (stdlib, 3.8 syntax; hyphenated name like
  `crash-triage.py`; tests load it with `importlib.util.spec_from_file_location`). CLI:
  `python tools/inventory-runtimes.py inventory` (JSON), `... plan [--auth-text "<sentence>" | --auth-file F]`
  (human text plus `--json`). Exit 0 always, because *reporting* is the job; it exits 2 only on a usage error.
- [ ] **Step 3: Run** `python -m pytest tests/test_inventory_runtimes.py -q -p no:cacheprovider` → green (the
  prompt row excepted until Task 9; keep it written, marked `xfail(strict=True)` **with reason "Task 9"**
  and remove the marker in Task 9; the module-level no-skips rule still applies: strict xfail is not a skip).
- [ ] **Step 4: Run it once against this machine, read-only,** and paste the output into the commit body:
  `python tools/inventory-runtimes.py plan --auth-text "Host-software installs: NOT AUTHORIZED."`.
  Check the result against what you know is installed here (Python 3.10/3.12, Node, Git for Windows): any
  false `missing` is a bug in discovery, fix it with a test first.
- [ ] **Step 5: Commit.** `git add tools/inventory-runtimes.py tests/test_inventory_runtimes.py`
  `git commit -m "inventory-runtimes: launch-based runtime inventory and an install plan that only prints"`.

---

### Task 8: The `toolkit-setup` skill

**Files:**
- Create: `agent/skills/toolkit-setup/SKILL.md`, `tests/test_toolkit_setup_skill.py`
- Regenerate: `.claude/skills/toolkit-setup/`, `.agents/skills/toolkit-setup/` (via the generator)
- Modify: `agent/README.md` (the skills list)

- [ ] **Step 1: Write the skill** (agent-neutral: no `Claude`, `CLAUDE.md`, `/hooks`, `.claude/...`):

````markdown
---
name: toolkit-setup
description: Use when setting the toolkit up in a folder, or when a command fails because Python, Git or Node is missing, broken or too old. Inventories the runtimes by launching them, never upgrades or repairs one that exists, and installs host software only when the user's own task authorizes it.
---

# Toolkit setup runbook

## The rule

You change the user's machine only as far as the **user's task** says. Look first; report; wait. A missing or broken
runtime is a thing to *tell the user about*, not a thing to fix on your own. If the task does not say, in so many
words, that you may install a named runtime at an exact version, you do **not** install anything.

## Steps

1. **Read the authorization.** The task's first paragraph contains one sentence starting
   `Host-software installs:`. `NOT AUTHORIZED` (or no such sentence) means no installs at all. Otherwise it
   lists `python|git|node = <winget id>==<exact version>` items.
2. **Inventory by launching.** If any Python is available, run `python tools/inventory-runtimes.py inventory`
   (try `py -3`, `python`, `python3`). It finds every candidate on `PATH` and runs each with `--version` only.
   If no Python runs at all, do the same by hand: `py -3 --version`, `python --version`, `python3 --version`,
   `git --version`, `node --version`, and look at the output, not at whether the command was found. A Windows
   Store alias answers with a Store message, not a version: that is **not** an installed Python.
3. **Plan.** Run `python tools/inventory-runtimes.py plan --auth-text "<the sentence>"`. For each runtime it
   gives one action:
   - `none`: nothing to do.
   - `report`: it is there but too old, broken or hung. **Do not upgrade, repair, reinstall or remove it.** Tell the
     user what you found and the manual steps printed, then stop and wait.
   - `manual`: it is missing and you are not authorized. Print the manual steps for the user and wait.
   - `install-authorized`: it is missing, the user authorized exactly this id and version, and the command below
     is printed. Run **only** a command the plan printed, unchanged.
4. **Verify by launching again** after any install the user or you performed. If the new runtime is not on
   `PATH` yet, say so and ask the user to open a new terminal; do not retry installs.
5. **Then** run `bash setup.sh` (Windows: from Git Bash). Never pass it a flag that installs software.

## What an install command looks like

<!-- install-form -->
```text
winget install --id <Id> --exact --version <V> --no-upgrade --accept-package-agreements --accept-source-agreements
```

`<Id>` and `<V>` come from the user's authorization, never from your memory. Only Windows with `winget`
present is covered; on any other system the answer is always `manual`.

## Never

- Never run a package manager's upgrade, repair, reinstall or uninstall for a runtime, and never use a
  force, no-confirm or "latest" form.
- Never download and run an installer you were not given, and never pipe a download into a shell.
- Never install a runtime to make a failing check pass; say what failed.
- Never edit the user's `PATH`, environment variables or registry.

## Why Git matters

The toolkit's hooks run under Git Bash on Windows, and a hook that cannot start lets some agents run the
command anyway (spec evidence E3, E17). A missing Git is therefore a safety gap, not a convenience: tell the user.
````

- [ ] **Step 2: Tests** (`tests/test_toolkit_setup_skill.py`):
  - the skill parses (frontmatter `name: toolkit-setup`, a `description`, first bytes `---`);
  - **fenced-block tripwire:** every fenced code block in the skill is scanned for the tokens
    `upgrade` (other than inside `--no-upgrade`), `repair`, `reinstall`, `uninstall`, `--force`, `--silent`,
    `choco`, `scoop`, `apt`, `brew`, `sudo`, `Install-Package`, `iex`, `curl`, `Invoke-WebRequest`;
    the only block allowed to contain `winget install` is the one directly under `<!-- install-form -->`, and it
    must match `^winget install --id <Id> --exact --version <V> --no-upgrade` (placeholders, not values);
  - the install-form line **equals** the command shape `tools/inventory-runtimes.py` prints (substitute
    `<Id>`/`<V>` and compare with the tool's output): the skill and the tool cannot drift;
  - the skill names `tools/inventory-runtimes.py`, and that file exists;
  - **the test can fail**: a copy of the skill with ``winget upgrade --all`` added in a fenced block is
    flagged; a copy without the marker is flagged.
- [ ] **Step 3: Regenerate and check.** `python scripts/gen_agent_trees.py` then
  `python scripts/gen_agent_trees.py --check` (green) and `python scripts/gen_agent_trees.py --budget` (note
  that skills do not count toward the `AGENTS.md` budget; record headroom for Task 10). Add `toolkit-setup` to
  the skills list in `agent/README.md`. Run `python -m pytest tests/test_instruction_neutrality.py
  tests/test_gen_agent_trees.py tests/test_toolkit_setup_skill.py -q -p no:cacheprovider`. If a test hard-codes
  the skill count or list (Task 0 Step 4 looked), update it **in this commit** with the new member, never by
  loosening the assertion.
- [ ] **Step 4: Commit.** `git add agent/skills/toolkit-setup agent/README.md .claude/skills .agents/skills tests/test_toolkit_setup_skill.py`
  (plus any test file that listed the skills) `git commit -m "toolkit-setup skill: inventory first, report, never upgrade or repair, install only when authorized"`.

---

### Task 9: The universal setup prompt

**Files:**
- Modify: `SETUP_PROMPT.txt`, `README.md` (the embedded copy and its lead-in), `docs/getting-started.md`
  (embedded copy), `tests/test_repo_invariants.py` (`PROMPT_START`), `tests/test_inventory_runtimes.py` (remove
  the Task 9 xfail)
- Create: `tests/test_setup_prompt.py`

The prompt keeps every behaviour of the old one (optional tools, DevBench told-not-installed, Nexus key,
tailoring) and changes four things on purpose: it names no agent as *the* reader, it routes through the
`toolkit-setup` skill, it **removes** "Install any missing prerequisites (Python 3, Node.js) for me", and it
carries the authorization sentence with the default `NOT AUTHORIZED`. The product rename is Plan 8's, so the
prompt names no product.

- [ ] **Step 1: Build the new text programmatically** (never retype the long tail): in the scratchpad,
  `old = Path("SETUP_PROMPT.txt").read_text().strip()`; `tail = old[old.index("After setup, ask me"):]`;
  `head = ` the text below; `new = head + " " + tail`. Verify `tail` contains no occurrence of `Claude`
  (Task 0 saw one `Claude` in the file; locate it and decide whether it belongs to the head or tail, and
  replace *only that word* with `the assistant`/`you` if it sits in the tail).

```text
I just installed the Skyrim modding toolkit into this folder. Read AGENTS.md (Claude Code reads CLAUDE.md, which says the same), then read the skill toolkit-setup (.agents/skills/toolkit-setup/SKILL.md; Claude Code: .claude/skills/toolkit-setup/SKILL.md) and follow it: it inventories Python, Git and Node by launching them, then you run "bash setup.sh". Host-software installs: NOT AUTHORIZED. That means: do not install, upgrade, repair or remove Python, Git, Node or anything else on my computer; if something is missing or broken, tell me the exact manual steps and wait for me. (If I want you to install something I will change that sentence myself, to this form: Host-software installs: AUTHORIZED python=<winget id>==<exact version>.) If you are an agent other than Claude Code or Codex, read ADAPTING.md after setup and tell me honestly which protection layers you can provide and which you cannot.
```
- [ ] **Step 2: Apply to the three copies.** Write `SETUP_PROMPT.txt` (single line + trailing newline). In
  `README.md` and `docs/getting-started.md` replace the line that starts with the old `PROMPT_START` by the
  new line and change the lead-in `paste it into Claude Code` to `paste it into your coding agent (Claude Code,
  Codex, ...)` (`grep -n "Copy this entire prompt" README.md docs/getting-started.md` finds them). Update
  `PROMPT_START` in `tests/test_repo_invariants.py` to `"I just installed the Skyrim modding toolkit"`.
- [ ] **Step 3: Tests** (`tests/test_setup_prompt.py`):
  - the canonical prompt contains `Host-software installs: NOT AUTHORIZED.`, `toolkit-setup`, `ADAPTING.md`,
    `AGENTS.md`;
  - it does **not** contain `Install any missing prerequisites`, `winget install`, or an unconditional
    `install` imperative aimed at host runtimes: assert with the regex
    `(?i)\binstall (any|the) missing|\binstall (python|node|git)` → no match;
  - `parse_authorization(prompt)` → `({}, [])` (this is the Task 7 row; remove its xfail now);
  - the three copies are identical (`test_setup_prompt_is_identical_everywhere` is the existing gate; also run
    it here);
  - **can fail:** the same text with the authorization sentence replaced by
    `Host-software installs: AUTHORIZED python=Python.Python.3.12==3.12.8.` parses to a non-empty
    authorization (so the parser distinguishes the two), and an edit that reintroduces the old sentence fails the
    "does not contain" assertion.
  - the old requirement that the prompt mentions DevBench as *tell, do not install* still holds
    (`DevBench` and `do NOT install it` both present).
- [ ] **Step 4: Run** `python -m pytest tests/test_setup_prompt.py tests/test_repo_invariants.py tests/test_inventory_runtimes.py -q -p no:cacheprovider`
  (the Node `npm test` invariant inside `test_repo_invariants.py` needs `node`; if it is red for an unrelated
  environment reason, run only `-k setup_prompt`).
- [ ] **Step 5: Commit.** `git add SETUP_PROMPT.txt README.md docs/getting-started.md tests/test_repo_invariants.py tests/test_setup_prompt.py tests/test_inventory_runtimes.py`
  `git commit -m "Universal setup prompt: agent-neutral, routed through toolkit-setup, host installs NOT AUTHORIZED by default"`.

---

### Task 10: Wire-in (pointer, docs, changelog, release surface)

**Files:**
- Modify: `agent/instructions/core.md` (one table row), regenerated `CLAUDE.md`/`AGENTS.md`; `agent/README.md`;
  `README.md` (a short "Other agents" paragraph after "Using with Codex"); `CHANGELOG.md`;
  `tools/verify-release.sh` and `.github/workflows/release.yml` **only if Task 0 (c) found a file list**;
  the bundle test that lists shipped files (found in Task 0).

- [ ] **Step 1: Core pointer.** In `core.md`, *Where the rest lives*, add one row, wording **neutral** (no agent
  names: `\bClaude\b` is banned in core):
  `| your agent is not one the README lists as supported, or Python, Git or Node is missing or broken | `ADAPTING.md`, skill `toolkit-setup` |`.
  Run `python scripts/gen_agent_trees.py`, then `--check` and `--budget`: **core plus the largest addendum
  must stay <= 32,768 bytes installed**; if the row tips it over, shorten the row, never raise the constant.
- [ ] **Step 2: README.** After "Using with Codex" add "Other agents": three sentences. The toolkit has Layers
  0-2 for any agent that reads files; `ADAPTING.md` lets an agent wire the rest and describes how far it can
  prove it got; the toolkit does not call such an agent supported. Link `ADAPTING.md` (the Markdown relative-link
  CI step resolves it). No claim beyond what `ADAPTING.md` cites.
- [ ] **Step 3: Release surface.** Make sure `ADAPTING.md`, `tools/adapting/**`, `tools/adapter_conformance.py`,
  `tools/inventory-runtimes.py`, `tools/skyguard/neutral.py` and the new skill are in the bundle: extend the
  list(s) found in Task 0 (c), and extend the corresponding bundle/verify test with one assertion per new
  path **and one that proves it can fail** (an archive missing `ADAPTING.md` → the check goes red). `tests/` is
  not shipped, so the toy test is a CI gate, not a bundle member: say so in the CHANGELOG.
- [ ] **Step 4: CHANGELOG** (unreleased section; follow the file's existing voice and heading style): added
  `ADAPTING.md` and the adapter kit; `run.py neutral pre-tool`; `toolkit-setup` skill and
  `tools/inventory-runtimes.py`; the universal setup prompt, **and the behaviour change**: the setup prompt no
  longer asks the agent to install missing Python or Node. Do not mention Plan numbers.
- [ ] **Step 5: Gates.** Run, targeted: `python scripts/gen_agent_trees.py --check`; `python -m pytest
  tests/test_gen_agent_trees.py tests/test_instruction_budget.py tests/test_instruction_neutrality.py
  tests/test_adapting_claims.py tests/test_toy_agent.py tests/test_skyguard_neutral.py -q -p no:cacheprovider`.
  Run the Markdown relative-link check the way `ci.yml` does (copy its Python step into a scratch script).
- [ ] **Step 5b: Tripwire for host installs in what Plan 7 adds.** Add one test
  (`tests/test_no_host_installs_in_new_files.py`): the skill, `tools/inventory-runtimes.py`, the template and the
  conformance tool contain no `winget install` / `choco` / `apt` / `brew` / `pip install` outside the one
  skill install-form block; it can fail (a temp copy with `pip install x` is flagged). **Do not** scan
  `setup.sh` here (R8): if Task 0 recorded the Node auto-install as still present, list it under "Deferred" in
  Task 11 for Plan 8.
- [ ] **Step 6: Commit** in two commits: `git commit -m "Core points at ADAPTING.md and toolkit-setup; README: other agents"` (instructions, generated files,
  README) and `git commit -m "Ship the adapter kit: bundle list, changelog, host-install tripwire"`.

---

### Task 11: Review of the whole range and close-out

**Files:**
- Create: `docs/superpowers/plans/2026-10-05-plan7-closeout.md`

Per roadmap cadence for doc/instruction plans: one reviewer over the whole range (opus), not per task. But
two parts here are guard-adjacent (`neutral.py`, the template's failure paths), so the reviewer reads those in
full and tries to break them.

- [ ] **Step 1: Full-range read** (`git diff master..plan7`): every added/changed file read in full. Checklist:
  (a) every path in the adapter that ends with an allow: list them, justify each (`allow` verdict with a valid
  marker, exit 0, a render slot's allow); (b) `BaseException` coverage (`SystemExit`, `KeyboardInterrupt`,
  `MemoryError` inside slots); (c) output encodings (non-ASCII reason on a Windows code page: the template
  writes through `sys.stdout.write`; the engine's JSON is ASCII-only, but a **slot** may add non-ASCII: note it
  in the review); (d) the heartbeat directory is under `.claude/backups`; confirm it cannot raise into the
  decision path; (e) the `--root`/`--root-win` pair for a native Windows interpreter; (f) every claim in
  `ADAPTING.md` against its citation *read by a human*, not just resolved by the gate (a needle that exists
  can still not support the sentence); (g) the prompt change is the only intended behaviour change to the user's
  first run.
- [ ] **Step 2: Fresh-eyes check of `ADAPTING.md`.** Give the document and nothing else to a clean reviewer
  context with the instruction "wire this toy agent from this document alone"; every place they have to guess
  is a defect in the document. (This is a review, not a subagent dispatch by this plan: the executing session
  decides how to run it.)
- [ ] **Step 3: Full suite, once, at the end.** `python -m pytest tests/ -q -p no:cacheprovider` on the plan7
  branch only when no other branch is running a heavy suite; record pass/skip counts and wall time against the
  base's numbers (the base count comes from Task 0).
- [ ] **Step 4: Close-out note** with: what shipped, rulings R1-R9 as executed, measured numbers (adapter
  median/p95), findings from Step 1-2 and their fixes, and **Deferred**:
  - **D-1** certification of adapted agents in `hook-canary` / `skydoctor` (Plan 6 interface; heartbeat path
    already written);
  - **D-2** `setup.sh` Node auto-install via `winget` (only if Task 0 saw it): remove in Plan 8 together with
    `--agent`, and extend the host-install tripwire to `setup.sh`;
  - **D-3** the product rename in the prompt and README text (Plan 8);
  - **D-4** OpenCode live adaptation (roadmap row 10): would be the first real third-party test of this kit;
    needs OpenCode installed by the user.
- [ ] **Step 5: Commit** `git add docs/superpowers/plans/2026-10-05-plan7-closeout.md` and
  `git commit -m "Plan 7 close-out: review findings, measurements, deferred items"`. **Do not merge to master or
  push**; the merge follows the roadmap's rule (after the blocking plan's final review) and the user's go-ahead.

---

## FINAL MANUAL BATCH additions (roadmap rule: user-run steps go at the very end, never mid-plan)

Plan 7 has **no required user-run step.** Every check is automated, and the two live probes it could want
(a real third-party agent, a real unknown-agent canary) cannot be run until the user installs such an agent.
Appended to the roadmap's FINAL MANUAL BATCH as **optional**: *"Plan 7 D-4: if you want OpenCode (or any
other agent) adapted for real, install it, then one session follows `ADAPTING.md` live; until then no claim
is made for it."* (`inventory-runtimes.py plan` run against this machine in Task 7 is read-only and driven by
the executing session, not the user.)
