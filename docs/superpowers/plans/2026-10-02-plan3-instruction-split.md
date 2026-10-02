# Plan 3 — instruction split, local preferences injection, neutrality gate

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the shared instruction core (`agent/instructions/core.md`) so that the instruction file Codex
reads (`AGENTS.md` = core + Codex addendum, Plan 4) stays at or under **32,768 bytes as installed**. Codex
truncates past that silently (E7). Move the bulk into three new skills and into `docs/`, deleting
nothing. Make the core agent-neutral and prove it with a gate. Put the user's standing preferences
(`KNOWLEDGEBASE.local.md` → `## My preferences`) in front of every named agent at session start (D10,
D15). Land the toolkit prompt-audit fixes the spec lists (§6).

**Architecture:** `scripts/gen_agent_trees.py` gains a **byte budget**: the worst-case *installed* size
of header + core + each addendum, with room held for the Codex addendum until Plan 4 writes it. It is
reported by `--budget` and enforced by `--check`, the existing CI drift step. A new pytest module,
`tests/test_instruction_neutrality.py`, bans Claude-only tool names, variables, slash commands and config
paths from the core and from the three new skills. Claude-specific text moves to the Claude addendum
`agent/instructions/claude.md`, which is generated into `CLAUDE.md` only. Preferences are injected by a
new pure function, `tools/skyguard/prefs.py`, which the existing SessionStart role
(`session.run`) appends to its output. No new hook, no wrapper change and no `settings.json` change are
needed.

**Tech Stack:** Python ≥ 3.8 syntax, stdlib only (3.10 locally, 3.11 in CI); bash; pytest 8.

