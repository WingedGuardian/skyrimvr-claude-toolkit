"""The neutral agent/ source and the per-agent files generated from it."""
import importlib.util
import subprocess
import sys
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


def _gen():
    spec = importlib.util.spec_from_file_location("gen_agent_trees", REPO / "scripts" / "gen_agent_trees.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scratch_source(root: Path) -> Path:
    src = root / "agent"
    (src / "instructions").mkdir(parents=True)
    (src / "instructions" / "core.md").write_bytes(b"# Core\n\nbody\n")
    (src / "instructions" / "claude.md").write_bytes(b"")
    (src / "instructions" / "agents-pointer.md").write_bytes(b"# Repository Guidelines\n")
    sk = src / "skills" / "demo"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_bytes(b"---\nname: demo\ndescription: d\n---\n\nBody.\n\n---\n\nAfter a rule.\n")
    (sk / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    hooks = src / "guards" / "adapters" / "claude"
    hooks.mkdir(parents=True)
    (hooks / "h.sh").write_bytes(b'#!/bin/bash\nPY="{{PYTHON_PATH}}"\nINPUT=$(cat)\n')
    tgt = src / "targets" / "claude"
    tgt.mkdir(parents=True)
    (tgt / "settings.json").write_bytes(b'{\n  "hooks": {}\n}\n')
    return src


def test_render_maps_every_source_to_its_claude_path(tmp_path):
    out = _gen().render("claude", _scratch_source(tmp_path))
    assert set(out) == {"CLAUDE.md", "AGENTS.md", ".claude/skills/demo/SKILL.md",
                        ".claude/skills/demo/pic.png", ".claude/hooks/h.sh", ".claude/settings.json"}


def test_header_goes_after_frontmatter_not_a_body_rule(tmp_path):
    text = _gen().render("claude", _scratch_source(tmp_path))[".claude/skills/demo/SKILL.md"].decode()
    assert text.startswith("---\nname: demo\ndescription: d\n---\n<!-- GENERATED")
    assert text.endswith("Body.\n\n---\n\nAfter a rule.\n")


def test_hook_header_goes_after_the_shebang_and_keeps_one_placeholder(tmp_path):
    text = _gen().render("claude", _scratch_source(tmp_path))[".claude/hooks/h.sh"].decode()
    lines = text.splitlines()
    assert lines[0] == "#!/bin/bash" and lines[1].startswith("# GENERATED")
    assert text.count("{{PYTHON_PATH}}") == 1


def test_binary_skill_file_passes_through(tmp_path):
    out = _gen().render("claude", _scratch_source(tmp_path))
    assert out[".claude/skills/demo/pic.png"] == b"\x89PNG\r\n\x1a\n\x00\xff"


def test_json_gets_no_header(tmp_path):
    assert _gen().render("claude", _scratch_source(tmp_path))[".claude/settings.json"] == b'{\n  "hooks": {}\n}\n'


def test_crlf_source_generates_lf(tmp_path):
    src = _scratch_source(tmp_path)
    (src / "instructions" / "core.md").write_bytes(b"# Core\r\n\r\nbody\r\n")
    assert b"\r" not in _gen().render("claude", src)["CLAUDE.md"]


def test_headers_never_contain_a_placeholder_token():
    g = _gen()
    assert "{{" not in g.MD_HEADER and "{{" not in g.SH_HEADER


def test_write_then_check_is_clean_and_idempotent(tmp_path):
    g = _gen()
    out = g.render("claude", _scratch_source(tmp_path))
    assert g.write(out, tmp_path)            # first write changes files
    assert g.write(out, tmp_path) == []      # second write changes nothing
    assert g.check(out, tmp_path) == []


def test_check_names_a_hand_edited_file(tmp_path):
    g = _gen()
    out = g.render("claude", _scratch_source(tmp_path))
    g.write(out, tmp_path)
    (tmp_path / ".claude" / "hooks" / "h.sh").write_bytes(b"#!/bin/bash\nedited\n")
    problems = g.check(out, tmp_path)
    assert any(".claude/hooks/h.sh" in p for p in problems)


def test_check_names_an_orphan_in_an_owned_dir(tmp_path):
    g = _gen()
    out = g.render("claude", _scratch_source(tmp_path))
    g.write(out, tmp_path)
    rogue = tmp_path / ".claude" / "skills" / "rogue" / "SKILL.md"
    rogue.parent.mkdir(parents=True)
    rogue.write_bytes(b"---\nname: rogue\n---\n")
    assert any("rogue" in p and "not generated" in p for p in g.check(out, tmp_path))


def test_check_explains_an_installed_copy(tmp_path):
    g = _gen()
    out = g.render("claude", _scratch_source(tmp_path))
    g.write(out, tmp_path)
    h = tmp_path / ".claude" / "hooks" / "h.sh"
    h.write_bytes(h.read_bytes().replace(b"{{PYTHON_PATH}}", b"C:/python.exe"))
    assert any("setup.sh" in p for p in g.check(out, tmp_path))


def test_crlf_target_file_generates_lf(tmp_path):
    """A JSON/.example target checked out CRLF must still generate LF (no header, but normalised)."""
    src = _scratch_source(tmp_path)
    (src / "targets" / "claude" / "settings.local.json.example").write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
    assert _gen().render("claude", src)[".claude/settings.local.json.example"] == b'{\n  "a": 1\n}\n'


def test_repo_is_in_sync():
    """The committed generated files equal what agent/ renders. Fails on a hand edit or a stale regen."""
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "gen_agent_trees.py"), "--check"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_real_skill_keeps_frontmatter_first():
    for f in sorted((REPO / ".claude" / "skills").glob("*/SKILL.md")):
        text = f.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f
        fm_end = text.index("\n---\n", 3) + len("\n---\n")
        assert text[fm_end:].startswith("<!-- GENERATED"), f


def test_binaries_under_generated_dirs_are_never_treated_as_text():
    """A text eol rule on agent/** or .claude/** overrides `*.png binary` and strips CRLF bytes from images."""
    out = subprocess.run(["git", "check-attr", "text", "--", "agent/skills/x/pic.png", ".claude/skills/x/pic.png",
                          "agent/skills/x/tex.dds", ".claude/skills/x/clip.wav"],
                         cwd=REPO, capture_output=True, text=True, check=True).stdout
    forced = [line for line in out.splitlines() if line.endswith(": text: set")]
    assert not forced, "binary forced to text:\n" + "\n".join(forced)


def test_write_refuses_an_installed_copy_and_leaves_it_untouched(tmp_path, capsys):
    """Regenerating inside an install would reset setup.sh's filled placeholders, disarming every hook."""
    g = _gen()
    _scratch_source(tmp_path)
    assert g.main(["--root", str(tmp_path)]) == 0
    hook = tmp_path / ".claude" / "hooks" / "h.sh"
    configured = hook.read_bytes().replace(b"{{PYTHON_PATH}}", b"C:/python.exe")
    hook.write_bytes(configured)
    assert g.main(["--root", str(tmp_path)]) == 2
    assert hook.read_bytes() == configured
    assert "setup.sh" in capsys.readouterr().out


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
    """Sized by the LITERAL Codex limit (spec E7/D9), never by the constant: a test that scales with
    INSTRUCTION_BUDGET still passes when someone lifts it to 10**9 (caught by the mutation gate)."""
    g = _gen()
    problems = g.budget_problems(_with_core(tmp_path, 32768))
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


def test_check_reports_an_over_budget_core(tmp_path, capsys):
    g = _gen()
    _with_core(tmp_path, g.INSTRUCTION_BUDGET)
    assert g.main(["--root", str(tmp_path)]) == 0          # generate it in sync first
    assert g.main(["--root", str(tmp_path), "--check"]) == 1
    assert "Codex silently truncates" in capsys.readouterr().out
