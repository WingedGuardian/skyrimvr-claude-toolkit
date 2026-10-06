"""The universal setup prompt: agent-neutral, routed through toolkit-setup, and it authorizes no host installs."""

from __future__ import annotations

import importlib.util
import re

from conftest import REPO
from test_repo_invariants import test_setup_prompt_is_identical_everywhere  # noqa: F401  (the existing gate, run here too)

AUTH_NO = "Host-software installs: NOT AUTHORIZED."
AUTH_YES = "Host-software installs: AUTHORIZED python=Python.Python.3.12==3.12.8."
OLD_IMPERATIVE = re.compile(r"(?i)\binstall (any|the) missing|\binstall (python|node|git)")
# The first sentences of the prompt that shipped before Plan 7, verbatim: the detectors must flag THIS.
OLD_HEAD = ('I just installed the Skyrim Claude Code Modding Toolkit into this folder. Run "bash setup.sh" to set '
            'everything up. Install any missing prerequisites (Python 3, Node.js) for me.')


def _prompt():
    return (REPO / "SETUP_PROMPT.txt").read_text(encoding="utf-8").strip()


def _parse(text):
    spec = importlib.util.spec_from_file_location("inventory_runtimes", REPO / "tools" / "inventory-runtimes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_authorization(text)


def test_prompt_carries_the_required_pieces():
    p = _prompt()
    for needle in (AUTH_NO, "toolkit-setup", "ADAPTING.md", "AGENTS.md"):
        assert needle in p, needle


def test_prompt_is_one_line():
    assert "\n" not in _prompt()


def test_prompt_does_not_tell_the_agent_to_install_host_runtimes():
    p = _prompt()
    assert "Install any missing prerequisites" not in p
    assert "winget install" not in p
    assert not OLD_IMPERATIVE.search(p), OLD_IMPERATIVE.search(p)


def test_system_software_is_never_installed_and_tools_stay_inside_the_folder():
    p = _prompt()
    assert "any other system software (a JDK, .NET, Blender)" in p
    assert "never install it yourself" in p  # the JDK sentence in the ReSaver entry
    assert "never covers a JDK, .NET or Blender" in p
    assert "only inside this folder" in p and "tools/" in p
    assert "e.g. the Microsoft.OpenJDK" not in p


def test_the_shipped_prompt_authorizes_nothing():
    assert _parse(_prompt()) == ({}, [])


def test_the_detectors_can_fail():
    """Swapping the sentence authorizes; the pre-Plan-7 wording is caught by the does-not-contain assertions."""
    authorized, rejected = _parse(_prompt().replace(AUTH_NO, AUTH_YES))
    assert authorized == {"python": ("Python.Python.3.12", "3.12.8")} and rejected == []
    assert OLD_IMPERATIVE.search(OLD_HEAD)
    assert "Install any missing prerequisites" in OLD_HEAD


def test_kept_tail_behaviours():
    """Everything the old prompt did besides the install sentence is still there."""
    p = _prompt()
    for tool in ("xeditlib", "Champollion", "Caprica", "Spriggit", "AutoMod CLI", "PyFFI", "PyNifly", "Blender",
                 "NifSkope", "ReSaver CLI"):
        assert tool in p, tool
    assert "tools/.nexus_api_key" in p and "Personal API Key" in p        # Nexus key
    assert "tailor the environment specifically to my Skyrim version" in p  # tailoring
    assert "tools/cosave-cli.sh" in p
    assert "Explain everything in plain English" in p


def test_devbench_is_told_and_installed_only_by_the_mod_manager():
    p = _prompt()
    assert "TELL me about DevBench but do NOT install it" in p
    assert "through my own mod manager" in p
    assert "never copy files into Data/ yourself" in p


def _after_prompt(rel, chars=900):
    text = (REPO / rel).read_text(encoding="utf-8")
    i = text.index(_prompt()) + len(_prompt())
    return text[i:i + chars]


def test_the_prose_after_each_embedded_prompt_does_not_contradict_it():
    """Final review M3: README said, right under a prompt that forbids host installs, that the agent 'will
    configure paths, install dependencies'; getting-started said 'Claude will: ... Check Python 3'."""
    for rel in ("README.md", "docs/getting-started.md"):
        tail = " ".join(_after_prompt(rel).split())
        assert "install dependencies" not in tail, rel
        assert not re.search(r"\bClaude (will|handles)\b", tail), rel
        assert "does not install" in tail or "will not install" in tail, rel


def test_product_is_not_named():
    """The rename is a later plan's; the prompt names no product."""
    assert "Claude Code Modding Toolkit" not in _prompt()


# Plan 8 T6 (Plan 7 minor, deferred row 49): the authorization form names the exact ids the parser accepts.
def _allowlist_clause():
    spec = importlib.util.spec_from_file_location("inventory_runtimes", REPO / "tools" / "inventory-runtimes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    floor = " ({}.{} or newer)".format(*mod.PYTHON_FLOOR)
    parts = ["{}{} for {}".format(" or ".join(ids), floor if rt == "python" else "", rt)
             for rt, ids in mod.ALLOWED_IDS.items()]
    return "The only winget ids it can name are " + ", ".join(parts[:-1]) + ", and " + parts[-1] + ","


def test_the_authorization_form_lists_exactly_the_parsers_allowlist():
    """The prompt's list is the parser's ALLOWED_IDS, rendered: an id added to either side alone goes red."""
    p = _prompt()
    assert _allowlist_clause() in p, _allowlist_clause()
    assert p.count("The only winget ids it can name are ") == 1
    assert "each at the exact version I write, installed with --no-upgrade" in p
    assert "for those, and for anything else, you only give me manual steps" in p


def test_the_python_floor_is_stated_the_same_everywhere():
    """Review M2: the floor the parser enforces is the one the skill and ADAPTING.md state (3.10: the tested range)."""
    spec = importlib.util.spec_from_file_location("inventory_runtimes", REPO / "tools" / "inventory-runtimes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    floor = "{}.{}".format(*mod.PYTHON_FLOOR)
    assert floor == "3.10"
    skill = (REPO / "agent" / "skills" / "toolkit-setup" / "SKILL.md").read_text(encoding="utf-8")
    assert "for `python` ({} or newer,".format(floor) in skill
    assert "means a Python {} or newer interpreter".format(floor) in (REPO / "ADAPTING.md").read_text(encoding="utf-8")
    assert "Python.Python.3.<minor> ({} or newer) for python".format(floor) in _prompt()