**Spec:** `docs/superpowers/specs/2026-09-30-universal-agent-support-design.md`: §4 source layout, §6
instructions, D9, D10, D15, §7.7 CI gates, §10 phase 4. Roadmap row "Plan 3":
`C:\Users\Moona\.claude\plans\looks-good-lovely-fern.md`. Predecessors: Plan 1 (generator) and Plan 2
(`skyguard`, whose last tasks are still landing; see Task 8's precondition).

## Measured inventory (2026-10-02, `master` @ `fd71271`)

All sizes are LF bytes, from `git cat-file -p HEAD:<path>`. Working copies here are CRLF in places, so
`wc -c` on disk over-counts; the generator normalises to LF.

| What | Bytes | How measured |
|---|---|---|
| `agent/instructions/core.md` | **53,367** | blob; on disk 54,053 (686 CRLF) |
| generated `CLAUDE.md` (header 211 + core + empty `claude.md`) | 53,578 | blob |
| generated `AGENTS.md` (the pointer, not the core) | 3,805 | blob |
| `agent/instructions/claude.md` | 0 | blob |
| 9 existing skills, total | 23,646 | `wc -c` |

Per-section LF bytes of `core.md`, for the sections this plan moves:

| Section (heading) | Bytes | Destination |
|---|---|---|
| Installing the Optional Tools + Reliability fixes | 6,765 + 1,364 = 8,129 | `docs/installing-tools.md` (fix 3 → skill `xelib`, Ruling R9) |
| AutoMod CLI + When to Use Which Tool | 1,018 + 610 = 1,628 | existing skills (they duplicate it) |
| skyrim-winner + papyrus-triage + crash-triage + Where these tools look | 2,017 + 936 + 1,297 + 1,004 = 5,254 | skill `triage-and-conflicts` |
| DevBench | 4,789 | skill `devbench` |
| Optional Devcontainer | 1,920 | `docs/container-vs-windows.md` (R7) |
| XEditLib.dll API | 1,407 | skill `xelib` |
| **Spec §6 table total** | **23,127** | core after: **30,240** |
| Safety Rules: install-resolution history, fail-closed detail, "why so few prompts", canary mechanics | ≈ 2,200 | `docs/safety-philosophy.md` (R8) |
| Knowledgebase → "The copy that covers the install you already have" (kb-guard internals) | 1,604 → ≈ 450 kept | `docs/safety-philosophy.md` (R8) |
| Nexus API paragraph (keeps the one-line standing rule) | ≈ 900 | `docs/installing-tools.md` (R8) |
| ESP Cross-Reference Integrity | 843 | skill `triage-and-conflicts` (R8) |

The spec's E9 (53,772) and its §6 table were measured on CRLF bytes: 6,814 + 1,370 = 8,184 matches the
spec's "8,184" exactly. That is why the numbers above are a few percent lower. **Always measure through
the generator (`--budget`), never with `wc -c` on a Windows working copy.**

**Budget arithmetic (Ruling R1).** Limit 32,768 = header 211 + core + addendum + install expansion.
Install expansion is held at 2,560 bytes and the Codex addendum at 2,048 bytes until `codex.md` exists. That
leaves **core ≤ 27,949**. This plan's **target is core ≤ 26,500**, which keeps ~1.4 KB of headroom for the
core to grow before CI goes red. Estimate after all the moves above, plus the new pointer table (+≈1,000)
and the preferences paragraph (+≈620): **≈ 26,000** (INFERRED; Task 7 measures it).

## Rulings on spec ambiguities (each confirmable at plan review)

- **R1. The budget is the worst-case INSTALLED size, not the repo file.** It is header + LF core + addendum
  + `INSTALL_EXPANSION_RESERVE` (2,560) and, until `codex.md` exists, + `CODEX_ADDENDUM_RESERVE` (2,048).
  — *Why:* Codex reads the installed `AGENTS.md`, after `setup.sh` has filled `{{GAME_ROOT}}` and friends
  and spliced in the MO2 block (`setup.sh:381-407`: about 1,250 bytes of template plus 7 path insertions).
  `project_doc_max_bytes` only works in a trusted directory (E7). A repo-only gate would be green on an
  install that truncates. — *Cost if wrong:* if the reserves are too generous, the core gets cut deeper than
  it needs to be (recoverable: lower the constant). If too small, a user with very long MO2 paths, in an
  untrusted Codex directory, loses the tail of `AGENTS.md`: the Principles sections.
- **R2. Neutrality gate scope = `core.md` + the three NEW skills.** The nine existing skills are out of
  scope, because spec §4 lets them keep Claude frontmatter and `$ARGUMENTS`, and Plan 4 adapts their bodies.
  `.claude/backups/` is **allowed** in neutral text, because the engine writes there for every agent
  (`tools/skyguard/audit.py:12`). `.claude/hooks|settings|skills|commands|agents` are banned. — *Why:* the
  spec names "the core"; the new skills are written now, so making them neutral costs nothing. —
  *Cost if wrong:* if the backups path should be neutral too, Plan 8's path move must re-touch the core
  (≈5 lines).
- **R3. Injection piggybacks on the existing SessionStart role (`session-kb-guard` → `session.run`). No
  new hook.** — *Why:* a new hook means a new `settings.json` entry, heartbeat, setup placeholder, canary
  row, release hook count, and for Codex one more **frozen** `hooks.json` definition (E18). Here it is
  one function call. — *Cost if wrong:* if preferences later need their own toggle or heartbeat, splitting
  them out after Plan 4 freezes the Codex definitions costs a "re-approve your hooks" release.
- **R4. Preferences are injected on `compact` too**, while the kb-guard snapshot is still skipped on
  `compact`. — *Why:* compaction drops earlier context, and SessionStart(compact) is exactly the re-inject
  point. Without this, preferences vanish mid-session. — *Cost if wrong:* duplicate preference text after
  a compact. That is harmless, and the cap (R5) bounds it.
- **R5. The injected section is capped at 4,096 bytes of body**, cut on a UTF-8 boundary, with a notice
  naming the file. — *Why:* SessionStart output lands in every session's context, and a runaway
  section should not crowd out the task. The exact limit on Claude Code hook output is **ASSUMED** to be
  well above this (around 10,000 characters). Task 9 measures it. — *Cost if wrong:* a user with a long
  section gets a truncated injection. The fallback instruction (read the file) still covers it.
- **R6. `AGENTS.md` stays the generated pointer in Plan 3.** The Codex target (core + `codex.md`) is Plan 4.
  The budget gate already measures the would-be `AGENTS.md`, so Plan 4 lands under budget without a
  second split. The pointer is corrected: skills are readable Markdown under `.claude/skills/`, the garbled
  item 4 is fixed, and the stale "require confirmation" claim goes. — *Cost if wrong:* if the user wants
  unknown agents to get the full core now, Plan 4's Task 1 moves forward (≈30 lines).
- **R7. The devcontainer section is appended to the existing `docs/container-vs-windows.md`**, which the
  section already calls "the full routing guide". No new file is created. — *Cost if wrong:* trivial;
  split it out later.
- **R8. More moves than the spec's §6 table.** Safety-rules internals, kb-guard internals, Nexus API
  detail, and ESP cross-reference all move. — *Why:* under R1 the spec table alone leaves core ≈ 27,470
  (INFERRED), over the 26,500 target. The spec explicitly allows the Safety Rules internals to move "if
  over". The rest are the next-cheapest moves that lose nothing (each keeps a one-line rule in the core). —
  *Cost if wrong:* if the user wants any of these kept in the core, the budget forces an equal cut elsewhere.
  The ordered fallback list (Task 7) names where.
- **R9. Reliability fix 3 (xelib: SSE `plugins.txt` hazard, `loadActive()`, run as a `.js` file) goes to
  skill `xelib`, not to the install doc.** — *Why:* it is a usage rule, read when writing a script, not
  when installing. — *Cost if wrong:* nil; the install doc links it.
- **R10. No shipped `KNOWLEDGEBASE.local.md` template.** The core tells the agent to start a new file with
  an empty `## My preferences` section. — *Why:* the release must never contain the local file (release.yml
  gate). A `.example` would be one more file for a user to mistake for the real one. Plan 8's `setup.sh` may
  create it. — *Cost if wrong:* the first session without the section injects nothing until the user or
  agent adds it; the fallback instruction still applies.
- **R11. Prompt-audit fixes are written for the TOOLKIT, not copied from the install.** The install's
  versions say "installed here", name `planck.psc` and `_CLAUDE_OUTPUT`. Each fix is verified first (Task 6);
  where verification is impossible here, the text drops the claim rather than asserting it.

## Global Constraints

- **Nothing is deleted** (spec §6). Every moved line lands in a destination, or is reworded with a reason.
  This is proven once by the conservation check (Task 7, Step 4).
- **Edit `agent/` and `docs/` only, then regenerate.** Never edit `CLAUDE.md`, `AGENTS.md` or `.claude/**`
  directly. `python scripts/gen_agent_trees.py --check` stays green at every commit.
- **The generator stays stdlib-only and 3.8-compatible.** `tools/skyguard/prefs.py` must pass
  `test_engine_parses_on_python_3_8`: no `match`, no runtime `X | Y`, no 3.9+ APIs.
- **SKILL.md frontmatter stays the first bytes** (CI "Skill frontmatter is well-formed"); `name` equals the
  directory name.
- **Session start never fails** (Plan 2 constraint). A preferences failure is a printed line plus an audit
  line, never an exception or a non-zero exit.
- **Plan 2's area is touched in Task 8 only**, which is last and has a precondition. It touches
  `tools/skyguard/session.py` (one import, one helper, three return sites), a new `tools/skyguard/prefs.py`,
  and tests. **No change** to `run.py`, the wrappers, `settings.json`, or `tests/guard_corpus/`.
- Commit each finished task on `master`; **never push.** Test command:
  `python -m pytest tests/ -q -p no:cacheprovider`.
- **Never edit the tree while the suite or a mutation run is executing** (project rule).

## Review Focus

1. **Measuring the wrong bytes.** A CRLF working copy, the header, or `setup.sh`'s expansion each move
   the number. → All measurement goes through `instruction_sizes()` (LF + header + reserves), and
   `test_install_expansion_fits_its_reserve` runs the REAL `setup.sh` on an MO2 fixture (Tasks 1, 7).
2. **A gate that cannot fail.** The neutrality gate passes vacuously if it reads a renamed or empty
   file. The budget gate passes if `--check` stops calling it. → `test_gate_reads_the_real_files`, one
   positive example per banned pattern, and mutation entries for each constant and each wiring (Task 7).
3. **A gate that bites ordinary wording.** "Read both files", "Write pass", "bash tools/x.sh", "Git Bash"
   and "the hook" must not trip it, or authors will reword the core badly to get past it. →
   `NEGATIVE` examples (Task 2).
4. **Content lost or left dangling in the move.** Moved text that said "see below" or "the XEditLib notes"
   (which never covered exit 127), and links that break. → the conservation check, the CI link step, and an
   explicit cross-reference sweep (Tasks 3–5, 7).
5. **Markdown edge cases in `## My preferences`.** CRLF, BOM, a `##` inside a code fence, a `###`
   subsection, an empty section, a very long one, a multibyte character at the cap, the file being a
   directory. → `tests/test_skyguard_prefs.py` (Task 8).
6. **Preferences proven through the real entry point**, not through `prefs.render()` alone. → a wrapper-level
   test, plus a live `claude -p` run with file-reading tools DISABLED. That control proves the text arrived
   via the hook and not because the agent read the file (Task 9).

## Interfaces handed to Plan 4

- `gen_agent_trees.INSTRUCTION_BUDGET = 32768` stays the CI limit (D9). Plan 4 writes
  `agent/instructions/codex.md`. From then on `instruction_sizes()` uses its **real** size and the
  `CODEX_ADDENDUM_RESERVE` row disappears on its own. The Codex target renders `AGENTS.md` from
  `core.md + codex.md` with the same `_headed()` call.
- **D9:** Plan 4's generated `.codex/config.toml` carries `project_doc_max_bytes = 65536`, as margin only.
  CI still fails at 32,768 through `--check`.
- **D15 on Codex:** the Codex adapter's SessionStart role prints `session.run()`'s text unchanged. E5: Codex
  SessionStart stdout reaches the model. No Codex-specific preference code is needed.
- **Plan 5 handoff:** the Claude addendum states "the PowerShell tool is not matched yet". When Plan 5
  widens the matcher to `Bash|PowerShell`, it must edit that line.
- **Raised for Plan 4/8 (not fixed here):** `tools/kb-guard.sh` `WATCHED` lists `CLAUDE.md` but not
  `AGENTS.md`. Once `AGENTS.md` carries installed paths (Plan 4), it should be watched too.

---

### Task 1: Byte budget in the generator (machinery only)

**Files:** Modify `scripts/gen_agent_trees.py`, `tests/test_gen_agent_trees.py`.

**Interfaces:**
- Constants `INSTRUCTION_BUDGET = 32768`, `CODEX_ADDENDUM_RESERVE = 2048`, `INSTALL_EXPANSION_RESERVE = 2560`,
  `NOT_ADDENDA = ("core.md", "agents-pointer.md")`.
- `instruction_sizes(src: Path) -> dict[str, int]`: worst-case installed bytes for each instruction file
  that is core + an addendum. Keys are addendum names, plus `"codex.md (reserved)"` while `codex.md` is
  absent.
- `budget_problems(src: Path) -> list[str]`: one line per size over the budget.
- CLI `--budget`: prints every size and the headroom; exits 1 if any is over. **In this task `--check` does
  NOT call it yet.** The real core is 53,367 bytes and would turn CI red; Task 7 wires it in.

- [ ] **Step 0: Record the pre-split blob** (the conservation check in Task 7 needs it):

```bash
cd /c/Users/Moona/Projects/skyrimvr-claude-toolkit
git rev-parse HEAD   # write this SHA into the Task 7 measurement doc as PRE
python -m pytest tests/ -q -p no:cacheprovider   # baseline must be green before anything changes
```

- [ ] **Step 1: Tests first** (append to `tests/test_gen_agent_trees.py`; `_gen` and `_scratch_source` are
  already there):

```python
def _with_core(tmp_path, nbytes, addendum=b"", codex=None):
    src = _scratch_source(tmp_path)
    (src / "instructions" / "core.md").write_bytes(b"x" * nbytes)
    (src / "instructions" / "claude.md").write_bytes(addendum)
    if codex is not None:
        (src / "instructions" / "codex.md").write_bytes(codex)
    return src


def test_budget_accepts_a_small_core(tmp_path):
    assert _gen().budget_problems(_with_core(tmp_path, 1000)) == []


def test_budget_rejects_an_oversize_core(tmp_path):
    g = _gen()
    problems = g.budget_problems(_with_core(tmp_path, g.INSTRUCTION_BUDGET))
    assert problems and "Codex silently truncates" in problems[0]


def test_budget_counts_the_header(tmp_path):
    """The header is real bytes in the file Codex reads; a core sized to the raw limit must fail."""
    g = _gen()
    room = g.INSTRUCTION_BUDGET - g.CODEX_ADDENDUM_RESERVE - g.INSTALL_EXPANSION_RESERVE
    assert g.budget_problems(_with_core(tmp_path, room))            # header pushes it over
    assert not g.budget_problems(_with_core(tmp_path / "b", room - 400))


def test_budget_holds_room_for_the_codex_addendum(tmp_path):
    """Before codex.md exists, a core that fits only WITHOUT the addendum must already fail."""
    g = _gen()
    core = g.INSTRUCTION_BUDGET - g.INSTALL_EXPANSION_RESERVE - 300
    sizes = g.instruction_sizes(_with_core(tmp_path, core))
    assert sizes["codex.md (reserved)"] > g.INSTRUCTION_BUDGET


def test_budget_uses_the_real_codex_addendum_once_it_exists(tmp_path):
    g = _gen()
    sizes = g.instruction_sizes(_with_core(tmp_path, 1000, codex=b"y" * 10))
    assert "codex.md (reserved)" not in sizes and "codex.md" in sizes


def test_budget_holds_room_for_install_expansion(tmp_path):
    """setup.sh expands the paths and the MO2 block in the INSTALLED copy, which is what Codex reads."""
    g = _gen()
    core = g.INSTRUCTION_BUDGET - g.CODEX_ADDENDUM_RESERVE - 300
    assert g.budget_problems(_with_core(tmp_path, core))


def test_budget_measures_lf_not_crlf(tmp_path):
    g = _gen()
    lf = g.instruction_sizes(_with_core(tmp_path, 0, addendum=b"a\nb\n"))["claude.md"]
    crlf = g.instruction_sizes(_with_core(tmp_path / "c", 0, addendum=b"a\r\nb\r\n"))["claude.md"]
    assert lf == crlf


def test_budget_ignores_the_pointer_and_counts_every_addendum(tmp_path):
    g = _gen()
    src = _with_core(tmp_path, 10)
    (src / "instructions" / "agents-pointer.md").write_bytes(b"z" * 40000)
    (src / "instructions" / "opencode.md").write_bytes(b"o")
    assert set(g.instruction_sizes(src)) == {"claude.md", "opencode.md", "codex.md (reserved)"}


def test_budget_cli_exits_1_and_names_the_file(tmp_path, capsys):
    g = _gen()
    _with_core(tmp_path, g.INSTRUCTION_BUDGET)
    assert g.main(["--root", str(tmp_path), "--budget"]) == 1
    assert "claude.md" in capsys.readouterr().out
```

- [ ] **Step 2: Run** → FAIL (`AttributeError: ... budget_problems`).

- [ ] **Step 3: Implement** (in `scripts/gen_agent_trees.py`, after `TEXT_SUFFIXES`):

```python
# Codex truncates AGENTS.md past this many bytes, SILENTLY (spec E7). CI fails above it (D9); the
# 65536 project_doc_max_bytes in .codex/config.toml (Plan 4) is margin only, and only in a trusted dir.
INSTRUCTION_BUDGET = 32768
# Room held for agent/instructions/codex.md until it exists; then its real size is used instead.
CODEX_ADDENDUM_RESERVE = 2048
# setup.sh fills {{GAME_ROOT}} & co. and splices the MO2 block into the INSTALLED copy -- the file an
# agent actually reads. Sized by tests/test_instruction_budget.py against the real setup.sh.
INSTALL_EXPANSION_RESERVE = 2560
NOT_ADDENDA = ("core.md", "agents-pointer.md")


def instruction_sizes(src: Path) -> dict[str, int]:
    """Worst-case INSTALLED bytes of header + core + each addendum (LF, as generated)."""
    ins = src / "instructions"
    core = _lf((ins / "core.md").read_bytes())
    sizes: dict[str, int] = {}
    for f in sorted(ins.glob("*.md")):
        if f.name in NOT_ADDENDA:
            continue
        body = _headed(f"agent/instructions/core.md + {f.name}", core + _lf(f.read_bytes()), ".md")
        sizes[f.name] = len(body) + INSTALL_EXPANSION_RESERVE
    if "codex.md" not in sizes:
        body = _headed("agent/instructions/core.md + codex.md", core, ".md")
        sizes["codex.md (reserved)"] = len(body) + CODEX_ADDENDUM_RESERVE + INSTALL_EXPANSION_RESERVE
    return sizes


def budget_problems(src: Path) -> list[str]:
    return [f"core.md + {name}: {n} bytes installed (worst case) > {INSTRUCTION_BUDGET} -- Codex silently "
            f"truncates AGENTS.md past this. Move bulk into a skill or docs/ (agent/README.md)."
            for name, n in sorted(instruction_sizes(src).items()) if n > INSTRUCTION_BUDGET]
```

In `main()`, add `ap.add_argument("--budget", action="store_true", help="report instruction sizes vs the budget")`
and, right after `a = ap.parse_args(argv)`:

```python
    if a.budget:
        for name, n in sorted(instruction_sizes(a.root / "agent").items()):
            print(f"core.md + {name}: {n} / {INSTRUCTION_BUDGET} bytes (headroom {INSTRUCTION_BUDGET - n})")
        problems = budget_problems(a.root / "agent")
        print("\n".join(problems) if problems else "within budget")
        return 1 if problems else 0
```

- [ ] **Step 4: Run** the new tests → PASS. Then: `python scripts/gen_agent_trees.py --budget`.
  Expected: two rows. `core.md + claude.md: 56138 / 32768` (53,578 + 2,560) and
  `core.md + codex.md (reserved): 58185 / 32768` (the header label is one byte shorter). Exit 1. These are today's real numbers; Task 7 brings
  them under the limit. Full suite → green (`--check` is unchanged).
- [ ] **Step 5: Commit** `"Generator: instruction byte budget (worst-case installed size), report-only"`.

---

### Task 2: The neutrality gate (machinery only)

**Files:** Create `tests/test_instruction_neutrality.py`.

**Interfaces:** `BANNED: dict[str, str]` (rule id → regex), `POSITIVE: dict[str, str]`, `NEGATIVE: list[str]`,
`NEUTRAL_FILES: list[str]`, `violations(text) -> list[tuple[int, str, str]]` (line number, rule id,
line). The real-file tests (`test_core_is_agent_neutral`, `test_new_skills_are_agent_neutral`,
`test_gate_reads_the_real_files`) are added in **Task 7**. Until Tasks 4–5 land they would fail by design.

- [ ] **Step 1: Write the module**

```python
"""Neutrality gate (spec §6): the shared instruction core must not speak one agent's dialect.

core.md is concatenated into CLAUDE.md now and into AGENTS.md (Plan 4), which Codex and any unknown
agent read. A Claude-only tool name, variable, slash command or config path there is an instruction the
other agents cannot follow -- or follow by guessing. Agent-specific text belongs in
agent/instructions/<agent>.md. Rules match CONSTRUCTS, not words: "Read both files" is English, "the
Read tool" is a tool name. Each rule carries a positive example (the rule must still match it) and the
NEGATIVE list holds ordinary wording that must never trip any rule.
"""
from __future__ import annotations

import re

import pytest

from conftest import REPO

BANNED = {
    "claude-name": r"\bClaude\b",
    "claude-md": r"\bCLAUDE\.md\b",
    "project-dir-var": r"\bCLAUDE_PROJECT_DIR\b",
    "arguments-var": r"\$ARGUMENTS\b",
    "hooks-slash-command": r"(?<![\w./-])/hooks\b",
    "claude-config-path": r"\.claude/(hooks|settings|skills|commands|agents)\b",
    "settings-json": r"\bsettings(\.local)?\.json\b",
    "tool-pair": r"\b(Edit|Write|MultiEdit)\s*/\s*(Edit|Write|MultiEdit)\b",
    "named-tool": r"\b(Edit|Write|Read|MultiEdit|NotebookEdit|Glob|Grep|Bash|PowerShell|WebFetch|WebSearch|Skill|Task)\s+tools?\b",
    "skill-invocation": r"`Skill:",
    "bash-as-channel": r"\b(through|via|by)\s+(the\s+)?Bash\b",
    "bash-command": r"\bBash\s+(command|call)s?\b",
    "mcp-tool-name": r"\bmcp__",
}

