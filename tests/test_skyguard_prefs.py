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
    raw = "﻿## my PREFERENCES  \r\n- x\r\n".encode("utf-8")
    assert "- x" in _render(_kb(tmp_path, raw, raw=True))


def test_empty_section_injects_nothing(tmp_path):
    assert _render(_kb(tmp_path, "## My preferences\n\n   \n## Next\n- y\n")) == ""


def test_long_section_is_capped_on_a_character_boundary(tmp_path):
    body = "x" + "é" * prefs.CAP             # 1 + 2n bytes: the cut at CAP lands mid-character
    out = _render(_kb(tmp_path, "## My preferences\n" + body + "\n"))
    assert "truncated" in out and KB in out
    out.encode("utf-8")                           # no lone surrogate / broken char
    assert "�" not in out                    # the half character was dropped, not replaced
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
    (tmp_path / "tools" / "kb-guard.sh").write_text("exit 0\n", encoding="utf-8", newline="\n")
    _kb(tmp_path, "## My preferences\n- PREF-CANARY\n")
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


def test_an_unreadable_paths_env_does_not_stop_preferences(tmp_path):
    """Plan 2 made an unreadable skyrim-paths.env fail CLOSED for protect-bash. SessionStart does not
    read that file, so a broken one must neither block the session nor the preferences."""
    root = install_hooks(tmp_path, names={"session-kb-guard.sh"})
    (root / ".claude" / "skyrim-paths.env").mkdir()          # exists, cannot be read as a file
    _kb(root, "## My preferences\n- PREF-CANARY-env\n")
    r = subprocess.run([BASH, str(root / ".claude" / "hooks" / "session-kb-guard.sh")],
                       input=b'{"source":"startup"}', capture_output=True, timeout=120,
                       env=dict(os.environ, CLAUDE_PROJECT_DIR=str(root)))
    assert r.returncode == 0
    assert b"PREF-CANARY-env" in r.stdout
