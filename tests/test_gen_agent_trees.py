"""The neutral agent/ source and the per-agent files generated from it."""
import subprocess
from pathlib import Path

from conftest import REPO

GENERATED_GLOBS = ["CLAUDE.md", "AGENTS.md", ".claude/skills/x/SKILL.md", ".claude/hooks/x.sh",
                   ".claude/settings.json", "agent/instructions/core.md", "agent/skills/x/SKILL.md"]


def test_every_generated_path_is_pinned_lf():
    """An autocrlf checkout must not turn generated files CRLF, or the drift gate is red on a clean tree."""
    out = subprocess.run(["git", "check-attr", "eol", "--", *GENERATED_GLOBS],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    unpinned = [line for line in out.splitlines() if not line.endswith(": eol: lf")]
    assert not unpinned, "not pinned to LF:\n" + "\n".join(unpinned)
