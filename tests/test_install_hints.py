"""Every runtime install command a user is shown is authorization-aware (Plan 8 T7, deferred row 50).

Two forms only:
  * a runtime the toolkit-setup skill lets an agent install when the user's task authorizes exactly that id and
    version (tools/inventory-runtimes.py ALLOWED_IDS: Python.Python.3.<minor>, Git.Git, OpenJS.NodeJS[.LTS]):
    the exact `winget install --id <id> --exact --version <v> --no-upgrade`, labelled "for you to run";
  * anything else (the .NET SDK, a JDK): labelled a "manual step" that "an agent may not install".
Any other host-installer (choco, scoop, msiexec, Install-Package, `winget upgrade`) is outside both forms.

The allowlist is the parser's own constant, imported -- never a second copy that could drift from it.
"""
from __future__ import annotations

import importlib.util
import re

import pytest

from conftest import REPO

SCANNED = ["setup.sh", "README.md", "docs/installing-tools.md", "docs/getting-started.md", "tools/toolchain-check.sh"]
WINDOW = 2   # the label may sit on the command's line or up to two lines above it (setup.sh prints it first)
# winget's `add` is an alias of install; `winget.exe` is the same program (T7 review M3).
ANY_WINGET = re.compile(r"\bwinget(?:\.exe)?\s+(?P<verb>install|add|upgrade|update)\b", re.I)
FORM = re.compile(r"winget install --id (?P<id>[A-Za-z0-9.]+) --exact --version "
                  r"(?P<v>\d+(?:\.\d+){1,3}|%s|\$\{?[A-Z_]+\}?) --no-upgrade(?![\w-])")
OTHER = re.compile(r"\b(?:choco|scoop)\s+install\b|\bmsiexec\b|\bInstall-Package\b|\bInstall-Module\b")
FOR_YOU = "for you to run"
MANUAL = ("manual step", "an agent may not install this")


