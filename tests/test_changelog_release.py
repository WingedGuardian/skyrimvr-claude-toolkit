"""scripts/check_changelog_release.py: a release never ships entries the release notes leave out (review L4a M4)."""
import importlib.util
import re
import subprocess
import sys

import pytest

from conftest import REPO

SCRIPT = REPO / "scripts" / "check_changelog_release.py"


def _mod():
    spec = importlib.util.spec_from_file_location("check_changelog_release", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


V4 = "## v4.0.0 — 2026-10-25\n\n### Added\n\n- a thing\n\n## v3.9.1 — 2026-09-09\n\n- old\n"


@pytest.mark.parametrize("text", [
    "# Changelog\n\n" + V4,
    "# Changelog\n\n## Unreleased\n\n" + V4,                    # an empty Unreleased heading is fine
    "# Changelog\n\n## Unreleased\n\n### Added\n\n" + V4,       # ...even with an empty subheading
], ids=["plain", "empty-unreleased", "empty-unreleased-subheading"])
def test_a_changelog_ready_for_the_tag_passes(text):
    assert _mod().problem(text, "4.0.0") == ""


@pytest.mark.parametrize("text", [
    "# Changelog\n\n## Unreleased\n\n- forgotten entry\n\n" + V4,
    "# Changelog\n\n## Unreleased\n\n### Fixed\n\n- forgotten entry\n\n" + V4,
], ids=["entry", "entry-under-subheading"])
def test_entries_above_the_tagged_section_refuse(text):
    assert "fold them in first" in _mod().problem(text, "4.0.0")


@pytest.mark.parametrize("heading", ["## v4.0.0 — unreleased", "## [4.0.0] - Unreleased", "## v4.0.0 (UNRELEASED)"])
def test_a_tagged_heading_that_still_says_unreleased_refuses(heading):
    """review2 C-docs L5: a tag would publish 'unreleased' as the release date."""
    text = "# Changelog\n\n" + V4.replace("## v4.0.0 — 2026-10-25", heading)
    assert "still says 'unreleased'" in _mod().problem(text, "4.0.0")
    # The same word in an OLDER version's heading, or in the body, is not the tagged heading.
    assert _mod().problem("# Changelog\n\n" + V4.replace("## v3.9.1 — 2026-09-09", "## v3.9.1 — unreleased"),
                          "4.0.0") == ""
    assert _mod().problem("# Changelog\n\n" + V4.replace("- a thing", "- unreleased work landed"), "4.0.0") == ""


def test_a_missing_section_refuses():
    assert "no section for v4.0.1" in _mod().problem("# Changelog\n\n" + V4, "4.0.1")


def test_an_unreleased_below_the_tagged_section_does_not_count():
    text = "# Changelog\n\n" + V4.replace("## v3.9.1", "## Unreleased\n\n- stale\n\n## v3.9.1")
    assert _mod().problem(text, "4.0.0") == ""


def test_the_cli_exit_codes_and_the_real_changelog(tmp_path):
    bad = tmp_path / "CHANGELOG.md"
    bad.write_text("## Unreleased\n\n- x\n\n" + V4, encoding="utf-8")
    run = lambda *a: subprocess.run([sys.executable, "-B", str(SCRIPT), *a], capture_output=True, text=True)
    assert run("v4.0.0", str(bad)).returncode == 1
    # The real CHANGELOG is dated by the maintainer at tag time, so until then its only allowed problem is the
    # undated heading: never assert a pass on it (review2 C-docs L5), but anything else is still a failure here.
    real = run("v4.0.0", str(REPO / "CHANGELOG.md"))
    assert real.returncode == 0 or (real.returncode == 1 and "still says 'unreleased'" in real.stdout), real.stdout
    assert run().returncode == 2


def test_the_release_workflow_runs_the_check():
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert re.search(r'^ +python -B scripts/check_changelog_release\.py "\$TAG" \|\|', text, re.M), \
        "the release safety step must RUN the check, as its own command"
