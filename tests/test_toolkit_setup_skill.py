"""The toolkit-setup skill: it must stay a look-first runbook that can only ever print the one install form."""

from __future__ import annotations

import importlib.util
import re

from conftest import REPO

SKILL = REPO / "agent" / "skills" / "toolkit-setup" / "SKILL.md"
MARKER = "<!-- install-form -->"
FORM_RE = re.compile(r"^winget install --id <Id> --exact --version <V> --no-upgrade")
TRIPWIRES = [
    r"\bupgrade\b", r"\brepair\b", r"\breinstall\b", r"\buninstall\b", r"--force\b", r"--silent\b",
    r"\bchoco\b", r"\bscoop\b", r"\bapt\b", r"\bbrew\b", r"\bsudo\b", r"Install-Package", r"\biex\b",
    r"\bcurl\b", r"Invoke-WebRequest",
]


def _tool():
    spec = importlib.util.spec_from_file_location("inventory_runtimes", REPO / "tools" / "inventory-runtimes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _blocks(text):
    """[(preceding_text, body)] for every fenced block."""
    out = []
    for m in re.finditer(r"```[^\n]*\n(.*?)```", text, re.S):
        out.append((text[:m.start()], m.group(1)))
    return out


def problems(text):
    """Everything wrong with a skill text; [] means it passes."""
    found = []
    if not text.startswith("---"):
        found.append("no frontmatter")
    if not re.search(r"^name: toolkit-setup$", text, re.M):
        found.append("name is not toolkit-setup")
    if not re.search(r"^description: \S", text, re.M):
        found.append("no description")
    if MARKER not in text:
        found.append("install-form marker missing")
    for before, body in _blocks(text):
        scan = body.replace("--no-upgrade", "")
        for pat in TRIPWIRES:
            if re.search(pat, scan, re.I):
                found.append("fenced block contains %s" % pat)
        if "winget install" in body:
            if not before.rstrip().endswith(MARKER):
                found.append("winget install outside the install-form block")
            elif not FORM_RE.match(body.strip()):
                found.append("install-form block is not the placeholder form")
    return found


def test_skill_is_clean():
    assert problems(SKILL.read_text(encoding="utf-8")) == []


def test_skill_names_the_tool_and_it_exists():
    text = SKILL.read_text(encoding="utf-8")
    assert "tools/inventory-runtimes.py" in text
    assert (REPO / "tools" / "inventory-runtimes.py").is_file()


def test_install_form_equals_the_tool_output():
    text = SKILL.read_text(encoding="utf-8")
    block = _blocks(text)[[b[0].rstrip().endswith(MARKER) for b in _blocks(text)].index(True)][1].strip()
    shape = block.replace("<Id>", "Some.Id").replace("<V>", "1.2.3")
    assert shape == " ".join(_tool().install_command("Some.Id", "1.2.3"))


def test_skill_item_form_matches_the_parser_and_cites_no_spec():
    text = SKILL.read_text(encoding="utf-8")
    assert "`python=<winget id>==<exact version>`" in text
    assert "python|git|node =" not in text
    assert not re.search(r"\bE\d+\b", text) and "spec evidence" not in text
    tool = _tool()
    assert tool.parse_authorization("Host-software installs: AUTHORIZED python=Some.Id==1.2.3.")[0] == {
        "python": ("Some.Id", "1.2.3")}


def test_setup_sh_has_no_unattended_install_path():
    """setup.sh may ECHO install hints; it must never EXECUTE an installer (the skill relies on this)."""
    runners = re.compile(r"\b(winget|choco|scoop|apt|apt-get|brew|pip|pip3|npm)\b")
    bad = []
    for n, line in enumerate((REPO / "setup.sh").read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("echo") or s.startswith("printf"):
            continue
        if runners.search(s):
            bad.append("%d: %s" % (n, s))
    assert bad == [], bad


def test_setup_sh_scan_can_fail(tmp_path):
    src = (REPO / "setup.sh").read_text(encoding="utf-8") + "\nwinget install OpenJS.NodeJS.LTS\n"
    runners = re.compile(r"\b(winget|choco|scoop|apt|apt-get|brew|pip|pip3|npm)\b")
    hit = [l for l in src.splitlines() if l.strip() and not l.strip().startswith(("#", "echo", "printf"))
           and runners.search(l)]
    assert hit == ["winget install OpenJS.NodeJS.LTS"]


def test_tripwire_can_fail():
    good = SKILL.read_text(encoding="utf-8")
    bad_block = good + "\n```text\nwinget upgrade --all\n```\n"
    assert problems(bad_block), "an upgrade in a fenced block was not flagged"
    assert problems(good.replace(MARKER, "")), "a skill without the marker was not flagged"
    stray = good + "\n```text\nwinget install --id X --exact --version 1.0\n```\n"
    assert problems(stray), "a second winget install block was not flagged"
    assert problems(good.replace("--no-upgrade", "--force")), "a drifted install form was not flagged"