POSITIVE = {
    "claude-name": "so Claude can inspect live state",
    "claude-md": "snapshots CLAUDE.md into the store",
    "project-dir-var": "falls back to $CLAUDE_PROJECT_DIR",
    "arguments-var": "the plugin named by `$ARGUMENTS`",
    "hooks-slash-command": "disable them with /hooks",
    "claude-config-path": "Hooks in `.claude/hooks/` enforce",
    "settings-json": "wired up by `.claude/settings.json`",
    "tool-pair": "every edit made through Edit/Write",
    "named-tool": "edit it directly with the Edit tool",
    "skill-invocation": "INVOKE IT (`Skill: skyrim-nif`)",
    "bash-as-channel": "a script run via Bash cannot be seen",
    "bash-command": "before every Bash command",
    "mcp-tool-name": "call mcp__devbench__inspect",
}

NEGATIVE = [
    "Read both files; write only to the local one.",
    "3. **Write pass**: only after user approval, run again with `SaveFile()` enabled",
    "2. **Edit**: Read and modify the YAML files directly",
    "Run it by hand any time: `bash tools/kb-guard.sh --verbose`.",
    "Hook commands of the form `bash \"<abs>\"` run under Git Bash.",
    "the hook is never told the caller's working directory",
    "copy the current `.psc` source files to `.claude/backups/<descriptive-name>/`",
    "An audit log at `.claude/backups/AUDIT_LOG.txt` records every file touched",
    "MCP clients can point at `http://127.0.0.1:<port>/mcp`",
    "Task 3 of 5: swing now",
    "at the start of every session the guards print it",
    "`.devcontainer/devcontainer.json` holds the mounts",
]

NEUTRAL_FILES = [
    "agent/instructions/core.md",
    "agent/skills/devbench/SKILL.md",
    "agent/skills/triage-and-conflicts/SKILL.md",
    "agent/skills/xelib/SKILL.md",
]


