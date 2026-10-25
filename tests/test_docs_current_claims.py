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
    asks = core[core.index("### ASKS THE USER"):core.index("Few prompts by design")]
    # Review C-docs F8: S-OPAQUE-PROTECTED asks only for these three forms; an unreadable command naming the install
    # is allowed (or refused as a delete), never asked about.
    assert "an unnamed program, an eval of a variable, or awk system() given a protected path" in asks
    assert "cannot read" not in asks
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


def _v4_section() -> str:
    import re
    text = _read("CHANGELOG.md")
    m = re.search(r"^(#{1,3})\s*\[?v?4\.0\.0\]?.*?$(.*?)(?=^\1\s|\Z)", text, re.S | re.M)   # release.yml's extractor
    assert m, "no v4.0.0 section"
    return m.group(2)


def test_the_changelog_v4_section_is_the_whole_release():
    """Review L4a M4 / L6 F2: a 201-line '## Unreleased' above '## v4.0.0' never reached the release notes, which
    release.yml extracts from the version heading to the next one."""
    text = _read("CHANGELOG.md")
    first = text.index("\n## ")
    assert text[first:].startswith("\n## v4.0.0"), text[first:first + 40]
    notes = _v4_section()
    for entry in ("housekeep", "House Carl", "S-SAVE-OPAQUE", "Knowledgebase notes get committed"):
        assert entry in notes, entry


def test_the_changelog_describes_the_approval_line_and_ask_rules_the_code_has():
    """Review L2 LOW-5 / L6 F2: the v4.0.0 text showed `{"op": ..., "files": [...]}`, which
    modding_harness.approval.read refuses, and said three rules ask."""
    notes = _v4_section()
    assert '"files": [' not in notes and "Three rules still ask" not in notes
    assert "Five rules ask" in notes and "absolute paths of both files of every save" in notes
    approval = _read("tools/modding_harness/approval.py")
    assert 'data.get("v") != 1 or data.get("session_id") != session_id' in approval
    assert "os.path.isabs(p) for p in data[\"paths\"]" in approval
    assert "Removed: .NET 8/9 support" not in notes and "- **.NET 8/9 support:**" not in notes


def test_pre_arc_carry_overs_are_gone():
    """Review L6 F13: a "confirmation threshold" setting that does not exist, the desktop app recommended without
    saying it is unclaimed, and a v4 README whose first section was "New in v3"."""
    assert "confirmation threshold" not in _read("docs/safety-philosophy.md")
    assert "not covered by the toolkit's hook testing" in _read("docs/getting-started.md")
    readme = _read("README.md")
    assert "## New in v4" in readme and "## New in v3" not in readme


def test_no_doc_says_the_bulk_save_refusal_prints_the_approval_line():
    """Review C-docs F1: the S-SAVE-APPROVAL refusal is two lines pointing to `check --preview` (rules.py template;
    tests/test_approval_preview.py::test_adapting_shell_example_yields_the_grant_the_refusal_does_not_print runs the
    documented route). Docs said the refusal prints or shows the line, which an agent cannot follow."""
    stale = ("line the refusal shows", "line the refusal prints", "approval line the guard shows",
             "line printed by the deciding hook", "the refusal (or `python tools/agent-harness.py check --preview`) prints")
    for rel in ("README.md", "CHANGELOG.md", "ADAPTING.md", "docs/safety-philosophy.md", "agent/instructions/core.md",
                "agent/skills/skyrim-save/SKILL.md"):
        text = _read(rel)
        for phrase in stale:
            assert phrase not in text, (rel, phrase)
    for rel in ("README.md", "docs/safety-philosophy.md", "agent/instructions/core.md", "agent/skills/skyrim-save/SKILL.md"):
        assert "check --preview` prints" in _read(rel) or "check --preview`." in _read(rel), rel


def test_the_v4_fixed_list_names_only_behaviour_3_9_1_shipped():
    """Review C-docs F6: v4.0.0 "Fixed" described pre-release defects of features new in v4 (bench-vr, vr-headless,
    House Carl, housekeep, migrate-3x, mod-install, Codex ...) as if a 3.9.1 user had them. Those lines live under
    "Hardened before release (features new in v4)"; a Fixed bullet's title may not name such a feature."""
    import re
    notes = _v4_section()
    fixed = notes[notes.index("\n### Fixed\n"):notes.index("\n### Removed\n")]
    titles = re.findall(r"^- \*\*(.+?)\*\*", fixed, re.M)
    assert len(titles) > 40, len(titles)
    new_in_v4 = ("Codex", "bench-vr", "vr-headless", "House Carl", "housekeep", "migrate-3x", "3.x migration",
                 "mod-install", "Manual installs", "archive-triage", "skydoctor", "Emulator", "Prefs", "Lock inheritance",
                 "Save reserve", "Guard stores", "sweep", "launcher", "CMD ", "Migration fence", "relock",
                 "Session transcripts", "auto_snapshots", "shared runtime", "Save preservation", "Guards-off",
                 "Shadow-mode", "Adapted agents")
    for title in titles:
        for word in new_in_v4:
            assert word.lower() not in title.lower(), (title, word)
    assert "#### Hardened before release (features new in v4)" in notes
    assert "it used to be refused, as every ask is there" not in notes


def test_logging_registry_never_suggests_a_report_folder_the_guard_will_not_let_anyone_clean():
    """Review C-docs F9: docs/logging-registry.md suggested `.skyguard/logging-registry/`; S-SELF-STATE (in
    NEVER_SHADOW) then refuses mv/rm of those reports, so they could never be rotated."""
    doc = _read("docs/logging-registry.md")
    assert ".skyguard/logging-registry" not in doc
    assert "`_CLAUDE_OUTPUT/logging-registry/`" in doc and "Never `.skyguard/`" in doc


def test_evidence_files_carry_no_cpu_count():
    """Review C-docs F10: the CPU model was scrubbed from docs/evidence, but "28 logical CPUs" stayed (a weaker
    machine fingerprint, removed on the maintainer's call)."""
    import re
    for p in (REPO / "docs" / "evidence").glob("*.md"):
        assert not re.search(r"\b\d+ logical CPUs", p.read_text(encoding="utf-8")), p.name


def test_upgrade_docs_say_new_folder_is_the_zips_inner_versioned_folder():
    """Review C-docs F10: `<new folder>` was ambiguous against the zip's one root `<slug>-<version>/`
    (scripts/build_release_zip.py); the snapshot path and the candidate's relative paths need the inner folder."""
    import json
    slug = json.loads(_read("package.json"))["name"] + "-<version>/"
    for rel in ("README.md", "docs/upgrading-native-hooks.md", "CHANGELOG.md"):
        assert slug in _read(rel), rel
