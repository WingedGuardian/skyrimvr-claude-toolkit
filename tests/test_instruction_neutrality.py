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
    "agent/skills/mod-release-review/SKILL.md",
    "agent/skills/mod-update-check/SKILL.md",
    "agent/skills/toolkit-setup/SKILL.md",
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


# The ONE sanctioned spelling of a Claude-only substitution in a skill body (spec 4): the neutral phrase
# first, the Claude Code detail in a parenthesis. A bare `$ARGUMENTS` is still flagged.
SANCTIONED = "(`$ARGUMENTS` in Claude Code)"


def violations(text: str):
    found = []
    for i, line in enumerate(_body(text.replace("\r\n", "\n")).split("\n"), 1):
        line = line.replace(SANCTIONED, "")
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


def test_only_the_sanctioned_arguments_spelling_is_exempt():
    assert violations("Check the mod the user named " + SANCTIONED + " now.") == []
    assert [r for _, r, _ in violations("Check the mod named by `$ARGUMENTS` now.")] == ["arguments-var"]
    assert violations("Check the mod the user named " + SANCTIONED + " and ask Claude")   # not a blanket pass


def test_every_skill_body_is_neutral():
    """Codex reads every skill (.agents/skills/), so every body is judged, not just the shared three."""
    files = sorted((REPO / "agent" / "skills").glob("*/SKILL.md"))
    assert len(files) >= 12, "skill sources not found -- the gate would be vacuous"
    hits = []
    for f in files:
        hits += [f"{f.parent.name}:{n} [{r}] {l}" for n, r, l in violations(f.read_text(encoding="utf-8"))]
    assert not hits, "\n".join(hits)