def _body(text: str) -> str:
    """Skill frontmatter keeps agent-specific keys by design (spec §4); judge only the body."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 3)
        if end != -1:
            return "\n" * text[:end + 5].count("\n") + text[end + 5:]   # keep line numbers true
    return text


def violations(text: str):
    found = []
    for i, line in enumerate(_body(text.replace("\r\n", "\n")).split("\n"), 1):
        for rule, pattern in BANNED.items():
            if re.search(pattern, line):
                found.append((i, rule, line.strip()))
    return found


def test_every_rule_has_a_positive_example():
    assert set(POSITIVE) == set(BANNED)


@pytest.mark.parametrize("rule", sorted(BANNED))
def test_each_rule_catches_its_example(rule):
    assert re.search(BANNED[rule], POSITIVE[rule]), f"{rule} no longer matches what it exists to catch"


@pytest.mark.parametrize("text", NEGATIVE)
def test_ordinary_wording_is_not_flagged(text):
    assert violations(text) == [], violations(text)


def test_frontmatter_is_exempt_but_body_is_not():
    skill = "---\nname: x\ndescription: d\nargument-hint: $ARGUMENTS\n---\nUse the Edit tool.\n"
    assert [(n, r) for n, r, _ in violations(skill)] == [(6, "named-tool")]
```

- [ ] **Step 2: Run** `python -m pytest tests/test_instruction_neutrality.py -q -p no:cacheprovider` → PASS.
  The positive and negative examples are the test of the machinery.
- [ ] **Step 3: Measure the starting point** (scratch only, no commit):
  `python -c "import sys; sys.path.insert(0,'tests'); from test_instruction_neutrality import violations; print(len(violations(open('agent/instructions/core.md',encoding='utf-8').read())))"`.
  Expected: about 20 hits (core lines 1, 3, 136, 257, 428–443, 477, 481, 511–527, 568, 588–594), all
  handled in Tasks 3–5. Record the count in the Task 7 measurement doc.
- [ ] **Step 4: Commit** `"Neutrality gate: banned constructs with positive and negative examples"`.

---

### Task 3: Move the docs-bound content

**Files:**
- Create `docs/installing-tools.md`
- Modify `docs/container-vs-windows.md`, `docs/safety-philosophy.md`, `agent/instructions/core.md`
- Regenerate `CLAUDE.md`

The section ranges below are **by heading**. Line numbers are as of `fd71271`, for orientation only. If
`core.md` changed since, re-derive them with the heading scan in Task 7 Step 4. Move text **verbatim**,
except for the edits each bullet lists.

- [ ] **Step 1: `docs/installing-tools.md`.** Title `# Installing the optional tools`, then one intro line
  ("None of the modding tools are bundled — install only the ones you need. Per tool: what it is, how to
  acquire it, and a quick verify."). Then, verbatim:
  - the bullet list of **Installing the Optional Tools** (core L44–89);
  - `## Why some tools have wrappers`: Reliability fixes **1 and 2** (L93–94), followed by one line:
    "The xelib loader hazard (fix 3) is a usage rule; it lives in the `xelib` skill." (R9);
  - `## Nexus API key`: the Nexus paragraph from L396, from "Nexus also offers a free REST+GraphQL API" to
    the end.
  - Edits: "Needs your Nexus key per the Nexus section below" → "Needs your Nexus key (see *Nexus API key*
    below)". Every "see the skyrim-nif skill" stays (it is neutral).
- [ ] **Step 2: `docs/container-vs-windows.md`.** Append `## Using the devcontainer`, holding the Optional
  Devcontainer section verbatim (L319–342). Drop its sentence "See `docs/container-vs-windows.md` for the
  full routing guide", because that is now this file. Add `docs/skse-cross-compile.md` as a relative link
  `[skse-cross-compile.md](skse-cross-compile.md)`.
- [ ] **Step 3: `docs/safety-philosophy.md`.** Append `## How the guards resolve "the install"` (core
  L525–540: RESOLVED history with the MEASURED 71/443/7,641 figures, fail-closed, broken env file, relative
  paths). Append `## Why so few prompts` (L559–563). Append `## Is the guard actually running?`
  (L565–570). Append `## The knowledgebase snapshot at session start` (L426–445). Edits: replace each
  `.claude/hooks/...` mention with "the Claude Code hook `.claude/hooks/<name>`", because this doc is
  allowed to name agents. Also **fix the stale Layer 3 list in the same file**: "Five bash scripts" →
  "Five thin wrappers over the Python engine `tools/skyguard/`". Since Plan 2 the rules live there
  (READ: `agent/README.md`, CHANGELOG Unreleased).
- [ ] **Step 4: Replace each moved block in `core.md`.**
  - *Installed Modding Tools*, the note line L40 → `> **Note**: none of these are bundled. How to install
    and verify each one: `docs/installing-tools.md`.` Fix the table cells: L33 "(see install section)" →
    "(see `docs/installing-tools.md`)"; L38 "see DevBench section below" → "skill `devbench`"; L35
    "needs **xeditlib**; see below" → "needs **xeditlib**; skill `triage-and-conflicts`".
  - *Installing the Optional Tools* + *Reliability fixes*: deleted from core (the content now lives in the
    doc and the skill).
  - *PyFFI / PyNifly* L125 "see the Installing section above" → "see `docs/installing-tools.md`".
  - *Optional Devcontainer*: deleted from core.
  - *Nexus Mod Research*: keep the L394 standing rule. Replace L396 with: "Nexus also has a free API
    (versions, changelogs, dependencies). `bash tools/nexus.sh mod <id>` wraps it; key setup and the
    personal-use rules are in `docs/installing-tools.md`. Never echo, log or commit the key."
  - *Safety Rules → DENIED*: replace L525–540 with:
    "The install is **resolved** (from the guard's own location plus `skyrim-paths.env`), never guessed
    from the word "Skyrim". ⚠ If no root resolves, the guard DENIES. ⚠ A relative path cannot be resolved,
    so `rm Data/x.nif` is only advised. Use absolute paths when deleting. Why, with measurements:
    `docs/safety-philosophy.md`."
  - *ASKS THE USER*: replace L559–570 with "Few prompts by design: a guard that prompts on routine work
    gets approved by reflex (`docs/safety-philosophy.md`). **Check the guards are alive:
    `bash tools/hook-canary.sh`.**"
  - *Knowledgebase → The copy that covers…*: replace L426–445 with the short neutral version:

```markdown
### The copy that covers the install you already have

At every session start the guards run `tools/kb-guard.sh`. It snapshots the knowledgebase files and
the generated instruction file into `.claude/backups/kb/<stamp>/` when they change, and alarms if one
vanishes, empties or loses more than half its bytes. No session hooks on your agent? Run
`bash tools/kb-guard.sh --verbose` yourself at the start of a session. ⚠ The snapshot is the
protection and the alarm is a convenience: an update that REPLACES a file with a bigger one raises
no alarm. Details: `docs/safety-philosophy.md`.
```

- [ ] **Step 5: Regenerate and check.**
  `python scripts/gen_agent_trees.py` → `wrote 1 of N files (claude)`.
  `python scripts/gen_agent_trees.py --check` → `in sync`.
  Run the CI link check locally (the `ci.yml` "Relative links in Markdown resolve" snippet) → no output.
  `python scripts/gen_agent_trees.py --budget` → `claude.md` row ≈ 41,500 (INFERRED; record the real value).
  Full suite → green.
- [ ] **Step 6: Commit** `"Split: install guide, devcontainer and guard internals move to docs/"`.

---

### Task 4: Three new skills, and the AutoMod section folded into the existing ones

**Files:**
- Create `agent/skills/devbench/SKILL.md`, `agent/skills/triage-and-conflicts/SKILL.md`, `agent/skills/xelib/SKILL.md`
- Modify `agent/skills/skyrim-nif/SKILL.md`, `agent/skills/create-mod/SKILL.md` (only if Step 4 finds a gap), `agent/instructions/core.md`
- Regenerate `.claude/skills/**`, `CLAUDE.md`

No skill gets `paths:` frontmatter. In Claude Code, `paths` limits when a skill is offered, and these three
must be reachable from any file.

- [ ] **Step 1: `devbench`.** Frontmatter:

```yaml
---
name: devbench
description: Drive the RUNNING game through DevBench (a localhost REST/MCP SKSE plugin) with tools/devbench-cli.sh -- liveness, console exec with output, Papyrus calls, HUD notes, scenarios. Use for any in-game test, any live-state question, and any "it doesn't work in game" problem while the game is running.
---
```

  Body: the DevBench section verbatim (core L254–315), as `# DevBench — the live in-game test channel`.
  Edits, each forced by the neutrality gate or by a fact: "so Claude can inspect" → "so the agent can
  inspect"; "see KNOWLEDGEBASE.md for the hazards" stays. Then add **one line from the install's
  2026-09-30 audit, only if verified**: does `tools/vr-headless.sh` exist in the toolkit tree? (`ls
  tools/vr-headless.sh`). If it does NOT (expected; READ: it is not in `tools/`), keep "Hard requirement —
  this is NOT headless" as is. Do not import the install's headless claim.
- [ ] **Step 2: `triage-and-conflicts`.** Frontmatter:

```yaml
---
name: triage-and-conflicts
description: Which plugin actually wins a record (skyrim-winner, full load order via xEdit), Papyrus log triage, CrashLogger crash-dump triage, ESP cross-reference snapshots around bulk edits, and how these tools pick a folder when more than one Skyrim install exists. Use for override/conflict questions, Papyrus.N.log or crash-log analysis, and before any bulk ESP operation.
---
```

  Body, verbatim: skyrim-winner (L155–186), papyrus-triage (L188–207), crash-triage (L209–230), Where
  these tools look (L232–250), ESP Cross-Reference Integrity (L344–351, R8). Edits: "(see the
  optional-tools section)" → "(install: `docs/installing-tools.md`)"; "(koffi/stdio shutdown, see the
  XEditLib notes)" → "(koffi/stdio shutdown; skill `xelib`)". That reference was **dangling**: the core's
  XEditLib notes never covered exit 127. Step 3 adds it.
- [ ] **Step 3: `xelib`.** Frontmatter:

```yaml
---
name: xelib
description: Write or run xelib / XEditLib.dll scripts (Node + koffi) -- UTF-16 strings, VOID init, WordBool, the string-return pattern, game mode, the SSE registry key, loading the REAL active load order, and judging success by stdout not the exit code. Use before writing, running or debugging any xelib script.
---
```

  Body: `# XEditLib / xelib`. Then the XEditLib API section verbatim (L355–383), then
  `## Load the real active order` with Reliability fix 3 verbatim (L95, R9). Then add
  `## Judge success by stdout, never the exit code`, adapted from `KNOWLEDGEBASE.md:837` (quote the gist,
  link the KB). Then: "For who-wins questions use `bash tools/skyrim-winner.sh` (skill
  `triage-and-conflicts`), not a new script." The **xelib Dry-Run Convention stays in the core**: it is a
  rule, not a recipe.
- [ ] **Step 4: Fold AutoMod CLI into the existing skills.** For each fact in core L97–120, find its home.
  Put the inventory in the commit body.

| Fact | Already in | Action |
|---|---|---|
| call via wrapper, `--json` always | each AutoMod skill's header | none |
| `--dry-run` first for writes | `create-mod` | none |
| `nif` module row incl. PreWEAPON/PreSHIELD | `skyrim-nif` | none |
| `archive` / `audio` / `mcm` rows | `skyrim-bsa` / `skyrim-audio` / `skyrim-mcm` | none |
| `esp` row | `create-mod` | none |
| "AutoMod `nif` cannot inspect Havok collision (`bhk*`)" | **nowhere** | add to `skyrim-nif` "Read-Only Commands" |
| When-to-use routing (AutoMod esp / Spriggit / xeditlib) | core *Spriggit → When to Use Which* | add one bullet there: "**AutoMod `esp`**: one-liner additions of simple records." |

  Then delete the AutoMod CLI section from the core. If the inventory finds any other fact with no home,
  add it to the matching skill. Do not drop it.
- [ ] **Step 5: Delete the moved sections from `core.md`** (skyrim-winner, papyrus-triage, crash-triage,
  Where these tools look, DevBench, ESP Cross-Reference Integrity, XEditLib API). Insert this table
  directly after *Installed Modding Tools*:

```markdown
## Where the rest lives

Skills are plain Markdown (`<name>/SKILL.md` in your agent's skills folder — see the end of this file);
any agent can read one directly. Read the skill BEFORE the work:

| When you are about to… | Read |
|---|---|
| use the running game (inspect, console, Papyrus calls, scenarios) | skill `devbench` |
| ask which plugin wins a record, triage Papyrus or crash logs, or bulk-edit ESPs | skill `triage-and-conflicts` |
| write or run an xelib / XEditLib script | skill `xelib` |
| touch a mesh (`.nif`) | skill `skyrim-nif` |
| work with archives, audio, MCM menus or saves | skills `skyrim-bsa`, `skyrim-audio`, `skyrim-mcm`, `skyrim-save` |
| install or verify an optional tool, or set up the Nexus API key | `docs/installing-tools.md` |
| use the Linux devcontainer | `docs/container-vs-windows.md` |
| understand why the guards behave as they do | `docs/safety-philosophy.md` |
```

  Keep a one-line standing rule where a moved section held one: "On any in-game problem, test it yourself
  through DevBench before asking the user (skill `devbench`)." This goes in *Do Your Homework*.
- [ ] **Step 6: Regenerate and check.** `python scripts/gen_agent_trees.py` → writes 3 new SKILL.md files
  plus `CLAUDE.md` and `.claude/skills/skyrim-nif/SKILL.md`. `--check` → in sync. Run the CI "Skill
  frontmatter is well-formed" snippet locally → `checked 12 skills`.
  `python -m pytest tests/test_gen_agent_trees.py -q -p no:cacheprovider` → PASS, including
  `test_every_real_skill_keeps_frontmatter_first`. `--budget` → record. Full suite → green.
- [ ] **Step 7: Commit** `"Split: skills devbench, triage-and-conflicts, xelib; AutoMod section folded into its skills"`.

---

### Task 5: Make the core neutral; write the Claude addendum; preferences fallback; pointer fixes

**Files:** Modify `agent/instructions/core.md`, `agent/instructions/claude.md`, `agent/instructions/agents-pointer.md`; regenerate.

- [ ] **Step 1: Neutral rewrites in `core.md`.** Use Task 2's `violations()` as the worklist. Rerun it after
  each edit.

| Where (heading) | Now | Becomes |
|---|---|---|
| title L1–3 | `# CLAUDE.md` / "guidance to Claude Code (claude.ai/code)" | `# Skyrim modding — agent instructions` / "Guidance for AI coding agents working in this Skyrim install. Agent-specific notes are at the end of this file." |
| PyFFI/PyNifly L136–140 | "INVOKE IT (`Skill: skyrim-nif`)… listed automatically… listing is not loading" | "★ **The working recipes live in skill `skyrim-nif` — read it before any mesh work**: the PyFFI read/mutate/write block, the PyNifly import, and why PyFFI beats binary patching or NifSkope re-saves." (the listing detail → addendum) |
| xelib Dry-Run L477 | "…xelib operates through Bash and can write via `SaveFile()`" | "…xelib runs as an ordinary script and can write through `SaveFile()`, which no guard sees." |
| Spriggit L481 | "that Claude can edit directly with its native Edit tool" | "that you can edit directly as text" |
| Safety Rules intro L511–516 | hook paths, `.claude/settings.json` | "**The guards** enforce these automatically on agents with hook support (your agent's notes at the end say how). The rules live in the Python engine `tools/skyguard/` (`shell_rules.py`, `write_rules.py`, `paths.py`), which needs Python 3; a guard that cannot start it REFUSES rather than allows. **A guard exists to stop the agent, not to interrupt the user**, so the rules are graded and only the first list refuses." |
| Audit trail L588–589 | "made through the Edit/Write tools… run through Bash" | "- Files edited through the agent's own file-edit tool are backed up to `.claude/backups/` first, on agents with hooks. ⚠ A file written by a script is not seen by that backup at all (see the knowledgebase note above).\n- An audit log at `.claude/backups/AUDIT_LOG.txt` records every file touched, when, and by which tool." |
| Iteration snapshots item 2 L594 | **garbled** (two contradicting clauses, a dangling "— it does NOT capture") | "2. **Automatic copies are not rollback points.** On agents with hooks, edits are backed up and `.psc`/`.pex` are snapshotted before shell commands. A copy you cannot identify later is not a plan, so snapshot by name anyway. Spriggit backs up the ESP on every deserialize, but not scripts." |
| Safety improvement loop L585 | "new hook" | "new guard rule" (wording only; neutral already, kept consistent) |
| Investigation checklist L622 | "hook coverage" | "guard coverage" |

- [ ] **Step 2: Preferences: the fallback for every agent (D10, D15).** In *Knowledgebase → Two files*, change
  the table's `KNOWLEDGEBASE.local.md` owner cell to "**you**: your facts AND your instructions". Insert
  after the standing instruction:

```markdown
### Your preferences: `## My preferences`, read every session

`KNOWLEDGEBASE.local.md` holds the user's facts AND their standing instructions. Its
`## My preferences` section is the user's instructions to you: **read it at the start of every
session and follow it**. On agents with session hooks the guards print it at session start; that is
the same text. Read the rest of the file before making changes. When the user states a lasting
preference ("always…", "never…", "from now on…"), add it to that section. When you create the
file, start it with an empty `## My preferences` section.
```

- [ ] **Step 3: Write `agent/instructions/claude.md`** (generated into `CLAUDE.md` only):

```markdown

---

## Claude Code specifics

Generated into `CLAUDE.md` only. Everything above is shared with every agent.

- **Skills** live in `.claude/skills/<name>/SKILL.md`. Load one with the Skill tool (`Skill: skyrim-nif`).
  A path-scoped skill (`paths:` in its frontmatter) is *listed* automatically when you touch a matching
  file. Listing is not loading: invoke it.
- **The guards** are `.claude/hooks/*.sh`, thin adapters over `tools/skyguard/`, wired up in
  `.claude/settings.json`: `Bash` → `protect-bash` + `snapshot-before-tool`; `Edit|Write` →
  `protect-files` + `backup-before-edit`; SessionStart → `session-kb-guard`. The user lists or disables
  them with `/hooks`. The PowerShell tool is not matched yet.
- **What the backup hook cannot see.** `backup-before-edit.sh` fires on Edit/Write tool calls only, with no
  extension filter (`.psc` included). A file written by a script run through the Bash tool is invisible to
  it. That is how the knowledgebase is usually written, and it is why `session-kb-guard.sh` snapshots the
  knowledgebase files at SessionStart. `snapshot-before-tool.sh` copies recent `.psc`/`.pex` before each
  non-read-only Bash command.
- **Why `hook-canary.sh` matters here.** Claude Code spawns a hook with a Win32 pipe on stdin, on which
  `cat /dev/stdin` returns zero bytes. Every hook here was inert that way for months while its suite
  passed. The canary reads heartbeats written from inside real invocations.
- **A refused command the user really wants**: they can run it themselves with the `!` prefix.
```

  Every fact in it is READ from today's tree: `agent/targets/claude/settings.json` (matchers `Bash`,
  `Edit|Write`), `tools/skyguard/backup.py` (no extension filter), `snapshot.py` (`SKIP` read-only list,
  under 7 days), and core L565–570. Re-verify each one against the tree at execution time. Do not copy
  this block blind.
- [ ] **Step 4: `agents-pointer.md` fixes (R6).**
  - "`.claude/skills/` — packaged workflows." → "`.claude/skills/<name>/SKILL.md` — plain Markdown. **Read
    them directly**: `CLAUDE.md` points into them by name (`devbench`, `triage-and-conflicts`, `xelib`, …)."
  - The safety bullet's "require confirmation before editing anything in the game or config directories"
    → "advise on edits in the game and config directories".
  - Item 4: delete the dangling fragment "under Claude Code, and reconstructing a working script from
    memory is miserable." (READ: line 50, orphaned since an earlier edit).
  - Add to the "Ground rules": "**Read `KNOWLEDGEBASE.local.md` → `## My preferences` at the start of every
    session**: they are the user's standing instructions."
- [ ] **Step 5: Verify.** `violations(core.md)` → `[]`. Regenerate → `--check` in sync. `--budget` → record.
  Read the regenerated `CLAUDE.md` end to end once. The addendum must read as the end of the same document,
  and the "see the end of this file" pointer must land on it. Full suite → green.
- [ ] **Step 6: Commit** `"Core is agent-neutral; Claude specifics move to the claude.md addendum; preferences fallback"`.

---

### Task 6: Toolkit prompt-audit fixes (spec §6), each verified first

**Files:** `agent/instructions/core.md`, `agent/skills/{create-mod,port-to-vr,skyrim-nif,inspect-esp,skyrim-context}/SKILL.md`.

Each fix follows the same order: **confirm the defect exists in the toolkit tree** (quote the line), verify
the replacement claim, then edit. A claim that cannot be verified here is **dropped**, not asserted (R11).

- [ ] **Step 1: Caprica invocation** (core *Installed Modding Tools* row; `create-mod` Step 3).
  - Defect, READ: the core gives `Caprica.exe --game skyrim --import "Data/Scripts/Source" input.psc`,
    run from the game root. MEASURED today with the install's Caprica v0.3.0 `--help`: `-o` defaults to
    **the current directory**, so this form writes the `.pex` into the game root, not `Data/Scripts/`.
    There is no `-f` flags file either, so a script that uses flags fails.
  - Verify, in a scratch dir (never the live `Data/`): copy `TESV_Papyrus_Flags.flg` and a one-function
    `Test.psc` into `<scratch>/Data/Scripts/Source`. Run
    (a) from `Source`: `<caprica> --game skyrim -f TESV_Papyrus_Flags.flg -i . -o .. Test.psc` →
    expect exit 0 and `<scratch>/Data/Scripts/Test.pex`.
    (b) the same from the scratch root, with the path `Data/Scripts/Source/Test.psc` → record exit code and
    message (the install noted a namespace Fatal Error; reproduce it, do not assume it).
  - Edit: the row becomes "`cd Data/Scripts/Source && ../../../tools/Caprica/Caprica.exe --game skyrim -f
    TESV_Papyrus_Flags.flg -i . -o .. MyScript.psc`, run from the source folder. Add `-i <dir>` for each
    further import folder (e.g. `../../Source/Scripts` where vanilla sources live there)". Add "with a
    BARE filename" **only if (b) failed**. Apply the same form to `create-mod` Step 3.
- [ ] **Step 2: `port-to-vr` Precision line** (Step 4: "Precision doesn't exist for VR; HIGGS/VRIK handle
  this"). That is defective: the author's own VR install runs Precision (READ: the install's CLAUDE.md
  "All combat mods … (MCO, SCAR, Precision, etc.)"). Verify Precision's VR status on its Nexus page
  (Playwright: WebFetch gets 403 there) or with `bash tools/nexus.sh mod <id>`. Edit to the generic,
  verifiable form: "Melee hit detection → in VR it is layered (HIGGS, optional collision mods such as
  Precision or WeaponCollision, then vanilla). Find out which layer the mod assumes and test against the
  user's actual stack." and "Weapon reach values → verify against the VR hit-detection stack, not SSE numbers."
- [ ] **Step 3: `port-to-vr` AMR / SCAR lines** (Step 6). "SCAR → dedicated VR port exists (BFCO)" is
  wrong: BFCO is a separate mod, not a SCAR port. "AMR → VR-compatible (CTD was fixed in update)" is
  unsourced. Check both Nexus pages. Edit to: "- [ ] Animation Motion Revolution → check its Nexus page for
  current VR support and the minimum version the mod needs" and "- [ ] SCAR → check its VR status and
  version; BFCO is a separate mod, not a VR port of SCAR". Assert a VR status only if a page states it, and
  cite the page in the commit body.
- [ ] **Step 4: `skyrim-nif` "all mesh work"** line ("Use the AutoMod CLI for all mesh work"). It
  contradicts the skill's own PyFFI/PyNifly half. Edit: "Use the AutoMod CLI for texture, string and
  shader work (always with `--json`); PyFFI and PyNifly (below) cover geometry and animation."
- [ ] **Step 5: `GetFormFromFile` own-plugin scoping.** Fix every site, not only the one the audit named:
  `grep -rn "GetFormFromFile" agent/` → core *Top Gotchas* #3, `skyrim-context` #3, `create-mod` Safety
  Reminders, `port-to-vr` Step 7 (READ today: four sites, all unscoped, contradicting *Own Your Records*).
  Each becomes: "use `GetFormFromFile()` on your OWN plugin's forms to minimize properties (never another
  mod's — see *Own Your Records*), and check the result for `None`".
- [ ] **Step 6: `inspect-esp` script path.** Defects, READ: "If xeditlib is installed (`tools/xelib/`
  exists)" is always true, because `tools/xelib/` ships with the toolkit. The real test is whether
  `node_modules/xeditlib` resolves. Spriggit is called raw, with `/tmp` output and a hardcoded
  `--PackageVersion "0.40.0"`, against the core's own rule "Don't hardcode `--PackageVersion`".
  Edit Option A to `bash tools/spriggit-cli.sh serialize … --OutputPath "<scratch dir>" … --PackageVersion "<the version spriggit --version prints>"`.
  Edit Option B's check to "If `node -e "require('xeditlib')"` exits clean from the toolkit root". Keep
  `node examples/inspect-esp.js "<Plugin.esp>"` (READ: `examples/inspect-esp.js:3` takes a plugin
  name). Plugin wording: "the plugin the user named (`$ARGUMENTS` in Claude Code)" (spec §4).
- [ ] **Step 7: Regenerate, `--check`, neutrality worklist still `[]`, full suite green.**
- [ ] **Step 8: Commit** `"Prompt-audit fixes: Caprica form, Precision/AMR/SCAR claims, skyrim-nif scope, GetFormFromFile scoping, inspect-esp"`.
  The body lists each defect, its verification (command and result) and any claim dropped.

**Raised, not changed (outside the spec's list):** core *Own Your Records*' "Safe exception:
`Skyrim.esm`… referencing their FormIDs is fine" is weaker than the install's newer rule (vanilla records
can be overridden by other plugins, so copy any whose behaviour you rely on). Put it to the user as a
candidate edit. Do not apply it here.

---

### Task 7: Enforce both gates on the real files; conservation check; mutations; docs

**Files:**
- Modify `scripts/gen_agent_trees.py`, `tests/test_gen_agent_trees.py`, `tests/test_instruction_neutrality.py`, `tests/test_mutations.py`
- Create `tests/test_instruction_budget.py`, `docs/superpowers/measurements/2026-10-XX-plan3-split.md`
- Docs: `agent/README.md`, `README.md`, `CHANGELOG.md`, `docs/getting-started.md`, `.github/workflows/ci.yml` (step name only)

- [ ] **Step 1: Measure, then cut if needed.** `python scripts/gen_agent_trees.py --budget`. Gate: every row
  ≤ 32,768, **and** core ≤ 26,500 for headroom. Core = the reserved row − 211 − 2,048 − 2,560. If the core is
  over the target, apply these **pre-committed fallback cuts in order**, re-measuring after each:
  (1) condense *PyFFI / PyNifly* + *NIF Validation* (≈ 2,600 B) to a 4-line HARD LIMITS list plus "recipes and
  gates: skill `skyrim-nif`". The skill already holds all of it (READ).
  (2) Trim the *Installed Modding Tools* ReSaver row to "headless `.ess` parse/clean; ops list:
  `bash tools/resaver-cli.sh --help`; skill `skyrim-save`". Check the op list is in `--help` first.
  (3) Move the *Confidence scale* table to `docs/safety-philosophy.md` and keep the 5 rules.
  Stop at the first cut that reaches ≤ 26,500.
- [ ] **Step 2: Wire the budget into `--check`.** In `main()`, in the `if a.check:` branch:

```python
    if a.check:
        problems = check(outputs, a.root)
        problems += budget_problems(a.root / "agent")
```

  Tests (append to `tests/test_gen_agent_trees.py`):

```python
def test_check_reports_an_over_budget_core(tmp_path, capsys):
    g = _gen()
    _with_core(tmp_path, g.INSTRUCTION_BUDGET)
    assert g.main(["--root", str(tmp_path)]) == 0          # generate it in sync first
    assert g.main(["--root", str(tmp_path), "--check"]) == 1
    assert "Codex silently truncates" in capsys.readouterr().out
```

  Create `tests/test_instruction_budget.py`:

```python
"""The instruction budget on the REAL tree, and the install-expansion reserve against the REAL setup.sh."""
from __future__ import annotations

import importlib.util

from conftest import REPO, run_setup


def _gen():
    spec = importlib.util.spec_from_file_location("gen_agent_trees", REPO / "scripts" / "gen_agent_trees.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_instructions_fit_the_budget():
    g = _gen()
    sizes = g.instruction_sizes(REPO / "agent")
    assert sizes, "no instruction files measured -- the gate would be vacuous"
    assert not g.budget_problems(REPO / "agent"), sizes


def test_install_expansion_fits_its_reserve(portable_mo2):
    """setup.sh's path fill + MO2 block is what an installed CLAUDE.md/AGENTS.md carries beyond the repo."""
    g = _gen()
    game, _inst, _mods = portable_mo2
    before = len((game / "CLAUDE.md").read_bytes())
    r = run_setup(game)
    assert r.returncode == 0, r.stdout + r.stderr
    after = len((game / "CLAUDE.md").read_bytes())
    assert after > before, "setup.sh expanded nothing -- the fixture did not exercise the MO2 block"
    assert after - before <= g.INSTALL_EXPANSION_RESERVE, (after - before, g.INSTALL_EXPANSION_RESERVE)
