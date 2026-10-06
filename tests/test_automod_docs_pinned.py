"""Every user-facing copy of the AutoMod install gives the PINNED clone and the wrapper's --rebuild.

Plan 8 review I1: README and installing-tools were updated, but the setup prompt -- the copy people actually
paste, embedded verbatim in README and getting-started -- still said "clone ... and dotnet build ...Cli".
That gets upstream HEAD (not the tested commit) and a net8.0 DLL the v4 wrapper refuses, so a fresh
install reported "built for net8.0 by an older toolkit". A fix that reaches some copies of an instruction
and not the one that is followed is the failure this test exists for.

Scanned: every Markdown/text file a user or agent reads as instructions, plus setup.sh. Not scanned: plan
and measurement history (docs/superpowers/) and CHANGELOG.md, which describe what older versions did.
"""
from __future__ import annotations

import re

from conftest import REPO

PIN_TAG = "v1.11.2"
SKIP_DIRS = {".git", "node_modules", ".superpowers"}
SKIP_PREFIXES = ("docs/superpowers/",)
SKIP_FILES = {"CHANGELOG.md"}
# A line that clones AutoMod must name the pinned tag; nothing may `dotnet build` its projects directly.
CLONE = re.compile(r"(?i)\bclon(e|ing)\b.*spookys-automod-toolkit|spookys-automod-toolkit.*\bclon(e|ing)\b")
BARE_BUILD = re.compile(r"dotnet build[^\n]*SpookysAutomod")


def _files():
    for path in sorted(REPO.rglob("*")):
        rel = path.relative_to(REPO).as_posix()
        if (not path.is_file() or set(path.relative_to(REPO).parts) & SKIP_DIRS
                or rel.startswith(SKIP_PREFIXES) or rel in SKIP_FILES):
            continue
        if path.suffix in (".md", ".txt") or rel == "setup.sh":
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def _problems(rel, text):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if CLONE.search(line) and PIN_TAG not in line:
            out.append(f"{rel}:{n}: clones AutoMod without the pinned tag {PIN_TAG}")
        if BARE_BUILD.search(line):
            out.append(f"{rel}:{n}: builds AutoMod with a bare dotnet build (use bash tools/automod-cli.sh --rebuild)")
    return out


def test_no_user_facing_copy_clones_automod_unpinned_or_builds_it_bare():
    problems = [p for rel, text in _files() for p in _problems(rel, text)]
    assert not problems, "\n".join(problems)


def test_the_scan_covers_the_copies_that_matter():
    scanned = {rel for rel, _ in _files()}
    for rel in ("SETUP_PROMPT.txt", "README.md", "docs/getting-started.md", "docs/installing-tools.md", "setup.sh"):
        assert rel in scanned, rel


def test_the_check_can_fail():
    """The exact v3 prompt wording, and the old setup.sh lines, are flagged."""
    old_prompt = ("it is a real tool you install by cloning https://github.com/SpookyPirate/spookys-automod-toolkit into "
                  "tools/automod and building the Cli project (dotnet build tools/automod/src/SpookysAutomod.Cli -c Release)")
    assert len(_problems("x", old_prompt)) == 2
    assert _problems("x", 'echo "  git clone https://github.com/SpookyPirate/spookys-automod-toolkit into tools/automod"')
    assert not _problems("x", "git clone --branch v1.11.2 https://github.com/SpookyPirate/spookys-automod-toolkit tools/automod")
