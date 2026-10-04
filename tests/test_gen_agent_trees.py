"""The neutral agent/ source and the per-agent files generated from it."""
import importlib.util
import subprocess
import sys
from pathlib import Path

from conftest import REPO

GENERATED_GLOBS = ["CLAUDE.md", "AGENTS.md", ".claude/skills/x/SKILL.md", ".claude/hooks/x.sh",
                   ".claude/settings.json", "agent/instructions/core.md", "agent/skills/x/SKILL.md",
                   ".agents/skills/x/SKILL.md", ".codex/hooks/skyguard-entry.ps1", ".codex/hooks/skyguard-entry.sh",
                   ".codex/hooks.json", ".codex/config.toml", ".codex/rules/skyrim.rules"]


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
    assert set(out) == {"CLAUDE.md", ".claude/skills/demo/SKILL.md",
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
    assert g.main(["--root", str(tmp_path), "--target", "claude"]) == 0
    hook = tmp_path / ".claude" / "hooks" / "h.sh"
    configured = hook.read_bytes().replace(b"{{PYTHON_PATH}}", b"C:/python.exe")
    hook.write_bytes(configured)
    assert g.main(["--root", str(tmp_path), "--target", "claude"]) == 2
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


CODEX_LIMIT = 32768   # spec E7/D9, LITERAL on purpose: a test sized by the constant scales with any lift


def _core_installing_to(g, total):
    """Core length whose worst-case INSTALLED size (header + core + both reserves) is exactly `total`."""
    header = len(g._headed("agent/instructions/core.md + codex.md", b"", ".md"))
    return total - header - g.CODEX_ADDENDUM_RESERVE - g.INSTALL_EXPANSION_RESERVE


def test_budget_rejects_an_oversize_core(tmp_path):
    """One byte over the literal limit must fail -- so lifting INSTRUCTION_BUDGET even slightly goes red."""
    g = _gen()
    src = _with_core(tmp_path, _core_installing_to(g, CODEX_LIMIT + 1))
    assert g.instruction_sizes(src)["codex.md (reserved)"] == CODEX_LIMIT + 1
    problems = g.budget_problems(src)
    assert problems and "Codex silently truncates" in problems[0]


def test_budget_accepts_a_core_exactly_at_the_limit(tmp_path):
    """The twin: exactly 32,768 installed bytes is allowed, so the limit is not quietly lowered either."""
    g = _gen()
    src = _with_core(tmp_path, _core_installing_to(g, CODEX_LIMIT))
    assert g.instruction_sizes(src)["codex.md (reserved)"] == CODEX_LIMIT
    assert g.budget_problems(src) == []


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
    assert g.main(["--root", str(tmp_path), "--target", "claude"]) == 0          # generate it in sync first
    assert g.main(["--root", str(tmp_path), "--check", "--target", "claude"]) == 1
    assert "Codex silently truncates" in capsys.readouterr().out


# --- the Codex target (Plan 4 Task 10) -------------------------------------------------------------

def _codex_source(root: Path) -> Path:
    src = _scratch_source(root)
    (src / "instructions" / "codex.md").write_bytes(b"\n## Codex specifics\n\nbe careful\n")
    ad = src / "guards" / "adapters" / "codex"
    ad.mkdir(parents=True)
    (ad / "skyguard-entry.ps1").write_bytes(b"# entry\r\n$py = '{{PYTHON_PATH}}'\r\n")
    (ad / "skyguard-entry.sh").write_bytes(b"#!/bin/bash\nPY=\"{{PYTHON_PATH}}\"\n")
    tgt = src / "targets" / "codex"
    tgt.mkdir(parents=True)
    (tgt / "hooks.json").write_bytes(b'{\r\n  "hooks": {}\r\n}\r\n')
    (tgt / "config.toml").write_bytes(b"project_doc_max_bytes = 65536\n")
    (tgt / "FROZEN.sha256").write_bytes(b"deadbeef  hooks.json\n")
    return src


def test_codex_target_maps_every_source(tmp_path):
    out = _gen().render("codex", _codex_source(tmp_path))
    assert set(out) == {"AGENTS.md", ".agents/skills/demo/SKILL.md", ".agents/skills/demo/pic.png",
                        ".codex/hooks/skyguard-entry.ps1", ".codex/hooks/skyguard-entry.sh",
                        ".codex/hooks.json", ".codex/config.toml", ".codex/rules/skyrim.rules"}
    assert not any("FROZEN" in k for k in out)


def test_agents_md_is_core_plus_codex_addendum(tmp_path):
    g = _gen()
    text = g.render("codex", _codex_source(tmp_path))["AGENTS.md"].decode()
    assert text.startswith("<!-- GENERATED by scripts/gen_agent_trees.py from agent/instructions/core.md + codex.md")
    assert text.endswith("# Core\n\nbody\n\n## Codex specifics\n\nbe careful\n")


def test_claude_target_no_longer_renders_agents_md(tmp_path):
    assert "AGENTS.md" not in _gen().render("claude", _codex_source(tmp_path))


def test_no_path_is_owned_by_two_targets(tmp_path):
    g = _gen()
    src = _codex_source(tmp_path)
    union = g.render_all(src)
    assert len(union) == len(g.render("claude", src)) + len(g.render("codex", src))
    # twin: a second target that claims a path another target owns must raise, not overwrite
    g.TARGETS["dup"] = lambda s: {"CLAUDE.md": b"x"}
    try:
        try:
            g.render_all(src)
        except ValueError as e:
            assert "CLAUDE.md" in str(e)
        else:
            raise AssertionError("render_all let two targets own CLAUDE.md")
    finally:
        del g.TARGETS["dup"]


def test_hooks_json_gets_no_header_and_ps1_gets_one_on_line_one(tmp_path):
    out = _gen().render("codex", _codex_source(tmp_path))
    assert out[".codex/hooks.json"] == b'{\n  "hooks": {}\n}\n'
    ps1 = out[".codex/hooks/skyguard-entry.ps1"].decode().split("\n")
    assert ps1[0].startswith("# GENERATED") and ps1[1] == "# entry" and "\r" not in "".join(ps1)
    assert out[".codex/config.toml"].decode().split("\n")[0].startswith("# GENERATED")
    assert out[".codex/rules/skyrim.rules"].decode().split("\n")[0].startswith("# GENERATED")
    sh = out[".codex/hooks/skyguard-entry.sh"].decode().split("\n")
    assert sh[0] == "#!/bin/bash" and sh[1].startswith("# GENERATED")


def test_codex_rules_are_the_renderers_output_and_come_from_this_checkout(tmp_path):
    """The rules text is code, not agent/ source: a scratch --root has no tools/, yet the rules render."""
    spec = importlib.util.spec_from_file_location("cr", REPO / "tools" / "skyguard" / "codex_rules.py")
    cr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cr)
    body = _gen().render("codex", _codex_source(tmp_path))[".codex/rules/skyrim.rules"].decode()
    assert body.endswith(cr.render())