```

  Before trusting it, check how long the fixture's paths are. Print `game_root_win(game)` once. If the
  fixture root is shorter than 80 characters, add a `long_mo2` variant of the fixture nested under a 120-character
  directory. Then the reserve is sized against realistic paths, not just tmp paths (Review Focus 1). Record
  the measured `after - before` in the measurement doc.
- [ ] **Step 3: Real-file neutrality tests** (append to `tests/test_instruction_neutrality.py`):

```python
def test_gate_reads_the_real_files():
    for rel in NEUTRAL_FILES:
        p = REPO / rel
        assert p.is_file() and p.stat().st_size > 500, f"{rel} missing or near-empty -- gate would be vacuous"


def test_core_is_agent_neutral():
    hits = violations((REPO / "agent/instructions/core.md").read_text(encoding="utf-8"))
    assert not hits, "\n".join(f"core.md:{n} [{r}] {l}" for n, r, l in hits)


def test_new_skills_are_agent_neutral():
    hits = []
    for rel in NEUTRAL_FILES[1:]:
        hits += [f"{rel}:{n} [{r}] {l}" for n, r, l in violations((REPO / rel).read_text(encoding="utf-8"))]
    assert not hits, "\n".join(hits)


def test_core_tells_every_agent_to_read_my_preferences():
    """The fallback for agents without session hooks (D15): the instruction itself must be in the core."""
    core = " ".join((REPO / "agent/instructions/core.md").read_text(encoding="utf-8").split())
    assert "## My preferences" in core
    assert "read it at the start of every session and follow it" in core   # whitespace-normalised: it wraps
