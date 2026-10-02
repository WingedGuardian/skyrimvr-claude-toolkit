"""The instruction budget on the REAL tree, and the install-expansion reserve against the REAL setup.sh."""
from __future__ import annotations

import importlib.util

from conftest import REPO, run_setup


def _gen():
    spec = importlib.util.spec_from_file_location("gen_agent_trees", REPO / "scripts" / "gen_agent_trees.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_instructions_fit_the_budget():
    g = _gen()
    sizes = g.instruction_sizes(REPO / "agent")
    assert sizes, "no instruction files measured -- the gate would be vacuous"
    assert not g.budget_problems(REPO / "agent"), sizes


def test_install_expansion_fits_its_reserve(portable_mo2):
    """setup.sh's path fill + MO2 block is what an installed CLAUDE.md/AGENTS.md carries beyond the repo."""
    g = _gen()
    game, _inst, _mods = portable_mo2
    before = len((game / "CLAUDE.md").read_bytes())
    r = run_setup(game)
    assert r.returncode == 0, r.stdout + r.stderr
    after = len((game / "CLAUDE.md").read_bytes())
    assert after > before, "setup.sh expanded nothing -- the fixture did not exercise the MO2 block"
    assert after - before <= g.INSTALL_EXPANSION_RESERVE, (after - before, g.INSTALL_EXPANSION_RESERVE)
