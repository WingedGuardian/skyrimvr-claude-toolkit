#!/usr/bin/env python3
"""check-codex-payload -- is the Codex half of a toolkit payload whole?

    python tools/check-codex-payload.py <payload root>

Run by the release workflow on the folder it zips, and by tools/verify-release.sh on the zip a
user downloads. A Codex install that is missing a piece does not error: a hook whose script is
absent cannot start, and on Codex a hook that cannot start lets the command RUN; a missing rules
file loses the one line that holds while the hooks are unapproved; an AGENTS.md over 32 KiB is
truncated SILENTLY by Codex in an untrusted folder. So each piece is asserted here, on the payload.

Exit 0 = every check passed, 1 = at least one failed (each failure is named), 2 = bad usage.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

AGENTS_MD_BUDGET = 32768   # scripts/gen_agent_trees.py INSTRUCTION_BUDGET (Codex truncates past it)
# The script a definition runs, after the install-root placeholder: `{{CODEX_ROOT}}/.codex/hooks/x.ps1`.
SCRIPT_RE = re.compile(r'\{\{CODEX_ROOT(?:_POSIX)?\}\}/([^"\\]+)')


def checks(root: Path):
    """Yield (name, problem-or-None) for every check."""
    defs = root / ".codex" / "hooks.json"
    hooks = None
    if not defs.is_file():
        yield ".codex/hooks.json present and parses", "MISSING"
    else:
        try:
            hooks = json.loads(defs.read_text(encoding="utf-8"))["hooks"]
            yield ".codex/hooks.json present and parses", None
        except (ValueError, KeyError, TypeError) as e:
            yield ".codex/hooks.json present and parses", "does not parse: {}".format(e)
    if hooks is not None:
        raw = defs.read_text(encoding="utf-8")
        # Rendered = it names one machine's folder, and Codex on every other machine runs nothing.
        yield ".codex/hooks.json still unrendered (setup.sh fills it)", (
            None if "{{CODEX_ROOT}}" in raw else "it carries a real install path, not {{CODEX_ROOT}}")
        handlers, missing, unmatched = 0, [], []
        for groups in hooks.values():
            for g in groups:
                for h in g.get("hooks", []):
                    handlers += 1
                    for key in ("command", "commandWindows"):
                        found = SCRIPT_RE.findall(h.get(key, ""))
                        if not found:
                            unmatched.append(key)
                        missing += [p for p in found if not (root / p).is_file()]
        problem = None
        if handlers == 0:
            problem = "no hook commands at all -- this check would be vacuous"
        elif unmatched:
            problem = "a command names no script under the install root ({})".format(", ".join(sorted(set(unmatched))))
        elif missing:
            problem = "missing: " + ", ".join(sorted(set(missing)))
        yield "every Codex hook command's script is in the payload", problem
    rules = root / ".codex" / "rules" / "skyrim.rules"
    yield ".codex/rules/skyrim.rules present", (
        None if rules.is_file() and rules.stat().st_size > 0 else "MISSING or empty")
    claude = sorted(p.parent.name for p in (root / ".claude" / "skills").glob("*/SKILL.md"))
    agents = sorted(p.parent.name for p in (root / ".agents" / "skills").glob("*/SKILL.md"))
    if not claude:
        problem = "no .claude/skills/*/SKILL.md -- this check would be vacuous"
    elif claude != agents:
        problem = "{} Claude skills, {} Codex skills (missing in .agents: {})".format(
            len(claude), len(agents), ", ".join(sorted(set(claude) - set(agents))) or "none")
    else:
        problem = None
    yield ".agents/skills matches .claude/skills ({} each)".format(len(claude)), problem
    agents_md = root / "AGENTS.md"
    if not agents_md.is_file():
        yield "AGENTS.md present and <= {} bytes".format(AGENTS_MD_BUDGET), "MISSING"
    else:
        n = agents_md.stat().st_size
        yield "AGENTS.md present and <= {} bytes".format(AGENTS_MD_BUDGET), (
            None if n <= AGENTS_MD_BUDGET else "{} bytes: Codex would silently truncate it".format(n))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not Path(argv[0]).is_dir():
        sys.stderr.write("usage: check-codex-payload.py <payload root>\n")
        return 2
    fails = 0
    for name, problem in checks(Path(argv[0])):
        print("  {:<58} {}".format(name, "ok" if problem is None else "!! " + problem))
        fails += problem is not None
    print("CODEX PAYLOAD: " + ("OK" if not fails else "{} CHECK(S) FAILED".format(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