```

- [ ] **Step 4: Conservation check (one-time; spec §6 "none is deleted").** In the scratchpad, write a
  script that:
  - reads `git show PRE:agent/instructions/core.md` (PRE from Task 1 Step 0);
  - splits it into sections by heading;
  - for each section that moved, takes every line ≥ 30 characters after stripping;
  - normalises whitespace and Markdown emphasis (`**`, `` ` ``);
  - searches for each line in the concatenation of: the new `core.md`, `claude.md`, `docs/*.md` and
    `agent/skills/*/SKILL.md`.

  It prints the misses. **Every miss must be accounted for**: either a reworded line (from Tasks 3–6, with the
  reason) or a bug, which you restore. Record the totals in the measurement doc: lines checked, found
  verbatim, reworded with reason, restored. Gate: **zero unexplained misses.**
- [ ] **Step 5: Mutation entries** (append to `MUTATIONS` in `tests/test_mutations.py`; every anchor is a
  single line and must be unique; `_mutate` asserts that):

```python
    # --- instruction budget and neutrality (Plan 3) ------------------------
    # Lift the limit: an AGENTS.md that Codex silently truncates would pass.
    pytest.param("scripts/gen_agent_trees.py", "INSTRUCTION_BUDGET = 32768", "INSTRUCTION_BUDGET = 10 ** 9",
                 "tests/test_gen_agent_trees.py::test_budget_rejects_an_oversize_core",
                 id="instruction-budget-is-enforced"),
    # Stop holding room for the Codex addendum: Plan 4 would land over budget on day one.
    pytest.param("scripts/gen_agent_trees.py", "CODEX_ADDENDUM_RESERVE = 2048", "CODEX_ADDENDUM_RESERVE = 0",
                 "tests/test_gen_agent_trees.py::test_budget_holds_room_for_the_codex_addendum",
                 id="budget-reserves-the-codex-addendum"),
    # Measure the repo file instead of the installed one.
    pytest.param("scripts/gen_agent_trees.py", "INSTALL_EXPANSION_RESERVE = 2560", "INSTALL_EXPANSION_RESERVE = 0",
                 "tests/test_gen_agent_trees.py::test_budget_holds_room_for_install_expansion",
                 id="budget-measures-the-installed-size"),
    # Unwire the budget from --check: every test of budget_problems() stays green, CI stops enforcing it.
    pytest.param("scripts/gen_agent_trees.py", '        problems += budget_problems(a.root / "agent")',
                 "        pass",
                 "tests/test_gen_agent_trees.py::test_check_reports_an_over_budget_core",
                 id="check-enforces-the-budget"),
    # Grow the real core past the limit.
    pytest.param("agent/instructions/core.md", "## INI Config Hierarchy", "## INI Config Hierarchy\n\n" + "x" * 9000,
                 "tests/test_instruction_budget.py::test_repo_instructions_fit_the_budget",
                 id="real-core-is-within-budget"),
    # Put a Claude-only construct back into the shared core.
    pytest.param("agent/instructions/core.md", "## INI Config Hierarchy",
                 "## INI Config Hierarchy (edit them with the Edit tool)",
                 "tests/test_instruction_neutrality.py::test_core_is_agent_neutral",
                 id="core-stays-agent-neutral"),
    pytest.param("agent/skills/devbench/SKILL.md", "# DevBench — the live in-game test channel",
                 "# DevBench — the live in-game test channel ($CLAUDE_PROJECT_DIR)",
                 "tests/test_instruction_neutrality.py::test_new_skills_are_agent_neutral",
                 id="new-skills-stay-agent-neutral"),
    # Drop the fallback instruction: an agent with no session hook never sees the user's preferences.
    pytest.param("agent/instructions/core.md", "session and follow it**.", "session**.",
                 "tests/test_instruction_neutrality.py::test_core_tells_every_agent_to_read_my_preferences",
                 id="core-keeps-the-preferences-fallback"),
```

  Check each anchor exists exactly once **before** running. The two `## INI Config Hierarchy` entries share
  an anchor, which is fine: each mutates its own copy. If a fallback cut (Step 1) renamed that heading, pick
  another unique single line. Run them:
  `python -m pytest tests/test_mutations.py -q -p no:cacheprovider -k "budget or neutral or preferences or real-core"`
  → 8 passed. Each mutated test exited 1. Make no tree edits while it runs.
