# Plan 2 — `skyguard` engine + Claude adapter + equivalence gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every rule and side effect of the five bash hooks into an agent-neutral Python engine
(`tools/skyguard/`), and reduce the hooks to thin Claude Code adapter wrappers, **with zero change in
any verdict**. This is proven by a recorded corpus and a one-time replay of real command history.

**Architecture:** `tools/skyguard/` is a stdlib-only Python package (Layer 0, spec §3), with one module
per concern: line-wise `grep` semantics, path roots, audit log, the shell rules, the write rules, the
backup, the snapshot, the session check, and the Claude adapter. Each `.claude/hooks/<name>.sh` becomes
a ~40-line bash wrapper. It reads stdin with a bare `cat`, writes the heartbeat, checks that the
configured Python runs (failing closed if not), pipes the payload to `tools/skyguard/run.py claude
<role>`, and passes the JSON through. If the engine crashes or prints anything that is not JSON, the
wrapper denies by itself. Plan 2 changes nothing for Codex; Plan 4 adds that adapter.

**Tech Stack:** Python ≥ 3.8 syntax, stdlib only (tested on 3.10 locally and 3.11 in CI); bash; pytest 8.

**Spec:** `docs/superpowers/specs/2026-09-30-universal-agent-support-design.md` — §5 engine + contract,
§5.1 fail-by-deny, §7.1 corpus, §7.2 equivalence gate, §10 phase 3. Predecessor: Plan 1 (commits
`5500829..9c49c20`). Roadmap: `C:\Users\Moona\.claude\plans\looks-good-lovely-fern.md`.

## Decision to confirm at plan review

**D17 (proposed): Python becomes REQUIRED for the safety hooks, and `jq` stops being required.**
Today `setup.sh` treats Python as optional and *requires* `jq` (it tries `winget install jqlang.jq`
and errors out without it). After this plan the hooks need Python and no longer use `jq`. Without
Python, the blocking wrappers fail closed: they deny every command with `GUARD INERT … cannot run
Python`, exactly as today's hooks do without `jq`. `setup.sh` resolves a concrete interpreter path,
writes it into the wrappers through a `{{PYTHON_PATH}}` placeholder (the mechanism `{{JQ_PATH}}` uses
today), and says loudly when Python is missing. It never auto-installs Python; that is today's stated
policy. Task 7 removes the `jq` requirement only once a grep shows that no shipped file still runs `jq`.

## Global Constraints

- **Zero verdict change.** For every input, each new hook returns the same decision
  (`allow`/`advise`/`ask`/`deny`) and **byte-identical reason/context text** as the old hook. The
  corpus test (Task 1, then Task 7) and the history replay (Task 8) enforce this. The only permitted
  differences are the ones a `Ruling:` names. A missing Python is new and is covered by its own tests.
- **Fail by deny** (spec §5.1, E8). Blocking roles: any engine failure → deny `GUARD INERT: …`.
  Non-blocking roles (backup, snapshot, session): a failure is written to `AUDIT_LOG.txt`, never blocks.
- **Same side effects.** Audit-log line formats, heartbeat files (`.claude/backups/.hook-heartbeat/<hook>`
  and `<hook>.blind`), backup file naming, snapshot layout, rate limit and prune are all unchanged
  (`tools/hook-canary.sh` and `tools/kb-guard.sh` read them).
- **The wrappers keep the release-gate properties:** a bare `INPUT=$(cat)`; a non-comment
  `hook-heartbeat` reference; exactly one install placeholder; the same five file names; the same
  `settings.json` wiring and timeouts. The matcher stays `Bash` (the `Bash|PowerShell` fix is Plan 5).
- **Engine floor:** Python 3.8 syntax (no `match`, no `X | Y` type unions at runtime, no 3.9+ stdlib
  APIs). Pinned by `test_engine_parses_on_python_3_8`.
- **Generated files:** wrappers live in `agent/guards/adapters/claude/` and are generated into
  `.claude/hooks/`. `tools/skyguard/` is ordinary source and is **not** generated.
- Commit each finished task on `master`; **never push.** Test command: `python -m pytest tests/ -q -p no:cacheprovider`.
- **Never** edit the tree while a mutation run or the history replay is running.

## Review Focus

1. **A multi-line command.** grep evaluates each LINE, and Python's `\s` would match across a newline
   where `[[:space:]]` cannot. → `test_grep_is_line_wise` (Task 2) and corpus rows `multiline-*`.
2. **Trailing newlines and empty lines.** `$(jq -r …)` strips trailing newlines, so a command ending in
   `\n\n` must not gain empty lines to match against. → `test_command_trailing_newlines_are_stripped` (Task 6).