def test_installed_agents_md_fits_the_budget():
    """Plan 3's budget, now with the REAL addendum: header + core + codex.md + install expansion <= 32,768."""
    sizes = _gen().instruction_sizes(REPO / "agent")
    assert "codex.md" in sizes and "codex.md (reserved)" not in sizes, sizes
    assert sizes["codex.md"] <= 32768, sizes


def test_codex_md_stays_small():
    n = len((REPO / "agent" / "instructions" / "codex.md").read_bytes().replace(b"\r\n", b"\n"))
    assert n <= 3000, n


def test_codex_target_is_written_and_checked_by_default(tmp_path):
    g = _gen()
    _codex_source(tmp_path)
    assert g.main(["--root", str(tmp_path)]) == 0
    assert (tmp_path / ".codex" / "config.toml").is_file() and (tmp_path / "CLAUDE.md").is_file()
    assert g.main(["--root", str(tmp_path), "--check"]) == 0
    (tmp_path / ".codex" / "config.toml").write_bytes(b"edited = 1\n")
    assert g.main(["--root", str(tmp_path), "--check"]) == 1


def test_check_names_an_orphan_in_a_codex_owned_dir(tmp_path):
    g = _gen()
    src = _codex_source(tmp_path)
    out = g.render_all(src)
    g.write(out, tmp_path)
    for rogue in (".codex/rules/extra.rules", ".codex/hooks/extra.ps1", ".agents/skills/rogue/SKILL.md"):
        p = tmp_path / rogue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        assert any(rogue in m and "not generated" in m for m in g.check(out, tmp_path)), rogue
        p.unlink()


def test_codex_tree_lf_pin_is_a_path_rule_that_comes_after_the_ps1_rule():
    """Read the file itself (no git needed, so a mutation copy exercises it): the last matching line wins, so the
    `.codex/**` pin must follow `*.ps1 text eol=crlf`; and `*.ps1` itself must stay CRLF (Windows helpers)."""
    lines = [l.split("#")[0].split() for l in (REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()]
    lines = [l for l in lines if l]
    ps1 = next(i for i, l in enumerate(lines) if l[0] == "*.ps1")
    pin = next(i for i, l in enumerate(lines) if l[0] == ".codex/**")
    assert lines[ps1][1:] == ["text", "eol=crlf"]
    assert lines[pin][1:] == ["text", "eol=lf"] and pin > ps1
    assert not any(l[0].startswith((".agents/", "agent/skills")) and l[0].endswith("**") for l in lines), \
        "a directory-wide text rule over a folder that can hold images overrides `*.png binary`"
