"""The Codex .rules file (Plan 4 Task 9; R5).

A prefix rule is only safe while it is a strict subset of a hook deny: .rules `forbidden` blocks even when
the user never approved the hooks, so a prefix broader than its hook would refuse work the hooks allow.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, codes, codex_rules, engine, paths  # noqa: E402
from skyguard.adapters import codex  # noqa: E402
from skyguard.request import Request  # noqa: E402

GAME = "C:/SkyguardRules/Skyrim Probe"
BUCKETS = {"forbidden", "prompt", "hook_only"}


def _verdict_codes():
    """Every deny/ask code the engine can emit: the rule-module constants AND (Plan 5) the rule registry, whose
    messages most denies now render straight from."""
    return codes.decisive_codes()


def _evaluate(tokens, tmp_path, shell="powershell"):
    """The argv as Codex hands it over: one text, each token quoted for the dialect (Codex on Windows runs
    PowerShell; the bash reading is checked too, so a prefix cannot be a subset in one dialect only)."""
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})
    req = Request(kind="shell", shell=shell, command=codex._argv_text(list(tokens), shell))
    return engine.evaluate_shell(req, r, audit.Audit(str(tmp_path)))


def test_every_shell_verdict_is_classified():
    codes = _verdict_codes()
    assert codes, "found no DENY_/ASK_/NO_ constants -- the scan itself is broken"
    assert [c for c in codes if c not in codex_rules.CLASSIFICATION] == []
    assert [c for c in codex_rules.CLASSIFICATION if c not in codes] == [], "classified code that no longer exists"


def test_buckets_sum():
    cls = codex_rules.CLASSIFICATION
    assert all(b in BUCKETS for b, _ in cls.values())
    counts = {b: sum(1 for x, _ in cls.values() if x == b) for b in BUCKETS}
    assert sum(counts.values()) == len(cls)
    with_rules = {r["code"] for r in codex_rules.PREFIX_RULES}
    wants_rule = {c for c, (b, _) in cls.items() if b in ("forbidden", "prompt")}
    assert with_rules == wants_rule
    assert all(cls[r["code"]][0] == r["decision"] for r in codex_rules.PREFIX_RULES)


@pytest.mark.parametrize("rule", codex_rules.PREFIX_RULES, ids=lambda r: " ".join(r["pattern"]))
def test_every_prefix_rule_is_a_subset_of_its_hook_deny(rule, tmp_path):
    assert rule["match"] and rule["not_match"]
    for shell in ("powershell", "bash"):
        for tokens in rule["match"]:
            assert tokens[:len(rule["pattern"])] == rule["pattern"], "match example outside its own pattern"
            v = _evaluate(tokens, tmp_path, shell)
            assert (v.decision, v.code) == ("deny", rule["code"]), (shell, tokens)
        for tokens in rule["not_match"]:
            assert tokens[:len(rule["pattern"])] != rule["pattern"], "not_match example inside its own pattern"
            assert _evaluate(tokens, tmp_path, shell).decision != "deny", (shell, tokens)


def test_no_prefix_refuses_a_registry_delete_the_hook_allows(tmp_path):
    """Merge of Plans 4+5 (R5): Plan 5 narrowed the registry deny to Bethesda keys, so `reg delete <other key>` is
    ALLOWED by the hook. A .rules prefix applies even after the hooks are approved, so no prefix may cover it."""
    for prog in ("reg", "reg.exe"):
        tokens = [prog, "delete", "HKCU" + chr(92) + "Software" + chr(92) + "SkyguardProbe", "/f"]
        assert _evaluate(tokens, tmp_path).decision == "allow", tokens
        assert not [r["pattern"] for r in codex_rules.PREFIX_RULES if tokens[:len(r["pattern"])] == r["pattern"]]


def test_render_shape():
    text = codex_rules.render()
    assert text.count("prefix_rule(") == len(codex_rules.PREFIX_RULES)
    assert 'pattern = ["reg", "delete", "HKLM\\\\SOFTWARE\\\\Bethesda Softworks"],' in text
    assert 'pattern = ["reg.exe", "delete", "HKLM\\\\SOFTWARE\\\\Bethesda Softworks"],' in text
    assert 'pattern = ["reg", "delete"],' not in text and 'pattern = ["reg.exe", "delete"],' not in text
    assert text.count('decision = "forbidden"') == len(codex_rules.PREFIX_RULES) == 12
    assert text.count("    match = ") == len(codex_rules.PREFIX_RULES)
    assert text.count("    not_match = ") == len(codex_rules.PREFIX_RULES)
    assert "\\\\Software" in text   # backslashes JSON-escaped, not raw


CODEX = shutil.which("codex")


def _check(rules, tokens):
    p = subprocess.run([CODEX, "execpolicy", "check", "--rules", str(rules), *tokens],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


@pytest.mark.skipif(CODEX is None, reason="codex not on PATH: cannot run `codex execpolicy check`")
def test_rules_file_passes_codex_execpolicy(tmp_path):
    rules = tmp_path / "skyrim.rules"
    rules.write_text(codex_rules.render(), encoding="utf-8", newline="\n")
    for r in codex_rules.PREFIX_RULES:
        for tokens in r["match"]:
            assert r["decision"] in _check(rules, tokens), tokens
        for tokens in r["not_match"]:
            out = _check(rules, tokens)
            assert r["decision"] not in out and "matchedRules" not in out.replace('"matchedRules":[]', ""), out