- [ ] **Step 6: CI.** `ci.yml`: rename the step to "Generated agent files match agent/, and the instruction
  budget holds (edit agent/, then run scripts/gen_agent_trees.py)". The command is unchanged; `--check`
  now covers both. The behavioural suite already runs the new tests on both OSes.
- [ ] **Step 7: Docs.**
  - `agent/README.md`: add the three skills to the map. Add a "Budget" paragraph (the limit, R1's
    reserves, `--budget`, what to move when over). Add a "Neutrality" paragraph (core + new skills; where
    agent-specific text goes; `tests/test_instruction_neutrality.py` is the list).
  - `README.md`: in *Editing CLAUDE.md, skills, hooks or settings*, add the budget and neutrality rules. In
    the update FAQ, add a sentence on `## My preferences` (filled in after Task 8: "named agents get it at
    session start"). Fix "Claude is instructed to read both files" → "the agent is instructed…" (that line
    is the README, so it may name agents; change it only for accuracy).
  - `docs/getting-started.md`: a short "Telling the agent your preferences" section with a 3-line example
    `## My preferences` block.
  - `CHANGELOG.md` → *Unreleased*: a "📚 Instructions split" entry (sizes before/after; where each section
    went; the 3 new skills; the budget and neutrality gates). A "🐛 Prompt-audit fixes" entry, one line per
    fix (Nexus changelog rule: one issue per line).
- [ ] **Step 8: Measurement doc** `docs/superpowers/measurements/2026-10-XX-plan3-split.md`: PRE SHA; the
  before/after byte table (core, `CLAUDE.md`, every `--budget` row, each new skill and doc); the measured
  install expansion; the neutrality hit count before (Task 2 Step 3) and after (0); the conservation totals;
  which fallback cuts were needed.
- [ ] **Step 9: Full suite** → green. Then `python scripts/gen_agent_trees.py --check` → in sync and
  within budget.
- [ ] **Step 10: Commit** `"Enforce the instruction budget and neutrality gates; conservation check; docs"`.

---

### Task 8: Inject `## My preferences` at session start (D15) — LAST, in Plan 2's area

**Precondition (check, do not assume):** Plan 2 is finished. `git log --oneline` shows its Task 8
measurement doc (`docs/superpowers/measurements/2026-10-*-plan2-equivalence.md`) committed, and its Task 9
verification done. `git status` is clean, and `git log -3 -- tools/skyguard/session.py` shows nobody else
mid-change. If Plan 2 is still open, **stop and report**. Do not interleave.

**Files:**
- Create `tools/skyguard/prefs.py`, `tests/test_skyguard_prefs.py`
- Modify `tools/skyguard/session.py`, `tests/test_mutations.py`, `agent/instructions/claude.md` (one
  bullet), `README.md`, `CHANGELOG.md`

**Interfaces:**
- `prefs.section(text: str) -> Optional[str]`: the body under the first `## My preferences` heading
  outside a code fence, up to the next `#`/`##` heading outside a fence. `None` if there is no heading.
- `prefs.render(project: str, audit) -> str`: the framed text to inject, or `""`. **Never raises.**
- `session.run(project, source, bash, audit) -> str`: the signature is unchanged. Output = the kb-guard
  text, as today, followed by `prefs.render()`'s text when non-empty. On `compact`, the preferences text
  only (R4).

- [ ] **Step 1: Tests first** (`tests/test_skyguard_prefs.py`):

```python
"""D15: the user's `## My preferences` reaches the agent at session start, through the real hook."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import BASH, REPO, install_hooks

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit as audit_mod, prefs, session  # noqa: E402

KB = "KNOWLEDGEBASE.local.md"


def _kb(tmp_path: Path, text, raw=False) -> Path:
    p = tmp_path / KB
    p.write_bytes(text if raw else text.encode("utf-8"))
    return tmp_path


def _render(tmp_path):
    return prefs.render(str(tmp_path), audit_mod.Audit(str(tmp_path)))


def test_no_file_injects_nothing(tmp_path):
    assert _render(tmp_path) == ""


def test_file_without_the_section_injects_nothing(tmp_path):
    assert _render(_kb(tmp_path, "# Notes\n\n## Engine quirks\n- a\n")) == ""


def test_section_is_injected_and_stops_at_the_next_h2(tmp_path):
    out = _render(_kb(tmp_path, "# KB\n## My preferences\n- British spelling\n### Sub\n- keep\n## Other\n- no\n"))
    assert "- British spelling" in out and "### Sub" in out and "- keep" in out
    assert "- no" not in out and KB in out


def test_an_h1_also_ends_the_section(tmp_path):
    assert "after" not in _render(_kb(tmp_path, "## My preferences\n- a\n# Next\nafter\n"))


def test_heading_inside_a_code_fence_is_not_the_section(tmp_path):
    text = "```\n## My preferences\n- fake\n```\n## My preferences\n- real\n"
    out = _render(_kb(tmp_path, text))
    assert "- real" in out and "- fake" not in out


def test_an_h2_inside_a_fence_does_not_end_the_section(tmp_path):
    text = "## My preferences\n- a\n```md\n## not a heading\n```\n- b\n## End\n"
    out = _render(_kb(tmp_path, text))
    assert "- b" in out and "## not a heading" in out


def test_crlf_bom_case_and_trailing_spaces(tmp_path):
    raw = "\ufeff## my PREFERENCES  \r\n- x\r\n".encode("utf-8")
    assert "- x" in _render(_kb(tmp_path, raw, raw=True))


def test_empty_section_injects_nothing(tmp_path):
    assert _render(_kb(tmp_path, "## My preferences\n\n   \n## Next\n- y\n")) == ""


def test_long_section_is_capped_on_a_character_boundary(tmp_path):
    body = "x" + "é" * prefs.CAP                  # 1 + 2n bytes: the cut at CAP lands mid-character
    out = _render(_kb(tmp_path, "## My preferences\n" + body + "\n"))
    assert "truncated" in out and KB in out
    out.encode("utf-8")                           # no lone surrogate / broken char
    assert len(out.encode("utf-8")) < prefs.CAP + 400


def test_unreadable_file_says_so_and_never_raises(tmp_path):
    (tmp_path / KB).mkdir()                       # a directory where the file should be
    out = _render(tmp_path)
    assert "NOT INJECTED" in out
    assert "prefs FAILED" in (tmp_path / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")


def test_session_appends_preferences_after_the_kb_guard_text(tmp_path):
    _kb(tmp_path, "## My preferences\n- PREF-CANARY\n")
    out = session.run(str(tmp_path), "startup", "bash", audit_mod.Audit(str(tmp_path)))
    assert out.index("KB GUARD") < out.index("PREF-CANARY")    # tools/kb-guard.sh absent -> NOT RUN text


def test_preferences_follow_a_clean_kb_guard_run(tmp_path):
    """The NORMAL return site (kb-guard ran), not only the missing-tool one the hook test reaches."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "kb-guard.sh").write_text("exit 0
", encoding="utf-8", newline="
")
    _kb(tmp_path, "## My preferences
- PREF-CANARY
")
    out = session.run(str(tmp_path), "startup", BASH, audit_mod.Audit(str(tmp_path)))
    assert out.startswith("USER PREFERENCES") and "PREF-CANARY" in out


def test_compact_still_injects_preferences_but_takes_no_snapshot(tmp_path):
    _kb(tmp_path, "## My preferences\n- PREF-CANARY\n")
    out = session.run(str(tmp_path), "compact", "bash", audit_mod.Audit(str(tmp_path)))
    assert "PREF-CANARY" in out and "KB GUARD" not in out
    assert not (tmp_path / ".claude" / "backups" / "AUDIT_LOG.txt").exists()


def test_a_crashing_prefs_module_cannot_fail_the_session(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(prefs, "_render", boom)
    out = session.run(str(tmp_path), "startup", "bash", audit_mod.Audit(str(tmp_path)))
    assert "KB GUARD" in out


def test_the_real_hook_delivers_preferences(tmp_path):
    """Through the shipped wrapper, the way the agent receives it -- not by calling the function."""
    root = install_hooks(tmp_path, names={"session-kb-guard.sh"})
    _kb(root, "## My preferences\n- PREF-CANARY-7f3a\n")
    r = subprocess.run([BASH, str(root / ".claude" / "hooks" / "session-kb-guard.sh")],
                       input=b'{"source":"startup"}', capture_output=True, timeout=120,
                       env=dict(os.environ, CLAUDE_PROJECT_DIR=str(root)))
    assert r.returncode == 0
    assert b"PREF-CANARY-7f3a" in r.stdout
```

  Existing tests that must stay green unchanged: `test_session_compact_is_silent` (no local file → `""`),
  `test_session_missing_tool`, `test_session_rc2_gets_extra_line_and_flat_audit`,
  `test_session_clean_run_and_empty_output`, `test_session_audit_output_truncated_to_200_bytes`, and every
  `tests/test_kb_guard.py` hook test. None of their fixtures creates `KNOWLEDGEBASE.local.md`. Check that
  with a grep before relying on it. If any fixture does create the file, its expected output gains the
  preferences block, and the test change is a reviewed diff, not a silent one.

- [ ] **Step 2: Run** → FAIL (`ImportError: cannot import name 'prefs'`).
- [ ] **Step 3: Implement.**

```python
# tools/skyguard/prefs.py
"""D15: hand the agent the user's standing preferences at session start.

KNOWLEDGEBASE.local.md -> the `## My preferences` section, framed for injection. Pure text in, text out,
and NEVER raises: session start must not fail (Plan 2), so any failure becomes one printed line plus an
audit line. Agents without session hooks get the same section through the core's fallback instruction.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

FILE = "KNOWLEDGEBASE.local.md"
CAP = 4096                                    # bytes of section body (R5)
_HEADING = re.compile(r"^##[ \t]+my preferences[ \t]*#*[ \t]*$", re.I)
_ENDS = re.compile(r"^#{1,2}[ \t]+\S")
_FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)")


