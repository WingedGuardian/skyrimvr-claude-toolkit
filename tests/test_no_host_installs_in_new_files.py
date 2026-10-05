"""Tripwire: what the adapter kit adds must not install host software.

The kit's rule is that an agent never installs, upgrades or repairs Python, Git or Node unless the user's
task says so in so many words. The one sanctioned install spelling is the block marked `<!-- install-form -->`
in the `toolkit-setup` skill (a fenced command the agent may run only under an explicit authorization).
Anywhere else, a package-manager install in these files is a regression of that rule.

Deliberately NOT scanned: `setup.sh` (it is a separate rule's business, and any Node auto-install it still
has is tracked as deferred work).
"""
from __future__ import annotations

import re

from conftest import REPO

SCANNED = [
    "agent/skills/toolkit-setup/SKILL.md",
    ".claude/skills/toolkit-setup/SKILL.md",
    ".agents/skills/toolkit-setup/SKILL.md",
    "tools/inventory-runtimes.py",
    "tools/adapting/adapter_template.py",
    "tools/adapter_conformance.py",
]

# winget/choco/brew/apt/apt-get/pip(3) install, also python -m pip install and the quoted-argv spelling.
INSTALL_RE = re.compile(
    r"(?:\bwinget\b|\bchoco\b|\bchocolatey\b|\bbrew\b|\bapt(?:-get)?\b|\bpip3?\b|-m\s+pip)"
    r"[\"',\s]+(?:install|upgrade)\b", re.I)
SANCTIONED_OPEN = "<!-- install-form -->"


def strip_sanctioned(text: str) -> str:
    """Remove the one install-form block: the marker line and the fenced block right after it."""
    return re.sub(re.escape(SANCTIONED_OPEN) + r"\s*```[^\n]*\n.*?\n```", "", text, count=1, flags=re.S)


def strip_printed_command(text: str) -> str:
    """tools/inventory-runtimes.py PRINTS the one install command shape in install_command() and never runs it
    (test_the_inventory_tool_never_runs_what_it_prints holds it to that). Exempt only that function."""
    return re.sub(r"def install_command\(.*?(?=\n\n\ndef |\Z)", "", text, count=1, flags=re.S)


def offenders(text: str):
    return [m.group(0) for m in INSTALL_RE.finditer(strip_printed_command(strip_sanctioned(text)))]


def test_the_new_files_install_nothing():
    found = {}
    for rel in SCANNED:
        hits = offenders((REPO / rel).read_text(encoding="utf-8"))
        if hits:
            found[rel] = hits
    assert not found, f"host-install commands outside the skill's install-form block: {found}"


def test_the_tripwire_can_fail_on_pip_install():
    assert offenders("run `pip install x` first") == ["pip install"]


def test_the_tripwire_can_fail_on_each_manager():
    for cmd in ("winget install foo", "choco install foo", "apt install foo", "apt-get install foo",
                "brew install foo", "python -m pip install foo", 'subprocess.run(["winget", "install"])'):
        assert offenders(cmd), cmd


def test_the_tripwire_flags_a_copy_of_the_real_skill_with_pip_added():
    text = (REPO / "agent/skills/toolkit-setup/SKILL.md").read_text(encoding="utf-8")
    assert offenders(text) == [], "the pristine skill must be clean"
    assert offenders(text + "\nthen pip install x\n") == ["pip install"]


def test_the_sanctioned_block_is_the_only_exemption():
    """The skill really has an install-form block, and stripping it is what makes the skill clean."""
    text = (REPO / "agent/skills/toolkit-setup/SKILL.md").read_text(encoding="utf-8")
    assert SANCTIONED_OPEN in text
    assert INSTALL_RE.search(text), "the block holds a winget install: the exemption is doing real work"
    assert not INSTALL_RE.search(strip_sanctioned(text))


def test_a_second_install_block_is_not_exempt():
    text = (REPO / "agent/skills/toolkit-setup/SKILL.md").read_text(encoding="utf-8")
    assert offenders(text + "\n```\nwinget install --id X\n```\n") == ["winget install"]


def test_the_inventory_tool_never_runs_what_it_prints():
    """The exemption above is only honest while install_command() is never executed: the tool's one
    subprocess.run is the --version launch, and install_command is only ever called to build a plan entry."""
    text = (REPO / "tools/inventory-runtimes.py").read_text(encoding="utf-8")
    assert len(re.findall(r"subprocess\.run\(", text)) == 1
    assert "os.system" not in text and "Popen" not in text
    run_at = text.index("subprocess.run(")
    assert text.rfind("def ", 0, run_at) == text.index("def launch(")


def test_the_printed_command_exemption_is_narrow():
    text = (REPO / "tools/inventory-runtimes.py").read_text(encoding="utf-8")
    assert offenders(text) == []
    assert offenders(text + '\nsubprocess.run(["winget", "install", "x"])\n') != []
