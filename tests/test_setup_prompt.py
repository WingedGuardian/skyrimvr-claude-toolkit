"""The universal setup prompt: agent-neutral, routed through toolkit-setup, and it authorizes no host installs."""

from __future__ import annotations

import importlib.util
import re

from conftest import REPO
from test_repo_invariants import test_setup_prompt_is_identical_everywhere  # noqa: F401  (the existing gate, run here too)

AUTH_NO = "Host-software installs: NOT AUTHORIZED."
AUTH_YES = "Host-software installs: AUTHORIZED python=Python.Python.3.12==3.12.8."
OLD_IMPERATIVE = re.compile(r"(?i)\binstall (any|the) missing|\binstall (python|node|git)")


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


def test_the_shipped_prompt_authorizes_nothing():
    assert _parse(_prompt()) == ({}, [])


def test_the_test_can_fail():
    """Swapping the sentence authorizes; the old imperative is caught by the does-not-contain assertions."""
    authorized, rejected = _parse(_prompt().replace(AUTH_NO, AUTH_YES))
    assert authorized == {"python": ("Python.Python.3.12", "3.12.8")} and rejected == []
    reintroduced = _prompt() + " Install any missing prerequisites (Python 3, Node.js) for me."
    assert "Install any missing prerequisites" in reintroduced
    assert OLD_IMPERATIVE.search(reintroduced)


def test_devbench_is_told_not_installed():
    p = _prompt()
    assert "DevBench" in p and "do NOT install it" in p


def test_product_is_not_named():
    """The rename is a later plan's; the prompt names no product."""
    assert "Claude Code Modding Toolkit" not in _prompt()