def section(text: str) -> Optional[str]:
    lines = text.splitlines()
    in_fence, start = False, None
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and _HEADING.match(line):
            start = i + 1
            break
    if start is None:
        return None
    body, in_fence = [], False
    for line in lines[start:]:
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and _ENDS.match(line):
            break
        body.append(line)
    return "\n".join(body).strip("\n")


def _render(project, audit) -> str:
    path = Path(project) / FILE
    if not path.exists():
        return ""
    try:
        text = path.read_bytes().decode("utf-8-sig", "replace")
    except OSError as e:
        audit.log("session-kb-guard", "prefs FAILED", "could not read {}: {}".format(FILE, e.__class__.__name__))
        return "PREFERENCES: NOT INJECTED -- {} exists but could not be read; read its `## My preferences` yourself.".format(FILE)
    body = section(text)
    if body is None or not body.strip():
        return ""
    data = body.encode("utf-8")
    if len(data) > CAP:
        body = data[:CAP].decode("utf-8", "ignore").rstrip() + \
            "\n[... truncated at {} bytes -- read the rest in {}]".format(CAP, FILE)
    return "USER PREFERENCES (from {}, section `## My preferences`) -- follow these all session:\n{}".format(FILE, body)


def render(project, audit) -> str:
    try:
        return _render(project, audit)
    except Exception:                         # noqa: BLE001 -- never fail the session
        return ""
```

  `session.py`: three return sites gain the preferences. The kb-guard logic is byte-for-byte unchanged:

```python
from . import prefs   # added beside the other imports

def _with_prefs(text, project, audit):
    extra = prefs.render(project, audit)
    return "\n".join(t for t in (text, extra) if t)


def run(project, source, bash, audit):
    if source == "compact":
        return _with_prefs("", project, audit)   # no snapshot on compact, but preferences survive it (R4)
    ...                                          # unchanged
        return _with_prefs("KB GUARD: NOT RUN -- {} is missing, so your knowledgebase was NOT snapshotted this session.".format(tool), project, audit)
    ...                                          # unchanged
    return _with_prefs("\n".join(lines), project, audit)
```

  `test_a_crashing_prefs_module_cannot_fail_the_session` patches `prefs._render`, so `render()`'s own
  `except` is what it proves.
- [ ] **Step 4: Run** the new file → PASS. Run `tests/test_skyguard_side_effects.py`,
  `tests/test_kb_guard.py`, `tests/test_guard_corpus.py` → PASS, unchanged. Run
  `test_engine_parses_on_python_3_8` → PASS. READ: it `rglob`s `tools/skyguard/*.py`, so it covers
  `prefs.py` with no change. `ruff check tools/skyguard` → clean.
- [ ] **Step 5: Mutations** (append to `MUTATIONS`):

```python
    # --- preferences injection (Plan 3, D15) --------------------------------
    pytest.param("tools/skyguard/prefs.py", "        elif not in_fence and _HEADING.match(line):",
                 "        elif _HEADING.match(line):",
                 "tests/test_skyguard_prefs.py::test_heading_inside_a_code_fence_is_not_the_section",
                 id="prefs-ignore-a-fenced-heading"),
    pytest.param("tools/skyguard/prefs.py", "    if len(data) > CAP:", "    if False:",
                 "tests/test_skyguard_prefs.py::test_long_section_is_capped_on_a_character_boundary",
                 id="prefs-are-capped"),
    pytest.param("tools/skyguard/session.py",
                 '        return _with_prefs("", project, audit)   # no snapshot on compact, but preferences survive it (R4)',
                 '        return ""',
                 "tests/test_skyguard_prefs.py::test_compact_still_injects_preferences_but_takes_no_snapshot",
                 id="prefs-survive-compaction"),
    pytest.param("tools/skyguard/session.py", '    return _with_prefs("\\n".join(lines), project, audit)',
                 '    return "\\n".join(lines)',
                 "tests/test_skyguard_prefs.py::test_preferences_follow_a_clean_kb_guard_run",
                 id="session-start-injects-preferences-after-a-kb-guard-run"),
    # One twin per return site: each site shadows the others, so one mutation proves only one site.
    pytest.param("tools/skyguard/session.py", 'snapshotted this session.".format(tool), project, audit)',
                 'snapshotted this session.".format(tool)',
                 "tests/test_skyguard_prefs.py::test_the_real_hook_delivers_preferences",
                 id="session-start-injects-preferences-when-kb-guard-is-missing"),
```

  In the source, the normal-site anchor's `\\n` is the two characters backslash and n. The missing-tool
  anchor assumes that call is written on ONE line ending `...snapshotted this session.".format(tool),
  project, audit)`. Write it that way. Check every anchor is unique first. Run
  `-k "prefs or session-start"` → 5 passed. The hook test reaches the missing-tool site, because
  `install_hooks` copies no `kb-guard.sh`. The clean-run test reaches the normal site. That is why each
  mutation names its own test.
- [ ] **Step 6: Docs.** `claude.md` addendum: the SessionStart bullet becomes "SessionStart →
  `session-kb-guard` (knowledgebase snapshot, then your `## My preferences`)". README: finish the
  Task 7 sentence. CHANGELOG *Unreleased*: "✨ Your preferences reach the agent at every session start"
  (one line each: what, the cap, the compact behaviour, the fallback for other agents). Regenerate →
  `--check` green.
- [ ] **Step 7: Full suite** → green. **Commit** `"Session start injects KNOWLEDGEBASE.local.md ## My preferences (D15)"`.

---

### Task 9: End-to-end verification (gate for Plan 3)

- [ ] **Fresh clone with `core.autocrlf=true`:** `--check` in sync and within budget; `git status` clean;
  `--budget` gives the same numbers as in the repo (CRLF does not leak into the measurement).
- [ ] **Release-style copy** (rsync excluding `.git .github tests`) into a fake game dir with an MO2 layout
  and long paths, then `bash setup.sh </dev/null`. The installed `CLAUDE.md` must be ≤ 32,768 − 2,048 bytes,
  measured on the file. The Codex addendum lands in the same file in Plan 4. Every link in the "Where the rest
  lives" table resolves to a file present in the copy.
- [ ] **Live `claude -p`, real entry point, preferences.** In the configured copy, write
  `KNOWLEDGEBASE.local.md` with `## My preferences\n- PREF-CANARY-7f3a: end every answer with the word
  "Quill".`. Then:
  (a) `claude -p "Say hello." --disallowedTools "Read,Bash,PowerShell,Grep,Glob"`. The answer ends with
  "Quill". The agent cannot read the file, so the text can only have arrived through the hook (Review
  Focus 6).
  (b) Control: same command after deleting the section → no "Quill".
  (c) The hook output size: confirm the full injected block arrived untruncated (R5's ASSUMED limit). Use a
  3,900-byte section and ask for its last line.
  Record (a)–(c) in the measurement doc.
- [ ] **Skills reachable:** in the same copy, `claude -p "Which skill covers crash logs? Answer with its
  name only."` → `triage-and-conflicts`. Then the same through Codex (`codex exec`) in a copy **with no
  `.claude/`**, reading only `AGENTS.md` → the pointer leads it to `.claude/skills/...`. If Codex is not
  installed, record "not run" and do not claim it.
- [ ] **Mutation gate:** `python -m pytest tests/test_mutations.py -q -p no:cacheprovider` → all pass,
  including the 13 new entries (8 from Task 7, 5 from Task 8).
- [ ] **Final whole-range review** on the most capable model, using this plan's Review Focus. Then the fix
  pass and the full suite. No push.

## Spec coverage of Plan 3

| Spec | Task |
|---|---|
| §6 section moves (installing, DevBench, triage, devcontainer, AutoMod, XEditLib) | 3, 4 |
| §6 Safety Rules internals → `docs/safety-philosophy.md` "if over" | 3 (R8) |
| §4 three new skills (devbench, triage-and-conflicts, xelib) | 4 |
| §4 `claude.md` addendum | 5 |
| D9 / §7.7 byte budget, CI fails at 32,768 | 1, 7 (interface to Plan 4 for `project_doc_max_bytes`) |
| §6 neutrality gate | 2, 7 |
| D10 local KB = facts + instructions; fallback "read it" | 5, 7 |
| D15 SessionStart injection | 8 |
| §6 prompt-audit fixes | 6 |
| §7.4-style mutants for new gates | 7, 8 |

Deferred by design:
- Codex target, `codex.md`, `.codex/config.toml` with `project_doc_max_bytes = 65536` → Plan 4.
- Neutral bodies for the 9 existing skills (`$ARGUMENTS` wording beyond `inspect-esp`) → Plan 4.
- `setup.sh` creating `KNOWLEDGEBASE.local.md` with an empty `## My preferences`, and the `AGENTS.md` path
  fill → Plan 8.
- `kb-guard.sh` watching `AGENTS.md` → Plan 4/8.
- The `Bash|PowerShell` matcher and its addendum line → Plan 5.
- The core's DENIED claim "Directly writing to ESP/ESM/ESL/BSA/BA2 files" holds only for file-edit tools
  today (E21). Plan 3 moves it verbatim. Plan 5 adds the shell-write rules and corrects the claim to match
  what the engine then enforces (spec §5).
