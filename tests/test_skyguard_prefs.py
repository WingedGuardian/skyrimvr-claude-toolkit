"""D15: the user's `## My preferences` reaches the agent at session start, through the real hook."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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


# --- Plan 8 T6 (deferred rows 18-20): an unclosed fence, the variant heading, a bounded read ---------------------
def _audit_text(tmp_path):
    p = tmp_path / ".claude" / "backups" / "AUDIT_LOG.txt"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def test_an_unclosed_fence_does_not_swallow_the_sections_after_it(tmp_path):
    """Row 18: a fence opened and never closed used to run to the end of the file, so every later section (here a
    private one) was injected as a preference, up to the cap."""
    out = _render(_kb(tmp_path, "## My preferences\n- a\n```\n- b\n## Private notes\n- SECRET-LATER\n"))
    assert "- a" in out and "- b" in out
    assert "SECRET-LATER" not in out


def test_an_unclosed_fence_before_the_heading_does_not_hide_it(tmp_path):
    out = _render(_kb(tmp_path, "# KB\n```\nstray fence\n## My preferences\n- REAL\n## Next\n- no\n"))
    assert "- REAL" in out and "- no" not in out


def test_a_closed_fence_still_hides_headings_inside_it(tmp_path):
    """The twin: only an UNCLOSED fence is read as plain text; a closed one (even with a ~~~ line inside a ``` block)
    still keeps a heading inside it out of the section search."""
    text = "```\n~~~\n## My preferences\n- fake\n```\n## My preferences\n- real\n"
    out = _render(_kb(tmp_path, text))
    assert "- real" in out and "- fake" not in out


def test_the_variant_heading_with_a_parenthetical_is_the_section(tmp_path):
    """Row 19: `## My Preferences (read every session)` injected nothing and logged nothing."""
    out = _render(_kb(tmp_path, "## My Preferences (read every session)\n- VARIANT\n## Next\n- no\n"))
    assert "- VARIANT" in out and "- no" not in out


def test_a_near_miss_heading_says_so_and_is_audited(tmp_path):
    """A heading that is evidently meant as the section but is not it (another level, or extra words) is named in
    one line and an audit line, never silently dropped."""
    out = _render(_kb(tmp_path, "# KB\n### My preferences for combat\n- x\n"))
    assert out.startswith("PREFERENCES: NOT INJECTED") and "### My preferences for combat" in out
    assert "## My preferences" in out
    assert "prefs NOT INJECTED" in _audit_text(tmp_path)


def test_a_file_without_any_preferences_heading_stays_silent(tmp_path):
    """The near-miss note is only for a heading that names the section, not for an ordinary knowledgebase."""
    assert _render(_kb(tmp_path, "# Notes\n## Engine preferences of the renderer\n- a\n")) == ""
    assert _audit_text(tmp_path) == ""


def test_the_read_is_bounded(tmp_path):
    """Row 20: the file was read whole. Only the first READ_CAP bytes are read now: a section that starts after
    them is not found (an unbounded read would inject it), and the reason is said and audited."""
    filler = ("x" * 99 + "\n") * (prefs.READ_CAP // 100 + 10)
    out = _render(_kb(tmp_path, "# KB\n" + filler + "## My preferences\n- BEYOND-CAP\n"))
    assert "BEYOND-CAP" not in out
    assert out.startswith("PREFERENCES: NOT INJECTED") and "larger than" in out
    assert "prefs NOT INJECTED" in _audit_text(tmp_path)


def test_a_section_at_the_top_of_a_large_file_is_still_injected(tmp_path):
    filler = ("y" * 99 + "\n") * (prefs.READ_CAP // 100 + 10)
    out = _render(_kb(tmp_path, "## My preferences\n- TOP\n## Rest\n" + filler))
    assert out.startswith("USER PREFERENCES") and "- TOP" in out


def test_a_section_cut_by_the_read_cap_says_it_was_cut(tmp_path):
    """A section that runs past the end of what was read is marked as cut, like the 4,096-byte cap."""
    filler = ("z" * 99 + "\n") * (prefs.READ_CAP // 100 - 1)
    out = _render(_kb(tmp_path, "# KB\n" + filler + "## My preferences\n- START\n" + "- more\n" * 100))
    assert "- START" in out and "truncated" in out


# Review I1: `_fenced` rescanned the rest of the file from every opener that never closes (```a has an info string,
# so it can never be a closer): 120 KB took 138 s, past the 120 s SessionStart timeout. Run in a child process with
# a hard timeout, so the quadratic form fails in seconds instead of hanging the suite.
# MEASURED 2026-10-06 (this machine, load ~20 % CPU, 3 runs each at the 1 MiB cap): 0.16-0.29 s for the four
# shapes below; the quadratic form did not finish the first in 30 s. The bound keeps about 3x headroom.
FAST_S = 1.0


@pytest.mark.parametrize("unit", ["```a\n", "````\n```\n", "~~~~ x\n```\n", "```\n"],
                         ids=["never-closing-openers", "short-closers-under-long-openers", "mixed-characters",
                              "closing-pairs"])
def test_a_worst_case_file_at_the_read_cap_renders_fast(tmp_path, unit):
    _kb(tmp_path, unit * (prefs.READ_CAP // len(unit)))
    code = ("import sys, time; sys.path.insert(0, {!r}); from skyguard import prefs, audit; "
            "a = audit.Audit({!r}); t = time.perf_counter(); prefs._render({!r}, a); "
            "print(time.perf_counter() - t)").format(str(REPO / "tools"), str(tmp_path), str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    took = float(r.stdout.strip())
    assert took < FAST_S, "{:.3f} s for a {} byte file".format(took, prefs.READ_CAP)


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