def _inv():
    spec = importlib.util.spec_from_file_location("inventory_runtimes", REPO / "tools" / "inventory-runtimes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def allowlisted(pkg_id: str) -> bool:
    inv = _inv()
    for ids in inv.ALLOWED_IDS.values():
        for allowed in ids:
            if "<minor>" in allowed:
                m = re.fullmatch(r"Python\.Python\.3\.([0-9]+)", pkg_id)
                if m and (3, int(m.group(1))) >= inv.PYTHON_FLOOR:
                    return True
            elif pkg_id == allowed:
                return True
    return False


def _comment(rel: str, line: str) -> bool:
    """A line the user never sees: a shell comment, or a Markdown/HTML comment. It can neither be a hint nor label one."""
    s = line.strip()
    return (rel.endswith(".sh") and s.startswith("#")) or (s.startswith("<!--") and s.endswith("-->"))


def problems(rel: str, text: str) -> list:
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _comment(rel, line):
            continue
        ctx = " ".join(x for x in lines[max(0, i - WINDOW):i + 1] if not _comment(rel, x)).lower()
        if OTHER.search(line):
            out.append("%s:%d: a host installer outside the two forms: %s" % (rel, i + 1, line.strip()[:120]))
        for m in ANY_WINGET.finditer(line):
            form = FORM.match(line, m.start())
            if not form:
                out.append("%s:%d: winget %s not in the exact form `winget install --id <id> --exact --version <v> "
                           "--no-upgrade`: %s" % (rel, i + 1, m.group("verb"), line.strip()[:140]))
                continue
            if allowlisted(form.group("id")):
                if FOR_YOU not in ctx:
                    out.append("%s:%d: %s is allowlisted, but the hint does not say %r" % (
                        rel, i + 1, form.group("id"), FOR_YOU))
            elif not all(k in ctx for k in MANUAL):
                out.append("%s:%d: %s is not on the allowlist, so the hint must say it is a manual step an agent may "
                           "not install" % (rel, i + 1, form.group("id")))
    return out


@pytest.mark.parametrize("rel", SCANNED)
def test_every_install_hint_is_in_an_authorized_form(rel):
    text = (REPO / rel).read_text(encoding="utf-8")
    assert problems(rel, text) == []


def test_the_scan_sees_hints_in_every_scanned_file():
    """A scan that matches nothing proves nothing: each file must hold at least one winget hint it judged."""
    for rel in SCANNED:
        assert ANY_WINGET.search((REPO / rel).read_text(encoding="utf-8")), rel


GOOD_ALLOWED = "For you to run: winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade"
GOOD_MANUAL = ("Manual step, for you to run (an agent may not install this): "
               "winget install --id Microsoft.DotNet.SDK.10 --exact --version 10.0.401 --no-upgrade")


@pytest.mark.parametrize("line,bad", [
    (GOOD_ALLOWED, False),
    (GOOD_MANUAL, False),
    ("For you to run: winget install --id Python.Python.3.12 --exact --version 3.12.10 --no-upgrade", False),
    ("For you to run: winget install --id Git.Git --exact --version 2.48.1 --no-upgrade", False),
    (GOOD_ALLOWED.replace(" --no-upgrade", ""), True),                    # the upgrade guard dropped
    (GOOD_ALLOWED.replace("--exact ", ""), True),
    (GOOD_ALLOWED.replace("--id ", ""), True),
    (GOOD_ALLOWED.replace("For you to run: ", "Run: "), True),            # no label
    (GOOD_ALLOWED.replace("--no-upgrade", "--no-upgrade-ish"), True),
    (GOOD_MANUAL.replace(" (an agent may not install this)", ""), True),  # .NET is never authorizable
    ("For you to run: winget install --id Microsoft.OpenJDK.21 --exact --version 21.0.12.8 --no-upgrade", True),
    ("For you to run: winget install --id Python.Python.3.7 --exact --version 3.7.9 --no-upgrade", True),  # < floor
    ("For you to run: winget install --id OpenJS.NodeJS.Current --exact --version 25.0.0 --no-upgrade", True),
    ("For you to run: winget upgrade --id OpenJS.NodeJS.LTS --exact --version 24.19.0", True),
    ("For you to run: winget add --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade", True),   # alias
    ("For you to run: winget.exe install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade", True),
    ("choco install nodejs-lts --version 24.19.0", True),
    ("msiexec /i node-v24.19.0-x64.msi", True),
])
def test_the_scan_can_fail(line, bad):
    assert bool(problems("README.md", "intro\n" + line + "\n")) is bad, problems("README.md", line)


def test_a_comment_is_not_a_label():
    """The user never sees a comment, so `# for you to run` cannot be the label a hint needs."""
    cmd = 'echo "      winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade"'
    assert problems("setup.sh", "echo 'For you to run:'\n" + cmd + "\n") == []
    assert problems("setup.sh", "# for you to run\n" + cmd + "\n") != []
    md = "`winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade`"
    assert problems("README.md", "<!-- for you to run -->\n" + md + "\n") != []


def test_a_label_two_lines_up_counts_and_three_does_not():
    cmd = "      winget install --id OpenJS.NodeJS.LTS --exact --version 24.19.0 --no-upgrade"
    assert problems("setup.sh", "echo 'For you to run:'\necho x\n" + cmd + "\n") == []
    assert problems("setup.sh", "echo 'For you to run:'\necho x\necho y\n" + cmd + "\n") != []


def test_the_allowlist_is_the_parsers_constant():
    inv = _inv()
    assert allowlisted("OpenJS.NodeJS") and allowlisted("OpenJS.NodeJS.LTS") and allowlisted("Git.Git")
    assert allowlisted("Python.Python.3.12") and not allowlisted("Microsoft.DotNet.SDK.10")
    assert sorted(i for ids in inv.ALLOWED_IDS.values() for i in ids) == sorted(
        ["Python.Python.3.<minor>", "Git.Git", "OpenJS.NodeJS", "OpenJS.NodeJS.LTS"]), (
        "ALLOWED_IDS changed: re-check every hint this scan judges (and SETUP_PROMPT.txt's authorization form)")
