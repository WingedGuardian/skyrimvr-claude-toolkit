"""User docs may not describe behaviour the v4 code no longer has (release review L6, 2026-10-20).

Each test pins one claim that went stale inside the v4 arc: the stale wording must stay gone, and where the doc
states the current behaviour, that statement must be present. The code side of each claim is tested elsewhere
(named in each test); this file only keeps the prose from drifting back.
"""
from __future__ import annotations

from conftest import REPO


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_saves_are_backed_up_not_confirmed():
    """W-SAVE / S-WRITE-SAVE went with the 2026-10-13 save policy: a write onto 1-2 saves is backed up and allowed
    (tests/test_save_policy.py::test_an_edit_tool_write_onto_a_save_is_backed_up and
    ::test_a_devbench_save_over_an_existing_save_is_backed_up)."""
    readme = _read("README.md")
    assert "its one prompt is a write onto a save" not in readme
    assert "existing save is refused with `NEEDS YOUR CONFIRMATION`" not in readme
    assert "A write onto one or two saves in `Saves/` is backed up first, then runs" in readme
    assert "existing save is backed up first, then runs" in readme
    assert "save-over-a-save confirmation" not in _read("ADAPTING.md")


def test_the_update_answer_names_a_path_a_user_can_follow():
    """Review L6 F4/F7: the FAQ sent users to a "private" `upgrade-stage` workflow with no program named and a
    required inventory that is not shipped, inside a file that tells Claude/Codex to stop reading."""
    readme = _read("README.md")
    assert "For the 4.0 candidate" not in readme and "the private `upgrade-stage`" not in readme
    assert "pre-update-snapshot.sh" in readme and "bash setup.sh --agent auto" in readme
    upgrading = _read("docs/upgrading-native-hooks.md")
    assert 'tools/agent-harness.py" upgrade-preview --installed-root' in upgrading
    assert "make it from the unpacked folder" in upgrading
    adapting = _read("ADAPTING.md")
    assert adapting.startswith("# ADAPTING.md") and "protect-save-access" not in adapting
    assert "upgrade-stage" not in adapting and "integrate-local-policy" not in adapting
    assert "**Which entry to call.**" in adapting


def test_sed_without_binary_is_listed_as_refused_and_the_ask_rules_are_five():
    """S-SED-CRLF denies `sed -i` without -b in the install area (tests/test_skyguard_shell.py::
    test_sed_crlf_in_the_install_area_denies); core.md listed it as ADVISED. The ask rules are five once the
    unreadable-command-naming-the-install rule lands (user ruling 3 of the v4 review)."""
    core = _read("agent/instructions/core.md")
    denied = core[core.index("### DENIED"):core.index("### ADVISED")]
    advised = core[core.index("### ADVISED"):core.index("### ASKS THE USER")]
    assert "`sed -i` without `-b`" in denied
    assert "`sed -i` " not in advised and "`sed -i`," not in advised
    assert "### ASKS THE USER -- five rules" in core
    assert "a command the guard cannot read that names the install" in core
    assert "`sed -i` without `-b`" in _read("README.md")
    for rel, phrase in (("README.md", "Only five rules ask"), ("docs/safety-philosophy.md", "Five rules in the toolkit")):
        assert phrase in _read(rel), rel


def test_user_docs_carry_no_pre_release_plan_wording():
    """Review L6 F12: "after Plan 2 certification" (README), "Plan 5 Task 14." as guard-coverage's opening line."""
    assert "Plan 2 certification" not in _read("README.md")
    assert "Plan 5 Task 14" not in _read("docs/guard-coverage.md")
    assert "Plan 5, Task 15" not in _read("docs/harness-integrations.md")


def test_the_codex_subfolder_case_is_stated_one_way():
    """Review L6 F6: "worked in 0.162.1" (README) against "never a subfolder: no hook runs" (CHANGELOG)."""
    for rel in ("README.md", "CHANGELOG.md"):
        assert "from a subfolder no hook runs" not in _read(rel), rel
    assert "it depends on the version" in _read("README.md")


def test_dotnet_requirements_admit_the_house_carl_runtimes():
    """Review L6 F8: ".NET 10 SDK, the toolkit's single .NET requirement" while tools/housecarl_integration/setup.py
    requires Microsoft.NETCore.App 9 and Microsoft.AspNetCore.App 9."""
    setup = _read("tools/housecarl_integration/setup.py")
    assert '"Microsoft.NETCore.App 9."' in setup and '"Microsoft.AspNetCore.App 9."' in setup
    tools = _read("docs/installing-tools.md")
    assert "the toolkit's single .NET requirement" not in tools
    assert "House Carl gateway needs its own, older .NET runtimes" in tools


def _ticked(text: str):
    import re
    return set(re.findall(r"`([a-z0-9-]+)`", text))


def test_the_neutral_skill_lists_are_the_gate_and_the_skill_list_is_the_folder():
    """Review L6 F9: README omitted mod-update-check, agent/README omitted toolkit-setup from the neutral list and
    toolkit-housecarl from the skill list."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("neutrality_for_docs", REPO / "tests" / "test_instruction_neutrality.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    neutral = {p.split("/")[2] for p in gate.NEUTRAL_FILES if p.startswith("agent/skills/")}
    readme = _read("README.md")
    said = readme[readme.index("The core\nand the `devbench`"):readme.index("must also stay **agent-neutral**")]
    assert _ticked(said) == neutral
    agent = _read("agent/README.md")
    said = agent[agent.index("**Neutrality.**"):agent.index("no Claude-only tool names")]
    assert _ticked(said) - {"core.md"} == neutral
    listed = agent[agent.index("Skills (`skills/<name>/SKILL.md`):"):agent.index("hold what used to be bulk")]
    listed = listed[:listed.rindex("\n")]
    folder = {p.name for p in (REPO / "agent" / "skills").iterdir() if (p / "SKILL.md").is_file()}
    assert _ticked(listed) - {"skills"} == folder


def test_skill_sentences_are_whole():
    """Review L6 F10/F11: skyrim-save introduced a deleted example with a colon; devbench named 1.24.0 as the newest
    release read against while saying 1.28.0 in the same sentence."""
    save = _read("agent/skills/skyrim-save/SKILL.md")
    assert "the line the refusal prints):\n\n" not in save
    devbench = _read("agent/skills/devbench/SKILL.md")
    assert "1.24.0, which is the newest release" not in devbench
    assert "1.28.0 (2026-10-11) is the newest release" in devbench