3. **Case and dialect of paths:** `C:\`, `C:/`, `/c/`, doubled separators, mixed case, `(`/`[`/`$` in an
   install path. → `test_path_regex_*` (Task 2) and corpus rows `dialect-*`.
4. **A crashing or silent engine** must never read as allow. → `test_wrapper_denies_when_engine_crashes`
   and `test_wrapper_denies_on_non_json_output` (Task 7).
5. **A non-ASCII command or path** (an accented mod name). Python's `\b` and case folding are Unicode;
   grep's depend on locale. → corpus rows `unicode-*`; any difference is a Task 8 ruling, not a guess.

---

### Task 1: Record the corpus from TODAY's bash hooks

**Files:**
- Create: `scripts/guard-corpus/record.py`, `tests/guard_corpus/expected.jsonl`, `tests/guard_corpus/README.md`
- Create: `tests/test_guard_corpus.py`

**Interfaces:**
- Produces `record.py` functions reused by Task 8:
  - `make_install(base: Path, hooks_src: Path, layout: str) -> Path`
  - `fire(install: Path, hook: str, payload) -> dict` returning `{"decision", "text"}`
  - `inputs() -> list[dict]` (one dict per case: `id`, `layout`, `hook`, `payload`)
  - CLI `python scripts/guard-corpus/record.py --hooks <dir> --out <jsonl>`
- `expected.jsonl`: one JSON object per line, `{"id","layout","hook","payload","decision","text"}`,
  sorted by `id`. `decision` is `allow|advise|ask|deny`; `text` is the reason or context, or `""`.

- [ ] **Step 1: Write the recorder**

```python
#!/usr/bin/env python3
"""Record what the CURRENT hooks decide for a fixed set of inputs (spec §7.1).

The output, tests/guard_corpus/expected.jsonl, is the yardstick for the skyguard port: the new hooks
must reproduce every decision and every reason byte for byte. Rows are generated from:
  * every cmd()/fpath() payload in tests/test_hooks.py, extracted by AST, not retyped;
  * every rule in the bash hooks, each with a positive and a negative input;
  * path dialects, multi-line, CRLF and non-ASCII variants (Plan 2 Review Focus);
  * the empty, invalid and non-object payloads.
Re-record only with --hooks pointing at the hooks whose behaviour is the reference.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BS = chr(92)
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
GAMEW = GAME.replace("/", BS)
LAYOUTS = {
    # name: (install dir name, skyrim-paths.env text)
    "declared": ("Skyrim VR", f'SKYRIM_GAME_ROOT="{GAME}"\n'),
    "undeclared": ("Skyrim VR", ""),
    "metachar": ("Skyrim (VR) [x]$", ""),
    "config": ("Skyrim VR", f'SKYRIM_GAME_ROOT="{GAME}"\nSKYRIM_CONFIG_DIR="D:/Profiles/MO2/profiles/Default"\n'),
}


def bash() -> str:
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(c).is_file():
            return c
    found = shutil.which("bash")
    if found and "System32" not in found:
        return found
    raise SystemExit("no non-WSL bash")


def configure(text: str) -> str:
    """Fill whichever install placeholder the hooks carry, the way setup.sh does."""
    jq = shutil.which("jq") or ""
    return (text.replace("{{JQ_PATH}}", jq.replace(BS, "/"))
                .replace("{{PYTHON_PATH}}", sys.executable.replace(BS, "/")))


def make_install(base: Path, hooks_src: Path, layout: str) -> Path:
    name, env = LAYOUTS[layout]
    root = base / layout / name
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "Data" / "Scripts" / "Source").mkdir(parents=True)
    for f in sorted(hooks_src.glob("*.sh")):
        (root / ".claude" / "hooks" / f.name).write_text(configure(f.read_text(encoding="utf-8")),
                                                         encoding="utf-8", newline="\n")
    if (REPO / "tools" / "skyguard").is_dir():
        shutil.copytree(REPO / "tools" / "skyguard", root / "tools" / "skyguard",
                        ignore=shutil.ignore_patterns("__pycache__"))
    if env:
        (root / ".claude" / "skyrim-paths.env").write_text(env, encoding="utf-8", newline="\n")
    return root


def install_relative(payload: dict, install: Path) -> dict:
    """Fill the `<install>` template of an install-relative row with this run's real install path."""
    return json.loads(json.dumps(payload).replace("<install>", install.as_posix()))


def needs_missing_jq(hooks_src: Path) -> bool:
    """The reference bash hooks need jq; a CI leg without it must skip, not record GUARD INERT rows."""
    uses_jq = any("{{JQ_PATH}}" in f.read_text(encoding="utf-8") for f in hooks_src.glob("*.sh"))
    return uses_jq and shutil.which("jq") is None


def classify(out: str) -> dict:
    out = out.strip()
    if not out:
        return {"decision": "allow", "text": ""}
    h = json.loads(out).get("hookSpecificOutput", {})
    if "permissionDecision" in h:
        return {"decision": h["permissionDecision"], "text": h.get("permissionDecisionReason", "")}
    if "additionalContext" in h:
        return {"decision": "advise", "text": h["additionalContext"]}
    return {"decision": "allow", "text": ""}


def fire(install: Path, hook: str, payload) -> dict:
    stdin = "" if payload is None else (payload if isinstance(payload, str) else json.dumps(payload))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(install))
    for k in ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR"):
        env.pop(k, None)
    r = subprocess.run([bash(), str(install / ".claude" / "hooks" / hook)], input=stdin.encode("utf-8"),
                       capture_output=True, env=env, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"{hook} exited {r.returncode}: {r.stderr[:300]!r}")
    return classify(r.stdout.decode("utf-8"))


def _from_test_hooks() -> list[tuple[str, str]]:
    """Every literal cmd(...)/fpath(...) argument in tests/test_hooks.py, f-strings evaluated."""
    tree = ast.parse((REPO / "tests" / "test_hooks.py").read_text(encoding="utf-8"))
    ns = {"GAME": GAME, "GAMEW": GAMEW, "BS": BS}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("cmd", "fpath") and node.args:
            try:
                value = eval(compile(ast.Expression(node.args[0]), "<arg>", "eval"), {}, dict(ns))
            except Exception:
                continue
            if isinstance(value, str):
                found.append((node.func.id, value))
    return sorted(set(found))


def _bash_payload(c):
    return {"tool_name": "Bash", "tool_input": {"command": c}}


def _write_payload(p):
    return {"tool_name": "Write", "tool_input": {"file_path": p}}


def inputs() -> list[dict]:
    rows = []

    def add(rid, layout, hook, payload):
        rows.append({"id": rid, "layout": layout, "hook": hook, "payload": payload})

    for i, (kind, value) in enumerate(_from_test_hooks()):
        hook = "protect-bash.sh" if kind == "cmd" else "protect-files.sh"
        payload = _bash_payload(value) if kind == "cmd" else _write_payload(value)
        add(f"testhooks-{i:03d}", "declared", hook, payload)

    spellings = {
        "fwd": GAME, "back": GAMEW, "msys": "/c/GOG Games/The Elder Scrolls V Skyrim VR",
        "doubled": GAME.replace("/", "//"), "doubled-back": GAMEW.replace(BS, BS * 2),
        "upper": GAME.upper(), "mixed": "c:\\GOG Games/The Elder Scrolls V Skyrim VR",
    }
    destroyers = ['rm -rf "{p}"', 'rm -r -f "{p}/Data"', 'rm --recursive --force "{p}"', 'rm -rf -- "{p}"',
                  'rmdir /s /q "{p}"', 'del /f /s /q "{p}"', 'Remove-Item -Recurse "{p}"', 'find "{p}" -delete',
                  'python -c "import shutil; shutil.rmtree(r\'{p}\')"', 'erase "{p}/x.txt"']
    for sname, p in spellings.items():
        for j, d in enumerate(destroyers):
            add(f"dialect-{sname}-{j}", "declared", "protect-bash.sh", _bash_payload(d.format(p=p)))
        add(f"dialect-{sname}-ls", "declared", "protect-bash.sh", _bash_payload(f'ls "{p}/Data"'))
        add(f"dialect-{sname}-write-esp", "declared", "protect-files.sh", _write_payload(f"{p}/Data/My.esp"))

    shell_rules = {
        "config-delete": f'rm -rf "C:/Users/u/Documents/My Games/Skyrim VR/x.ini"',
        "reg-delete": "reg delete HKLM\\SOFTWARE\\WOW6432Node\\Bethesda /f",
        "regexe-delete": "reg.exe delete HKLM\\SOFTWARE\\Bethesda /f",
        "remove-itemproperty": "Remove-ItemProperty -Path HKLM:\\SOFTWARE\\Bethesda -Name x",
        "psc-output": "tools/Champollion/Champollion.exe x.pex -o out/MyScript.psc",
        "psc-output-long": 'AutoMod papyrus decompile x.pex --output "src/A.psc"',
        "champollion-pex": f'Champollion "{GAME}/Data/Scripts/MyScript.pex"',
        "champollion-temp": 'Champollion "C:/Temp/MyScript.pex"',
        "delete-inside": f'rm "{GAME}/Data/Textures/x.dds"',
        "relative-data": "rm Data/Textures/x.dds",
        "relative-data-flags": "rm -r -f ./Data/x",
        "relative-mydata": "rm mydata/cache.bin",
        "mv-inside": f'mv a.txt "{GAME}/Data/a.txt"',
        "cp-inside": f'cp a.txt "{GAME}/Data/a.txt"',
        "redirect": f'echo x > "{GAME}/Data/a.txt"',
        "sed-i": f'sed -i "s/a/b/" "{GAME}/Data/a.ini"',
        "plugin-ref": "python read.py Data/My.esp",
        "loadorder-ref": "cat plugins.txt",
        "automod-write-nodry": 'bash tools/automod-cli.sh esp add-weapon "M.esp" X --json',
        "automod-write-dry": 'bash tools/automod-cli.sh esp add-weapon "M.esp" X --dry-run --json',
        "automod-nif": "bash tools/automod-cli.sh nif scale x.nif 2",
        "automod-archive": "bash tools/automod-cli.sh archive create d --output x.bsa",
        "resaver-apply": "bash tools/resaver-cli.sh reset-havok save.ess --apply",
        "resaver-dry": "bash tools/resaver-cli.sh reset-havok save.ess",
        "ext-tool-out": f'Caprica -o "{GAME}/Data/Scripts" x.psc',
        "benign-git": "git status",
        "benign-pytest": "python -m pytest tests/ -q",
        "multiline-destroyer-split": f'rm\n"{GAME}"',
        "multiline-two-cmds": f'echo start\nrm -rf "{GAME}"',
        "crlf": f'rm -rf "{GAME}"\r\n',
        "trailing-newlines": "git status\n\n\n",
        "unicode-path": f'rm "{GAME}/Data/Ëlven Ármor.esp"',
        "unicode-benign": "echo 'Ébène'",
        "compound-tmp": f'rm -rf /tmp/x && ls "{GAME}"',
    }
    for name, c in shell_rules.items():
        add(f"shell-{name}", "declared", "protect-bash.sh", _bash_payload(c))
        add(f"shell-{name}@config", "config", "protect-bash.sh", _bash_payload(c))
    add("shell-config-dir-declared", "config", "protect-bash.sh",
        _bash_payload('rm -rf "D:/Profiles/MO2/profiles/Default/SkyrimPrefs.ini"'))

    write_rules = {
        "esp": f"{GAME}/Data/X.esp", "esm-upper": f"{GAME}/Data/X.ESM", "bsa-trailing-space": f"{GAME}/Data/X.bsa ",
        "claude-hooks": f"{GAME}/.claude/hooks/x.sh", "claude-memory": "C:/x/.claude/memory/m.md",
        "node-modules": "C:/p/node_modules/x/y.js", "skyrim-ini": "C:/Users/u/Documents/My Games/Skyrim VR/Skyrim.ini",
        "skse-ini": f"{GAME}/Data/SKSE/Plugins/x.ini", "plugins-txt": "C:/Users/u/AppData/Local/Skyrim VR/plugins.txt",
        "psc": f"{GAME}/Data/Scripts/Source/X.psc", "pex": f"{GAME}/Data/Scripts/X.pex",
        "data-other": f"{GAME}/Data/meshes/x.nif", "relative-data": "Data/x.txt",
        "outside": "C:/Users/u/Projects/notes.md", "toolkit-checkout": "C:/Users/u/Projects/skyrimvr-claude-toolkit/README.md",
        "unicode": f"{GAME}/Data/Ëlven.txt",
    }
    for name, p in write_rules.items():
        add(f"write-{name}", "declared", "protect-files.sh", _write_payload(p))

    for layout in ("undeclared", "metachar"):
        add(f"root-{layout}-self", layout, "protect-bash.sh", None)  # payload filled at record time
    for hook in ("protect-bash.sh", "protect-files.sh"):
        add(f"payload-empty-{hook}", "declared", hook, None)
        add(f"payload-invalid-{hook}", "declared", hook, "{not json")
        add(f"payload-array-{hook}", "declared", hook, "[1,2]")
        add(f"payload-notool-{hook}", "declared", hook, {"tool_name": "Bash"})
    return sorted(rows, key=lambda r: r["id"])


def record(hooks_src: Path, out: Path) -> int:
    rows = inputs()
    with tempfile.TemporaryDirectory() as td:
        installs = {name: make_install(Path(td), hooks_src, name) for name in LAYOUTS}
        lines = []
        for r in rows:
            install = installs[r["layout"]]
            payload = r["payload"]
            if r["id"].startswith("root-"):          # delete the install's own Data, by its real path
                # Stored as a template so the corpus does not change with every temp dir.
                r = dict(r, payload=_bash_payload('rm -rf "<install>/Data"'), payload_note="install-relative")
                payload = install_relative(r["payload"], install)
            res = fire(install, r["hook"], payload)
            lines.append(json.dumps({**r, **res}, ensure_ascii=False, sort_keys=True))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hooks", type=Path, default=REPO / ".claude" / "hooks")
    ap.add_argument("--out", type=Path, default=REPO / "tests" / "guard_corpus" / "expected.jsonl")
    a = ap.parse_args()
    print(f"recorded {record(a.hooks, a.out)} rows from {a.hooks}")
```

Note: rows whose payload is install-relative (`root-*`) are re-derived at fire time in both the recorder
and the test, because the temp path differs on every run. `payload_note` marks them.

- [ ] **Step 2: Write the corpus test (it must pass against the CURRENT hooks)**

```python
"""Every hook verdict in tests/guard_corpus/expected.jsonl, re-checked against the shipped hooks.

Recorded from the bash hooks before the skyguard port (Plan 2, Task 1). After the port, the new
wrappers must reproduce every decision AND every reason byte for byte (spec §7.2).
"""
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest

from conftest import REPO

EXPECTED = REPO / "tests" / "guard_corpus" / "expected.jsonl"


def _rec():
    spec = importlib.util.spec_from_file_location("record", REPO / "scripts" / "guard-corpus" / "record.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROWS = [json.loads(line) for line in EXPECTED.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def installs():
    rec = _rec()
    if rec.needs_missing_jq(REPO / ".claude" / "hooks"):
        pytest.skip("the shipped hooks still need jq, and it is not installed")
    with tempfile.TemporaryDirectory() as td:
        yield rec, {name: rec.make_install(Path(td), REPO / ".claude" / "hooks", name) for name in rec.LAYOUTS}


def test_corpus_is_not_trivially_small():
    assert len(ROWS) >= 150, len(ROWS)
    assert {r["decision"] for r in ROWS} >= {"allow", "advise", "ask", "deny"}


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_hook_reproduces_the_recorded_verdict(installs, row):
    rec, inst = installs
    install = inst[row["layout"]]
    payload = row["payload"]
    if row.get("payload_note") == "install-relative":
        payload = rec.install_relative(payload, install)
    got = rec.fire(install, row["hook"], payload)
    assert (got["decision"], got["text"]) == (row["decision"], row["text"])
```

- [ ] **Step 3: Record, read every row's decision distribution, and run the test**

Run: `python scripts/guard-corpus/record.py` → `recorded N rows` (N ≥ 150; expect ~230).
Run: `python -c "import json,collections;print(collections.Counter(json.loads(l)['decision'] for l in open('tests/guard_corpus/expected.jsonl',encoding='utf-8')))"`
Expected: all four decisions present. **Read every `deny` row and every `shell-*` row's text by eye.**
The corpus records today's behaviour, wrong or right. A row whose verdict looks wrong is NOT fixed here;
note it in the ledger as `Task 1: observation:` (it becomes a Plan 5 corpus case).
Run: `python -m pytest tests/test_guard_corpus.py -q -p no:cacheprovider` → all PASS.

- [ ] **Step 4: Prove the corpus test can fail.** Temporarily change one byte of one recorded `text` in
`expected.jsonl` and re-run: exactly that row fails. Restore with `git checkout -- tests/guard_corpus/expected.jsonl`
(after the first commit) or re-record. Write `tests/guard_corpus/README.md`: what the corpus is, that
`record.py` regenerates it **only** from reference hooks, how rows are sourced, and the install-relative note.

- [ ] **Step 5: Commit** — `git add scripts/guard-corpus tests/guard_corpus tests/test_guard_corpus.py`
`git commit -m "Record a guard corpus from the current bash hooks (Plan 2 yardstick)"`.

---

### Task 2: `skyguard` core — line-wise grep, path roots, audit log, verdict

**Files:**
- Create: `tools/skyguard/__init__.py`, `tools/skyguard/grep.py`, `tools/skyguard/paths.py`,
  `tools/skyguard/audit.py`, `tools/skyguard/verdict.py`
- Test: `tests/test_skyguard_core.py`

**Interfaces (produced):**
- `grep.grep(pattern: str, text: str, icase: bool = True) -> bool`
- `paths.path_to_regex(path: str) -> str | None`
- `paths.read_env(path: Path, environ: dict) -> dict` (environment first, then the file overrides)
- `paths.Roots(game: str, config: str)` (regex alternations, `""` if none) and
  `paths.roots(root_posix: str, root_win: str, env: dict) -> Roots`
- `audit.Audit(project: str)` with `.log(hook, level, msg)`, `.raw(line_bytes_or_str)`, `.stamp() -> str`
- `verdict.Verdict(decision: str = "allow", reason: str = "", context: str = "")`;
  `verdict.Advice` accumulator with `.add(msg)` and `.joined()`.

- [ ] **Step 1: Write the failing tests**

```python
"""skyguard core: grep semantics, path roots, audit log format, Python floor."""
import ast
import sys
from pathlib import Path

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, grep, paths, verdict  # noqa: E402

BS = chr(92)
SEP = r"[/\\]+"


def test_grep_is_line_wise():
    # [[:space:]] cannot cross a newline in grep; Python's \s can. The port evaluates per line.
    assert not grep.grep(r"rm\s+/x", "rm\n/x")
    assert grep.grep(r"rm\s+/x", "echo a\nrm /x")


def test_grep_case_flag():
    assert grep.grep("REMOVE-ITEM", "remove-item x")
    assert not grep.grep(r"^\s*ls\s", "LS x", icase=False)


def test_path_regex_windows_dialects_all_match():
    rx = paths.path_to_regex("C:/GOG Games/Skyrim VR")
    for s in ["C:/GOG Games/Skyrim VR", "C:" + BS + "GOG Games" + BS + "Skyrim VR", "/c/GOG Games/Skyrim VR",
              "C://GOG Games//Skyrim VR", "C:" + BS * 2 + "GOG Games" + BS * 2 + "Skyrim VR", "c:/gog games/skyrim vr"]:
        assert grep.grep(rx, s), s


def test_path_regex_escapes_metacharacters():
    rx = paths.path_to_regex("C:/Games/Skyrim (VR) [x]$")
    assert grep.grep(rx, "rm -rf C:/Games/Skyrim (VR) [x]$/Data")
    assert not grep.grep(rx, "C:/Games/Skyrim VR x/Data")


def test_path_regex_rejects_relative_and_bare_drive():
    assert paths.path_to_regex("Data/x") is None
    assert paths.path_to_regex("C:/") is None
    assert paths.path_to_regex("/home/u") == SEP + "home" + SEP + "u"


def test_env_file_overrides_environment_and_parses_both_quote_styles(tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_GAME_ROOT='C:/A $B'\nSKYRIM_CONFIG_DIR=\"D:/P\"\n# comment\nbad line\n", encoding="utf-8")
    env = paths.read_env(f, {"SKYRIM_GAME_ROOT": "X", "SKYRIM_LOADORDER_DIR": "L"})
    assert env["SKYRIM_GAME_ROOT"] == "C:/A $B" and env["SKYRIM_CONFIG_DIR"] == "D:/P"
    assert env["SKYRIM_LOADORDER_DIR"] == "L"


def test_roots_dedupe_identical_spellings_only_like_bash():
    # bash's add_root dedupes by exact ERE text: the same spelling twice is one root, but /c/ and C:/
    # render different text (drive-letter case), so both are kept -- harmless, and what bash does.
    one = paths.path_to_regex("C:/G/S")
    assert paths.roots("C:/G/S", "C:/G/S", {}).game == one
    assert paths.roots("/c/G/S", "C:/G/S", {}).game == paths.path_to_regex("/c/G/S") + "|" + one
    assert paths.roots("", "", {}).config == r"Documents[/\\]+My Games[/\\]+Skyrim"


def test_audit_line_format(tmp_path):
    a = audit.Audit(str(tmp_path))
    a.log("protect-bash", "DENY", "why")
    line = (tmp_path / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert line.startswith("[")
    stamp, rest = line[1:].split("] ", 1)
    assert len(stamp) == 15 and stamp[8] == "_" and rest == "protect-bash DENY: why\n"


def test_advice_joins_with_pipes():
    adv = verdict.Advice()
    adv.add("a")
    adv.add("b")
    assert adv.joined() == "a | b"


def test_engine_parses_on_python_3_8():
    for f in sorted((REPO / "tools" / "skyguard").rglob("*.py")):
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f), feature_version=(3, 8))
```

- [ ] **Step 2: Run, and confirm they fail** → `ModuleNotFoundError: skyguard`.

- [ ] **Step 3: Implement**

```python
# tools/skyguard/__init__.py
"""skyguard -- the toolkit's agent-neutral guard engine (spec §5). Stdlib only; Python >= 3.8 syntax."""
__version__ = "1"
```

```python
# tools/skyguard/grep.py
"""`echo "$TEXT" | grep -qE PATTERN`, reproduced: a pattern matches if it matches ANY LINE.

grep is line-oriented, and its [[:space:]] never crosses a newline. Python's \\s does, so every ported
pattern is applied to each line separately. Patterns are written in Python syntax by hand; this
module does not translate POSIX syntax.
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def _compile(pattern: str, icase: bool):
    return re.compile(pattern, re.IGNORECASE if icase else 0)


def grep(pattern: str, text: str, icase: bool = True) -> bool:
    rx = _compile(pattern, icase)
    return any(rx.search(line) for line in text.split("\n"))
```

```python
# tools/skyguard/paths.py
"""Which directories are "the install" and "the config dir" -- ported from protect-bash.sh's
path_to_ere/add_root. Roots are regex alternations matching a path in EITHER dialect (C:/ or /c/)
with ONE OR MORE separators of EITHER kind, case-insensitively (the grep is -i)."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

SEP = r"[/\\]+"
_META = set("[]*+?^$(){}|.\\")
CONFIG_LITERAL = r"Documents[/\\]+My Games[/\\]+Skyrim"
ENV_KEYS = ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR")


def path_to_regex(path: str) -> Optional[str]:
    p = path.replace("\\", "/")
    if re.match(r"^[a-zA-Z]:/", p):
        drive, tail = p[0], p[3:]
    elif re.match(r"^/[a-zA-Z]/", p):
        drive, tail = p[1], p[3:]
    elif p.startswith("/") and len(p) > 1:
        drive, tail = "", p[1:]
    else:
        return None
    if not tail:
        return None
    out = "".join(SEP if c == "/" else ("\\" + c if c in _META else c) for c in tail)
    return "({d}:|{s}{d}){s}{o}".format(d=drive, s=SEP, o=out) if drive else SEP + out


def read_env(path: Path, environ: Dict[str, str]) -> Dict[str, str]:
    """Bash `[ -f env ] && . env`: the environment's values, then the file's assignments over them."""
    env = {k: environ[k] for k in ENV_KEYS if environ.get(k)}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        try:
            parts = shlex.split(value, posix=True)
        except ValueError:
            continue
        env[key] = parts[0] if parts else ""
    return env


class Roots(NamedTuple):
    game: str
    config: str


def _add(acc: List[str], value: str) -> None:
    if not value:
        return
    rx = path_to_regex(value)
    if rx and rx not in acc:
        acc.append(rx)


def roots(root_posix: str, root_win: str, env: Dict[str, str]) -> Roots:
    game: List[str] = []
    _add(game, root_posix)
    _add(game, root_win)
    _add(game, env.get("SKYRIM_GAME_ROOT", ""))
    config = [CONFIG_LITERAL]
    _add(config, env.get("SKYRIM_CONFIG_DIR", ""))
    return Roots("|".join(game), "|".join(config))
```

```python
# tools/skyguard/audit.py
"""The audit trail and timestamps, in the exact line formats the bash hooks wrote."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Union


class Audit:
    def __init__(self, project: str):
        self.project = project
        self.backups = Path(project) / ".claude" / "backups"
        self.path = self.backups / "AUDIT_LOG.txt"

    @staticmethod
    def stamp() -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    def raw(self, line: Union[str, bytes]) -> None:
        try:
            self.backups.mkdir(parents=True, exist_ok=True)
            data = line.encode("utf-8") if isinstance(line, str) else line
            with open(self.path, "ab") as fh:
                fh.write(data)
        except OSError:
            pass                                          # bash: >> "$AUDIT_LOG" 2>/dev/null

    def log(self, hook: str, level: str, msg: str) -> None:
        self.raw("[{}] {} {}: {}\n".format(self.stamp(), hook, level, msg))
```

```python
# tools/skyguard/verdict.py
from __future__ import annotations

from typing import List, NamedTuple


class Verdict(NamedTuple):
    decision: str = "allow"        # allow | ask | deny
    reason: str = ""
    context: str = ""              # advice text for the model


class Advice:
    def __init__(self) -> None:
        self.items: List[str] = []

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def joined(self) -> str:
        return " | ".join(self.items)
```

- [ ] **Step 4: Run the tests** → all PASS. `python -m ruff check tools/skyguard tests/test_skyguard_core.py` → clean.
- [ ] **Step 5: Commit** — `git commit -m "skyguard core: line-wise grep, path roots, audit log, verdicts"`.

---

### Task 3: Port `protect-bash.sh` → `skyguard/shell_rules.py`

**Files:** Create `tools/skyguard/shell_rules.py`; Test `tests/test_skyguard_shell.py`.

**Interfaces:** `shell_rules.decide(command: str, roots: paths.Roots, audit: audit.Audit) -> verdict.Verdict`
(`command` already extracted; the empty-command and no-root refusals live here too).

**The port, rule by rule, in this exact order.** Patterns are Python syntax, applied with
`grep.grep(…, icase=True)`. `{G}` = `roots.game`, `{C}` = `roots.config`, `{A}` = `roots.game + "|" + roots.config`.
Messages are copied **verbatim** from `protect-bash.sh` (line numbers from that file at `9c49c20`);
`$COMMAND` becomes the command string.

| # | Bash line | Condition (Python pattern) | Effect |
|---|---|---|---|
| 0 | 57-61 | `command == ""` | log `REFUSE` "no command in payload -- hook could not evaluate its rules"; deny **GUARD INERT … received no command …** (line 59 text) |
| 0b | 214-218 | `roots.game == ""` | log `REFUSE` "could not derive the install root -- the delete guard evaluated NOTHING"; deny line-216 text |
| 1 | 251-254 | `DESTROYER` and `{C}` | deny line-252 text (config dir) |
| 2 | 251-254 | `DESTROYER` and `{G}` | deny line-253 text (install) |
| 3 | 258 | `(reg(\.exe)?\s+delete\|Remove-ItemProperty.*Bethesda)` | deny "BLOCKED: Cannot delete Bethesda registry keys." |
| 4 | 263 | `-(o\|-output\|-OutputPath)\s+["']?[^"' ]*\.psc(\b\|"\|')` | deny line-263 text |
| 5 | 269-272 | `Champollion\b` and `({G})[/\\]+Data[/\\]+Scripts[/\\]+[^"' /\\]+[.]pex` | deny line-271 text |
| 6 | 281 | `(^\|[;&\|(`]\|\s)(rm\|rmdir\|del\|erase)\s.*({A})` | advise line-281 text |
| 7 | 282 | `RELATIVE_DATA_DELETE` | advise line-282 text |
| 8 | 283 | `(^\|[;&\|(`]\|\s)(mv\|cp\|move\|copy)\s.*({A})` | advise line-283 text |
| 9 | 284 | `>\s*["']?({G}\|{C})` | advise line-284 text |
| 10 | 285 | `sed\s+-i.*({G}\|{C})` | advise line-285 text |
| 11 | 288 | `\.(esp\|esm\|esl\|bsa\|ba2)\b` | advise line-288 text |
| 12 | 289 | `(loadorder\.txt\|plugins\.txt)` | advise line-289 text |
| 13 | 292-294 | `AUTOMOD_ESP_WRITE` and not `--dry-run` | advise line-293 text |
| 14 | 295 | `(automod\|SpookysAutomod).*\b(replace-textures\|rename-strings\|fix-eyes\|scale)\b` | advise line-295 text |
| 15 | 296 | `(automod\|SpookysAutomod).*\b(archive\s+(create\|add-files\|remove-files\|replace-files\|update-file\|merge\|optimize))\b` | advise line-296 text |
| 16 | 302-304 | `(resaver-cli\|ResaverCLI)\b.*\b(reset-havok\|cleanse-formlists\|remove-created)\b` and `--apply` | log ASK; **return ask** with line-303 text (accumulated advice is DROPPED, as in bash) |
| 17 | 307-310 | `\b(automod\|SpookysAutomod\|spookys-automod\|automod-cli\|PapyrusAssembler\|PapyrusCompiler\|Champollion\|Caprica\|spriggit)\b` and `-(o\|-output\|-OutputPath\|-p\|-psc\|-asm\|-a)\s+["']?({G}\|{C})` | advise line-309 text |
| end | 312 | advice non-empty | allow with `context = advice.joined()`; else plain allow |

Every deny logs `DENY <reason>` and every advise logs `ADVISE <msg>` before returning or accumulating (bash `hooklog`).
`DESTROYER = r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s|Remove-Item|shutil\.rmtree|rmtree\s*\(|\s-delete(\s|$)|Remove-ItemProperty"`
`RELATIVE_DATA_DELETE = r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)(\s+-\S*)*\s+(\S*[/\\]+)?Data[/\\]+"`
`AUTOMOD_ESP_WRITE = r"(automod|SpookysAutomod).*\b(add-weapon|add-spell|add-armor|add-npc|add-quest|add-perk|add-book|add-global|add-faction|add-leveled-item|add-form-list|add-encounter-zone|add-location|add-outfit|attach-script|set-property|auto-fill|merge|generate-seq)\b"`
**Each grep is evaluated separately over all lines**, exactly as the bash pipelines are. A conjunction
(rule 2) is two independent any-line checks, not one same-line check.

- [ ] **Step 1: Write the tests.** One positive and one negative per table row, plus order: rule 16 with
advice already accumulated returns `ask` and only the ask text; a command matching rules 6 and 11 gets
both, joined by ` | ` in that order; rule 0 and 0b each log `REFUSE`. Copy each expected message by
extracting it from the bash file inside the test, so a later edit to one copy is caught:

```python
import re
import sys
from pathlib import Path

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules  # noqa: E402

BASH = (REPO / "agent" / "guards" / "legacy-bash" / "protect-bash.sh").read_text(encoding="utf-8")
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"


def msg(line_no):
    """The quoted message of a deny/advise/ask call on a protect-bash.sh line (1-based).

    The three GUARD INERT texts (lines 59, 216; protect-files 84) are emitted through `jq -n` with a
    different quoting, so tests for rules 0/0b take them with `re.search(r'permissionDecisionReason:"(.*?)"', line)`."""
    line = BASH.splitlines()[line_no - 1]
    return re.search(r'(?:deny|advise|ask) "(.*)"', line).group(1)


def run(cmd, tmp_path, game=GAME):
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": game})
    return shell_rules.decide(cmd, r, audit.Audit(str(tmp_path)))


def test_install_delete_denied_with_exact_text(tmp_path):
    v = run(f'rm -rf "{GAME}"', tmp_path)
    assert (v.decision, v.reason) == ("deny", msg(253))


def test_benign_ls_allowed_silently(tmp_path):
    assert run(f'ls "{GAME}/Data"', tmp_path) == ("allow", "", "")


def test_ask_drops_earlier_advice(tmp_path):
    v = run("cat plugins.txt; bash tools/resaver-cli.sh reset-havok s.ess --apply", tmp_path)
    assert v.decision == "ask" and "plugins" not in v.reason
```
Add the remaining rows in the same style: for every row of the table, one test that fires and one that
must not. Where a message contains `$COMMAND`, compare against `msg(n).replace("$COMMAND", cmd)`.

- [ ] **Step 2: Run** → FAIL (no `shell_rules`). **Step 3: Implement** `decide()` exactly per the table,
using `grep.grep`, `verdict.Advice`, and `audit.log("protect-bash", …)`. Messages are built from
string constants at the top of `shell_rules.py`, copied verbatim from the bash file. **Step 4:** tests
PASS and ruff is clean. **Step 5: Commit** `"skyguard: port protect-bash.sh rules (shell guard)"`.

> When this plan moves the bash source in Task 7, the tests' `BASH` path moves with it: point it at
> `tests/guard_corpus/reference/protect-bash.sh`, a copy Task 7 freezes for exactly this purpose.

---

### Task 4: Port `protect-files.sh` → `skyguard/write_rules.py`

**Files:** Create `tools/skyguard/write_rules.py`; Test `tests/test_skyguard_write.py`.

**Interfaces:** `write_rules.decide(file_path: str, audit) -> Verdict`.

| # | Bash line | Condition | Effect |
|---|---|---|---|
| 0 | 82-86 | `file_path == ""` | log `REFUSE` "no file_path in payload -- hook could not evaluate its rules"; deny line-84 text |
| 1 | 98 | `\.(esp\|esm\|esl\|bsa\|ba2)[\s.]*$` | deny "BLOCKED: Cannot directly write to plugin/archive files. Use xelib, Spriggit or AutoMod." |
| 2 | 101 | `(^\|[/\\]+)\.claude[/\\]+(hooks\|plans\|backups\|memory\|projects)[/\\]+` | **silent allow** (no advice, no log) |
| 3 | 102 | `[/\\]+node_modules[/\\]+` | silent allow |
| 4 | 105 | `(Skyrim\.ini\|SkyrimVR\.ini\|SkyrimPrefs\.ini\|SkyrimCustom\.ini)$` | advise line-106 text |
| 5 | 107 | `[/\\]+Data[/\\]+SKSE[/\\]+Plugins[/\\]+.*\.ini$` | advise line-108 text |
| 6 | 109 | `(loadorder\.txt\|plugins\.txt)$` | advise line-110 text |
| 7 | 111 | `\.(pex\|psc)$` | advise line-112 text |
| 8 | 115-122 | only if NO advice yet: `(^\|[/\\]+)Data[/\\]+\|My Games[/\\]+Skyrim` | advise line-121 text |

All are case-insensitive and line-wise; `$FILE_PATH` → `file_path`. **Steps 1–5** as in Task 3 (the `msg()`
helper reads `protect-files.sh`; tests per row plus: rule 8 does not fire when rule 4 fired; a whitelist
hit after nothing is silent). Commit `"skyguard: port protect-files.sh rules (write guard)"`.

---

### Task 5: Port the non-blocking hooks → `backup.py`, `snapshot.py`, `session.py`

**Files:** Create `tools/skyguard/backup.py`, `tools/skyguard/snapshot.py`, `tools/skyguard/session.py`;
Test `tests/test_skyguard_side_effects.py`.

**Interfaces:**
- `backup.run(project: str, tool_name: str, file_path: str, payload_empty: bool, audit) -> None`
- `snapshot.run(project: str, command: str, payload_empty: bool, audit, now: float = None) -> None`
- `session.run(project: str, source: str, bash: str, audit) -> str` (the text to print, possibly `""`)

```python
# tools/skyguard/backup.py
"""Back up a file before it is edited (port of backup-before-edit.sh). Never blocks."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .grep import grep

HOOK = "backup-before-edit"
SKIP = (r"[/\\]\.claude[/\\](backups|hooks|plans)[/\\]", r"[/\\]node_modules[/\\]")


def run(project, tool_name, file_path, payload_empty, audit):
    if payload_empty:
        audit.log(HOOK, "BLIND", "received an EMPTY payload -- no backup was taken for this call")
        return
    if not file_path or not os.path.isfile(file_path):
        return
    if any(grep(p, file_path) for p in SKIP):
        return
    backups = Path(project) / ".claude" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    ts = audit.stamp()
    safe = re.sub(r"[/\\:]", "_", file_path).lstrip("_")
    try:
        shutil.copyfile(file_path, backups / "{}__{}".format(ts, safe))
    except OSError:
        audit.log(HOOK, "FAILED", "could not copy {} -- NO BACKUP EXISTS for this edit".format(file_path))
        return
    audit.raw("[{t}] {n} -> {p} (backup: {t}__{s})\n".format(t=ts, n=tool_name, p=file_path, s=safe))
```

```python
# tools/skyguard/snapshot.py
"""Snapshot .psc/.pex before every non-read-only shell command (port of snapshot-before-tool.sh).

find's -mtime counts whole 24h periods (truncated); -newer is strictly newer; `ls | wc -l` skips
dotfiles; `-name "*.psc"` is case-sensitive. Each is reproduced below."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .grep import grep

SKIP = (r"^\s*(ls|cat|head|tail|grep|find|wc|file|stat|pwd|echo|date|whoami|which|type|env|printenv)\s",
        r"^\s*git\s+(status|log|diff|show|branch|remote|config\s+--get)")
DAY = 86400


def _files(d: Path, suffix: str):
    if not d.is_dir():
        return []
    return [d / n for n in os.listdir(d) if n.endswith(suffix) and (d / n).is_file()]


def _age_days(path: Path, now: float) -> int:
    return int((now - path.stat().st_mtime) // DAY)


def _visible_count(d: Path) -> int:
    return len([n for n in os.listdir(d) if not n.startswith(".")]) if d.is_dir() else 0


def run(project, command, payload_empty, audit, now=None):
    if payload_empty:
        audit.raw("[{}] snapshot-before-tool BLIND: EMPTY payload -- no snapshot taken\n".format(audit.stamp()))
        return
    if any(grep(p, command, icase=False) for p in SKIP):
        return
    now = time.time() if now is None else now
    ts = audit.stamp()
    root = Path(project)
    base = root / ".claude" / "backups" / "auto_snapshots"
    src, dep, rl = root / "Data" / "Scripts" / "Source", root / "Data" / "Scripts", base / ".last_snapshot"
    base.mkdir(parents=True, exist_ok=True)
    if not src.is_dir() and not dep.is_dir():
        return
    if rl.is_file():
        try:
            last = int(rl.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            last = 0
        if int(now) - last < 60:
            rl_mtime = rl.stat().st_mtime
            if not any(f.stat().st_mtime > rl_mtime for f in _files(src, ".psc")):
                return
    snap = base / ts
    (snap / "source").mkdir(parents=True, exist_ok=True)
    (snap / "deploy").mkdir(parents=True, exist_ok=True)
    for f in _files(src, ".psc"):
        if _age_days(f, now) < 7:
            shutil.copyfile(f, snap / "source" / f.name)
    for f in _files(dep, ".pex"):
        if _age_days(f, now) < 7:
            shutil.copyfile(f, snap / "deploy" / f.name)
    psc, pex = _visible_count(snap / "source"), _visible_count(snap / "deploy")
    if psc == 0 and pex == 0:
        shutil.rmtree(snap, ignore_errors=True)
        return
    rl.write_text("{}\n".format(int(now)), encoding="utf-8")
    short = (command + "\n").encode("utf-8")[:120].rstrip(b"\n")
    audit.raw(b"[" + ts.encode() + b"] AUTO-SNAPSHOT (psc:" + str(psc).encode() + b" pex:" + str(pex).encode()
              + b") before: " + short + b"\n")
    for d in base.iterdir():
        if d.is_dir() and _age_days(d, now) > 14:
            shutil.rmtree(d, ignore_errors=True)
```

```python
# tools/skyguard/session.py
"""SessionStart: run tools/kb-guard.sh and report (port of session-kb-guard.sh). Never fails the session."""
from __future__ import annotations

import subprocess
from pathlib import Path

HOOK = "session-kb-guard"


def run(project, source, bash, audit):
    if source == "compact":
        return ""
    tool = "{}/tools/kb-guard.sh".format(project)
    if not Path(tool).is_file():
        audit.raw("[{}] {} NOT RUN: tools/kb-guard.sh missing\n".format(audit.stamp(), HOOK))
        return "KB GUARD: NOT RUN -- {} is missing, so your knowledgebase was NOT snapshotted this session.".format(tool)
    r = subprocess.run([bash, tool], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = r.stdout.decode("utf-8", "replace").rstrip("\n")
    lines = [output] if output else []
    if r.returncode == 2:
        lines.append("KB GUARD: the above means NOTHING was snapshotted this session -- unchecked, not clean.")
    flat = output.replace("\n", " ").encode("utf-8")[:200].decode("utf-8", "ignore")
    audit.raw("[{}] {} rc={} {}\n".format(audit.stamp(), HOOK, r.returncode, flat))
    return "\n".join(lines)
```

- [ ] **Step 1: Tests first**, in temp dirs, with `os.utime` to set ages:
  - backup: copy made with the exact name `TS__C_x_y.txt` for `C:\x\y.txt`, and the audit line format;
    `.claude/backups/…` and `node_modules` skipped; a missing file is silent; an empty payload writes
    `BLIND`; an unreadable source writes `FAILED` (on Windows, make it a directory named like a file).
  - snapshot: a read-only command (`ls x`, `git status`, and a second line `ls y`) is skipped;
    `LS x` is NOT skipped (case-sensitive); only files under 7 days are copied; `.PSC` is ignored; a
    second call within 60 s with no newer `.psc` is skipped and one with a newer `.psc` is not; an
    empty snapshot is removed; dirs older than 14 days are pruned and the new one kept; the audit line
    carries the first 120 bytes; `.last_snapshot` holds the epoch.
  - session: `compact` → `""` and no audit line; a missing tool → the NOT RUN text and the audit line;
    a stub `kb-guard.sh` exiting 2 gets the extra line; the audit line has `rc=2` and the flattened output.
- [ ] **Step 2: Run** (FAIL). **Step 3: Implement** (code above). **Step 4: PASS** and ruff clean.
- [ ] **Step 5: Commit** `"skyguard: port backup, snapshot and session hooks"`.

---

### Task 6: Claude adapter and `run.py`

**Files:** Create `tools/skyguard/adapters/__init__.py`, `tools/skyguard/adapters/claude.py`, `tools/skyguard/run.py`;
Test `tests/test_skyguard_claude_adapter.py`.

**Interfaces:**
- `claude.extract(payload_text: str) -> dict` with keys `empty` (bool), `tool_name` (str, default
  `"unknown"`), `command` (str), `file_path` (str), `source` (str). Each value follows `jq -r '.x // empty'`,
  and the command has trailing `\n` stripped, like `$(…)`.
- `claude.render(verdict) -> str` (`{}` for a silent allow; never empty).
- `run.py <agent> <role> --root R --root-win RW --project P [--bash B]`. Roles: `protect-bash`,
  `protect-files`, `backup-before-edit`, `snapshot-before-tool`, `session-kb-guard`. It prints JSON for
  the tool roles and plain text for `session-kb-guard`. Any exception exits 1 with nothing on stdout.

```python
# tools/skyguard/adapters/claude.py
"""Claude Code <-> skyguard. Translation only (spec §5): native payload in, native reply out."""
from __future__ import annotations

import json


def _jq_r(v) -> str:
    """`jq -r '<path> // empty'`: null/false/missing -> "", strings raw, everything else as JSON."""
    if v is None or v is False:
        return ""
    if isinstance(v, str):
        return v
    if v is True:
        return "true"
    if isinstance(v, (dict, list)):
        return json.dumps(v, indent=2, ensure_ascii=False)
    return json.dumps(v)


def extract(payload_text: str) -> dict:
    empty = payload_text == ""
    try:
        d = json.loads(payload_text) if payload_text.strip() else None
    except ValueError:
        d = None
    d = d if isinstance(d, dict) else {}
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    return {
        "empty": empty,
        "tool_name": _jq_r(d.get("tool_name")) or "unknown",
        "command": _jq_r(ti.get("command")).rstrip("\n"),
        "file_path": _jq_r(ti.get("file_path")).rstrip("\n"),
        "source": _jq_r(d.get("source")).rstrip("\n"),
    }


def render(v) -> str:
    if v.decision in ("deny", "ask"):
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": v.decision,
                                      "permissionDecisionReason": v.reason}}
    elif v.context:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": v.context}}
    else:
        out = {}
    return json.dumps(out, ensure_ascii=False)
```

```python
# tools/skyguard/run.py
"""Entry point the per-agent hook wrappers call: run.py <agent> <role> --root .. --root-win .. --project ..

Exits 1 with NOTHING on stdout on any internal failure, so a wrapper can tell "the engine decided"
from "the engine broke" -- the second must never read as allow (spec §5.1)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skyguard import backup, paths, session, shell_rules, snapshot, write_rules  # noqa: E402
from skyguard.adapters import claude  # noqa: E402
from skyguard.audit import Audit  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

ADAPTERS = {"claude": claude}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", choices=sorted(ADAPTERS))
    ap.add_argument("role", choices=["protect-bash", "protect-files", "backup-before-edit",
                                     "snapshot-before-tool", "session-kb-guard"])
    ap.add_argument("--root", default="")
    ap.add_argument("--root-win", default="")
    ap.add_argument("--project", required=True)
    ap.add_argument("--bash", default="bash")
    a = ap.parse_args(argv)
    adapter = ADAPTERS[a.agent]
    raw = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
    p = adapter.extract(raw)
    audit = Audit(a.project)
    if a.role == "protect-bash":
        if not p["command"]:
            v = shell_rules.decide("", paths.Roots("", ""), audit)
        else:
            env = paths.read_env(Path(a.root) / ".claude" / "skyrim-paths.env", dict(os.environ))
            v = shell_rules.decide(p["command"], paths.roots(a.root, a.root_win, env), audit)
    elif a.role == "protect-files":
        v = write_rules.decide(p["file_path"], audit)
    elif a.role == "backup-before-edit":
        backup.run(a.project, p["tool_name"], p["file_path"], p["empty"], audit)
        v = Verdict()
    elif a.role == "snapshot-before-tool":
        snapshot.run(a.project, p["command"], p["empty"], audit)
        v = Verdict()
    else:
        sys.stdout.write(session.run(a.project, "" if p["empty"] else p["source"], a.bash, audit))
        return 0
    sys.stdout.write(adapter.render(v))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(1)
```

`shell_rules.decide` must check `command == ""` (rule 0) **before** `roots.game == ""` (rule 0b), as bash does.

- [ ] **Step 1: Tests first:**
  - every Plan 1 fixture in `tests/fixtures/payloads/claude/` extracts its `command` or `file_path`;
  - the Review Focus 2 case `test_command_trailing_newlines_are_stripped`;
  - invalid JSON, an array, and `{"tool_input": "x"}` → empty fields and no exception;
  - a numeric command → its JSON text;
  - `render` gives `{}` for a silent allow and the exact Claude shapes otherwise;
  - `run.py` as a subprocess on each role, and exit 1 with empty stdout when an internal error is
    forced (monkeypatch `shell_rules.decide` to raise, via `-c` driver code).
- [ ] **Steps 2–4** RED → GREEN, ruff clean. **Step 5: Commit** `"skyguard: Claude adapter and run.py entry point"`.

---

### Task 7: Switch over — the wrappers become the hooks

**Files:**
- Create: `agent/guards/adapters/claude/{protect-bash,protect-files,backup-before-edit,snapshot-before-tool,session-kb-guard}.sh`
- Move: `agent/guards/legacy-bash/*.sh` → `tests/guard_corpus/reference/` (frozen reference for the Task 3–4 `msg()` helpers and Task 8; NOT shipped, because `tests/` is excluded from the release)
- Modify:
  - `scripts/gen_agent_trees.py`: the hooks come from `agent/guards/adapters/claude/`;
  - `agent/README.md`;
  - `setup.sh`: Python resolution, the `{{PYTHON_PATH}}` substitution loop, a loud missing-Python message, and `jq` demoted only if unused;
  - `tests/test_hooks.py`, `tests/test_kb_guard.py`, `tests/test_hook_canary.py`: the project fixtures substitute `{{PYTHON_PATH}}` and copy `tools/skyguard`;
  - `tests/test_jq_path.py` → `tests/test_python_path.py`;
  - `tests/test_repo_invariants.py`: the placeholder invariant becomes `{{PYTHON_PATH}}`;
  - `tests/test_mutations.py`: re-anchor every entry whose anchor moved;
  - `tools/hook-canary.sh`: the "check that jq is installed" text becomes Python;
  - `.github/workflows/release.yml` and `tools/verify-release.sh`: also require `tools/skyguard/run.py` in the payload;
  - docs: README requirements, `docs/safety-philosophy.md` (customise rules in `tools/skyguard/*_rules.py`), `agent/instructions/core.md` safety section, CHANGELOG.

**The blocking wrapper** (`protect-bash.sh`; `protect-files.sh` is identical except `HOOK_NAME`):

```bash
#!/bin/bash
# Claude Code adapter for skyguard's shell guard. The rules live in tools/skyguard/ (Python);
# this wrapper only reads the payload, records the heartbeat and passes the verdict through.
#
# SETUP: setup.sh fills in the Python path below (it writes sys.executable of the Python it found).
#
# A bare `cat`, never `cat /dev/stdin`: the latter returns ZERO BYTES when Claude Code (Node) spawns a
# hook, which made every hook here inert for five weeks. tools/hook-canary.sh reads the heartbeat.

HOOK_NAME="protect-bash"
PY="{{PYTHON_PATH}}"
INPUT=$(cat)

_SELF="${BASH_SOURCE[0]//\\//}"
HOOK_DIR="$(cd "$(dirname "$_SELF")" 2>/dev/null && pwd)"
ROOT="$(cd "$HOOK_DIR/../.." 2>/dev/null && pwd)"
ROOT_WIN="$(cd "$HOOK_DIR/../.." 2>/dev/null && pwd -W 2>/dev/null)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$ROOT}"
HB_DIR="$PROJECT_DIR/.claude/backups/.hook-heartbeat"
mkdir -p "$HB_DIR" 2>/dev/null

inert() {
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"GUARD INERT: %s %s, so it evaluated NO rules and cannot report a decision. Allowing silently would be indistinguishable from approving."}}\n' \
        "$HOOK_NAME" "$1"
    exit 0
}

PY_SHOWN="${PY//\\//}"
# `command -v` is a builtin: no extra interpreter start per call. An unsubstituted placeholder or a
# missing path fails here; a present-but-broken Python fails below as "engine failed".
command -v "$PY" >/dev/null 2>&1 || inert "cannot run Python ($PY_SHOWN) -- install Python 3 and run setup.sh to configure its path"

if [ -n "$INPUT" ]; then
    printf '%s payload_bytes=%s\n' "$(date +%Y%m%d_%H%M%S)" "${#INPUT}" > "$HB_DIR/$HOOK_NAME" 2>/dev/null
else
    printf '%s NO PAYLOAD\n' "$(date +%Y%m%d_%H%M%S)" > "$HB_DIR/$HOOK_NAME.blind" 2>/dev/null
fi

OUT=$(printf '%s' "$INPUT" | "$PY" "$ROOT/tools/skyguard/run.py" claude "$HOOK_NAME" \
      --root "$ROOT" --root-win "$ROOT_WIN" --project "$PROJECT_DIR" 2>/dev/null)
RC=$?
case "$OUT" in
    "{"*) [ "$RC" -eq 0 ] && { printf '%s\n' "$OUT"; exit 0; } ;;
esac
inert "engine failed (exit $RC) -- run: bash tools/hook-canary.sh"
```

**The non-blocking wrappers** (`backup-before-edit`, `snapshot-before-tool`) are the same down to the
heartbeat, but they have no `inert()`. When Python can't run they append
`[TS] <hook> FAILED: Python not runnable (<path>) -- NO backup/snapshot was taken` to `AUDIT_LOG.txt`
and exit 0. They run the engine and discard stdout; a nonzero exit appends
`[TS] <hook> FAILED: engine error (exit RC) -- NO backup/snapshot was taken`.

**`session-kb-guard.sh`** works the same way, but passes `--bash "$BASH"` and prints the engine's
text with `[ -n "$OUT" ] && printf '%s\n' "$OUT"`. When Python can't run it prints
`KB GUARD: NOT RUN -- Python is not runnable (<path>), so your knowledgebase was NOT snapshotted this session.`,
writes the matching audit line, and exits 0.

- [ ] **Step 1: Freeze the reference and add the wrapper tests.** `git mv agent/guards/legacy-bash tests/guard_corpus/reference`;
point the Task 3–4 tests' `BASH` paths at it.
  - Add `test_wrapper_denies_when_engine_crashes`: replace `tools/skyguard/run.py` in a fixture copy with `raise SystemExit(3)`, then expect deny `GUARD INERT … engine failed (exit 3)`.
  - Add `test_wrapper_denies_on_non_json_output`: `print("hi")`, then expect deny.
  - Add `test_wrapper_denies_without_python`: `{{PYTHON_PATH}}` left unsubstituted, then expect deny `cannot run Python`.
  - Add the non-blocking twins: an audit `FAILED` line and no stdout.
- [ ] **Step 2: Write the five wrappers, update the generator mapping and `agent/README.md`, regenerate**
(`python scripts/gen_agent_trees.py`), and update the fixtures in `test_hooks.py`, `test_kb_guard.py` and `test_hook_canary.py`.
In each fixture, substitute `{{PYTHON_PATH}}` with `sys.executable` (forward slashes) and copy `tools/skyguard` into the fixture install.
- [ ] **Step 3: `setup.sh`.**
  - After the Python detection block, set `PYTHON_PATH=$($PY_FOUND -c 'import sys; print(sys.executable)' | tr -d '\r' | tr '\134' '/')`.
  - The hook loop substitutes `{{PYTHON_PATH}}`, and is otherwise unchanged in shape: missing-file check → placeholder check → `sed -i` → "Already configured".
  - If no Python: print "WARNING: the safety hooks need Python 3 and will REFUSE every command until it is installed and setup.sh is re-run."
  - `grep -rn '"\$JQ"\|jq ' tools/ .claude/hooks scripts/ setup.sh`: if nothing that ships still runs `jq`, make the `jq` block informational (not fatal, no winget install), and record a `Ruling:`. Otherwise leave it and ledger why.
  - `test_python_path.py` (renamed from `test_jq_path.py`) asserts the wrappers hold a real interpreter path, with no backslash corruption.
- [ ] **Step 4: Re-anchor the mutation gate.** Run the mutation tests; each entry whose anchor no longer
exists fails with "anchor not found". Re-target each one to the equivalent construct in
`tools/skyguard/*.py` (for example the rule-2 deny → the `GAME` conjunction in `shell_rules.py`), with the same
guarding test. Add one new entry per wrapper property: removing the `case "$OUT"` guard must turn
`test_wrapper_denies_on_non_json_output` red. **Every entry must still be killed.**
- [ ] **Step 5: THE EQUIVALENCE GATE ON THE CORPUS.** `python -m pytest tests/test_guard_corpus.py -q`
→ **every row PASSES against the new wrappers**, with zero edits to `expected.jsonl`. A failing row is fixed in the
engine (systematic-debugging), never in the corpus. Then the full suite and `npm test` → green, and
`python scripts/gen_agent_trees.py --check` → in sync.
- [ ] **Step 6: Release gates and docs**, then commit, in two commits:
  - `"Hooks become thin Claude adapters over skyguard (zero verdict change, corpus-proven)"`
  - `"setup.sh: require Python for the hooks, configure its path"`

---

### Task 8: One-time equivalence replay over real command history (spec §7.2)

**Files:** Create `scripts/guard-corpus/replay.py`, `docs/superpowers/measurements/2026-10-XX-plan2-equivalence.md`;
append new rows to `tests/guard_corpus/expected.jsonl` via `record.py` only.

**What it does:**
1. Extract every Bash `command` and Edit/Write `file_path` from `tool_use` blocks in
   `~/.claude/projects/*/*.jsonl` (this machine's real history), de-duplicated.
2. Fire each one at the OLD hooks (`tests/guard_corpus/reference/`) and at the NEW wrappers, in a
   `declared` install. Use a process pool and write nothing in the repo while it runs.
3. Compare `(decision, text)` per item.

The raw commands are **never committed** (they can hold secrets and paths). Commit only counts.

- [ ] **Step 1: Write the replay script.** Reuse `record.make_install`/`record.fire`. The `--old` and `--new` hook dirs, `--jobs N` and `--out-dir` (outside the repo) are arguments. It prints the total N, the agreement count, and a per-decision old×new matrix. It writes the disagreements as JSONL to `--out-dir`.
- [ ] **Step 2: Run it** in the background with `run_in_background` (thousands of items at roughly 0.3–0.5 s per old-hook call).
- [ ] **Step 3: Read EVERY disagreement by hand.**
  - Each one is either an engine bug, fixed RED→GREEN with a new corpus row recorded from the reference hooks,
  - or a documented reason (for example a locale-dependent `\b` on non-ASCII text, Review Focus 5) with a `Ruling:`.
  - Gate: **zero unexplained differences.** Where the OLD hook was stricter, write an explicit line in the measurements doc.
- [ ] **Step 4: Write the measurements doc:**
  - N and the date;
  - the agreement count and the matrix;
  - each disagreement class with a scrubbed example (scrubbed with `tools/payload-capture/scrub.py`-style rules) and its resolution.
  Commit: `"Plan 2 equivalence replay: N real commands, 0 unexplained differences"`.

---

### Task 9: End-to-end verification (release gate for Plan 2)

- [ ] **A fresh clone** with `core.autocrlf=true`: `--check` in sync, and `git status` clean.
- [ ] **A release-style copy** (rsync excluding `.git .github tests`) into a fake game dir, then `bash setup.sh </dev/null`:
  - the wrappers hold a real Python path;
  - firing each hook with the corpus' 10 most important rows gives the recorded verdicts;
  - the canary reports 5/5 ALIVE after one call each.
- [ ] **Python missing:** copy the install, set the wrapper's `PY` to a nonexistent path, then a `Write` to `Data/X.esp` → deny `cannot run Python`.
- [ ] **Live `claude -p`** in the configured copy: a Write to `Data/Decoy.esp` and an `rm` in `Data/` are both blocked with the reason visible, while `ls` is allowed. The heartbeats and `AUDIT_LOG.txt` prove the copy's own hooks answered.
- [ ] **Latency (spec M4):** time 50 invocations of each wrapper through `bash <wrapper>` on corpus payloads. Gate: p95 ≤ 1 s, and compare it with the Plan 1 spike numbers (E10). Record the result in the measurements doc.
- [ ] **Final whole-range review** on the most capable model, with this plan's Review Focus. Then the fix pass, and the full suite.

## Spec coverage of Plan 2

| Spec section | Task |
|---|---|
| §5 one engine | 2–6 |
| §5.2 contract (minimal Claude-only form; extended for Codex in Plan 4) | 6 |
| §5.3 adapter duties + wrapper self-deny | 6, 7 |
| §5.1 fail-by-deny | 6, 7 |
| §7.1 corpus | 1 |
| §7.2 equivalence gate | 7 (corpus), 8 (history) |
| §7.4 mutants | 7 |
| §9 M4 latency | 9 |

Deferred by design:
- new rules (PowerShell, MO2, DevBench, E16, E21, the `Bash|PowerShell` matcher) → Plan 5;
- Codex → Plan 4;
- the instruction split and D15 preference injection → Plan 3.
