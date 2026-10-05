"""The capability report (ADAPTING.md section 3): its status is DERIVED from measured leaves, never declared."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys

import pytest

from conftest import REPO

TEMPLATE = REPO / "tools" / "adapting" / "capability-report.template.json"
TOOL = REPO / "tools" / "adapter_conformance.py"
FULL = "locally adapted, self-verified"
PARTIAL = "partially supported: Layers 0-2"


def _conf():
    spec = importlib.util.spec_from_file_location("adapter_conformance", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


conf = _conf()


def d(value, tier="MEASURED", evidence="tests/probe.log"):
    return {"value": value, "tier": tier, "evidence": evidence}


def measured_report() -> dict:
    return {
        "v": 1,
        "agent": {"name": "toy", "version": "1.0", "surface": "cli"},
        "instruction_file": {"name": d("TOY.md"), "limit_bytes": d(32768), "truncation_behaviour": d("silent")},
        "skills_dir": {"path": d(".toy/skills")},
        "pre_tool_hook": {
            "exists": d(True), "fires_on_this_surface": d(True), "can_block": d(True),
            "block_mechanism": d("exit 2"), "can_ask": d(False), "failed_hook_behaviour": d("allows"),
            "hook_timeout_s": d(30),
        },
        "session_start": {"exists": d(False), "injects_context": d(False)},
        "payload_capture": {"method": d("tee to a file"), "files": ["a.json"]},
        "proof": {"conformance_passed": True, "live_canary_blocked": True, "canary_evidence": "canary.log line 3"},
    }


def run_cli(tmp_path, report):
    p = tmp_path / "report.json"
    p.write_text(json.dumps(report), encoding="utf-8")
    return subprocess.run([sys.executable, str(TOOL), "report", str(p)], capture_output=True, timeout=60)


def test_a_fully_measured_report_derives_locally_adapted():
    r = measured_report()
    assert conf.derive_status(r) == FULL
    assert conf.validate_report(r) == []


def test_failed_hook_behaviour_blocks_also_counts_when_measured():
    r = measured_report()
    r["pre_tool_hook"]["failed_hook_behaviour"] = d("blocks")
    assert conf.derive_status(r) == FULL


@pytest.mark.parametrize("path,tier", [
    (("pre_tool_hook", "can_block"), "INFERRED"),
    (("pre_tool_hook", "fires_on_this_surface"), "ASSUMED"),
    (("pre_tool_hook", "failed_hook_behaviour"), "READ"),
])
def test_a_gating_leaf_that_is_not_measured_demotes_the_status(path, tier):
    r = measured_report()
    r[path[0]][path[1]]["tier"] = tier
    assert conf.derive_status(r) == PARTIAL


@pytest.mark.parametrize("path,value", [
    (("pre_tool_hook", "can_block"), False),
    (("pre_tool_hook", "fires_on_this_surface"), False),
    (("pre_tool_hook", "failed_hook_behaviour"), "unknown"),
])
def test_a_gating_leaf_with_the_wrong_value_demotes_the_status(path, value):
    r = measured_report()
    r[path[0]][path[1]]["value"] = value
    assert conf.derive_status(r) == PARTIAL


@pytest.mark.parametrize("key,value", [
    ("live_canary_blocked", False), ("conformance_passed", False), ("canary_evidence", ""),
    ("canary_evidence", "   "), ("live_canary_blocked", "yes"),
])
def test_the_proof_block_must_be_true_and_evidenced(key, value):
    r = measured_report()
    r["proof"][key] = value
    assert conf.derive_status(r) == PARTIAL


def test_the_unfilled_template_is_partially_supported_and_not_an_error(tmp_path):
    t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert conf.derive_status(t) == PARTIAL
    assert conf.validate_report(t) == []
    cp = run_cli(tmp_path, t)
    assert cp.returncode == 0 and PARTIAL in cp.stdout.decode()


def test_a_claim_stronger_than_the_derived_status_fails_naming_claimed_status(tmp_path):
    r = measured_report()
    r["proof"]["live_canary_blocked"] = False
    r["claimed_status"] = FULL
    probs = conf.validate_report(r)
    assert any("claimed_status" in p for p in probs)
    cp = run_cli(tmp_path, r)
    assert cp.returncode == 1 and b"claimed_status" in cp.stdout + cp.stderr
    assert PARTIAL in cp.stdout.decode()     # the derived status is still printed first


def test_a_claim_equal_to_or_weaker_than_the_derived_status_is_fine():
    r = measured_report()
    r["claimed_status"] = FULL
    assert conf.validate_report(r) == []
    r["claimed_status"] = PARTIAL
    assert conf.validate_report(r) == []
    t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    t["claimed_status"] = PARTIAL
    assert conf.validate_report(t) == []


@pytest.mark.parametrize("claim", ["supported", "Locally Adapted", "", None, 5])
def test_a_claim_outside_the_vocabulary_fails(claim):
    r = measured_report()
    r["claimed_status"] = claim
    assert any("claimed_status" in p for p in conf.validate_report(r))


def test_a_strong_claim_over_an_inferred_gating_leaf_names_that_leaf():
    r = measured_report()
    r["pre_tool_hook"]["can_block"]["tier"] = "INFERRED"
    r["claimed_status"] = FULL
    probs = conf.validate_report(r)
    assert any("pre_tool_hook.can_block" in p and "INFERRED" in p for p in probs)
    assert any("claimed_status" in p for p in probs)


def test_an_inferred_gating_leaf_without_a_claim_is_just_partial():
    r = measured_report()
    r["pre_tool_hook"]["can_block"]["tier"] = "INFERRED"
    assert conf.validate_report(r) == []


@pytest.mark.parametrize("leaf", conf.LEAVES)
def test_a_missing_leaf_fails_naming_the_path(tmp_path, leaf):
    r = measured_report()
    node = r
    parts = leaf.split(".")
    for p in parts[:-1]:
        node = node[p]
    del node[parts[-1]]
    probs = conf.validate_report(r)
    assert any(leaf in p for p in probs), probs
    cp = run_cli(tmp_path, r)
    assert cp.returncode == 1 and leaf.encode() in cp.stdout + cp.stderr


def test_a_missing_section_names_every_leaf_inside_it():
    r = measured_report()
    del r["pre_tool_hook"]
    probs = " ".join(conf.validate_report(r))
    for leaf in conf.LEAVES:
        if leaf.startswith("pre_tool_hook."):
            assert leaf in probs


def test_a_tier_outside_the_enum_fails():
    r = measured_report()
    r["pre_tool_hook"]["can_block"]["tier"] = "PROBABLY"
    assert any("pre_tool_hook.can_block" in p and "tier" in p for p in conf.validate_report(r))
    r = measured_report()
    r["pre_tool_hook"]["can_block"]["tier"] = "measured"      # case matters
    assert any("pre_tool_hook.can_block" in p for p in conf.validate_report(r))


def test_a_filled_leaf_must_be_wrapped_with_evidence():
    r = measured_report()
    r["pre_tool_hook"]["can_block"] = True
    assert any("pre_tool_hook.can_block" in p for p in conf.validate_report(r))
    r = measured_report()
    r["pre_tool_hook"]["can_block"]["evidence"] = ""
    assert any("pre_tool_hook.can_block" in p and "evidence" in p for p in conf.validate_report(r))
    r = measured_report()
    del r["pre_tool_hook"]["can_block"]["tier"]
    assert any("pre_tool_hook.can_block" in p for p in conf.validate_report(r))


def test_an_unknown_failed_hook_behaviour_value_fails():
    r = measured_report()
    r["pre_tool_hook"]["failed_hook_behaviour"]["value"] = "explodes"
    assert any("failed_hook_behaviour" in p for p in conf.validate_report(r))


def test_wrong_version_and_non_object_fail():
    r = measured_report()
    r["v"] = 2
    assert any(p.startswith("v") or " v " in p or "'v'" in p for p in conf.validate_report(r))
    assert conf.validate_report([]) and conf.validate_report("x")


def test_bool_typed_proof_fields_must_be_booleans():
    r = measured_report()
    r["proof"]["conformance_passed"] = "true"
    assert any("proof.conformance_passed" in p for p in conf.validate_report(r))


def test_cli_rejects_unparseable_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    cp = subprocess.run([sys.executable, str(TOOL), "report", str(p)], capture_output=True, timeout=60)
    assert cp.returncode == 1


def test_cli_prints_the_derived_status_for_a_clean_full_report(tmp_path):
    cp = run_cli(tmp_path, measured_report())
    assert cp.returncode == 0 and cp.stdout.decode().splitlines()[0] == FULL


@pytest.mark.parametrize("leaf", ["fires_on_this_surface", "can_block", "failed_hook_behaviour"])
@pytest.mark.parametrize("empty", ["", "   ", None])
def test_a_gating_leaf_with_no_evidence_does_not_count(leaf, empty):
    r = measured_report()
    r["pre_tool_hook"][leaf]["evidence"] = empty
    assert conf.derive_status(r) == PARTIAL


def test_the_cli_never_prints_the_strong_status_over_a_problem(tmp_path):
    r = measured_report()
    r["status"] = "supported"           # derive alone would say FULL; the problem must stop that line
    cp = run_cli(tmp_path, r)
    assert cp.returncode == 1
    assert cp.stdout.decode().splitlines()[0] == PARTIAL


@pytest.mark.parametrize("claim", [[], {}, ["x"], 3.5, True])
def test_an_unhashable_or_non_string_claim_is_a_problem_not_a_traceback(tmp_path, claim):
    r = measured_report()
    r["claimed_status"] = claim
    assert any("claimed_status" in p for p in conf.validate_report(r))
    cp = run_cli(tmp_path, r)
    assert cp.returncode == 1 and b"Traceback" not in cp.stderr


def test_a_top_level_status_key_is_rejected():
    r = measured_report()
    r["status"] = "supported"
    assert any("status" in p and "unknown" in p for p in conf.validate_report(r))


def test_unknown_nested_keys_are_rejected():
    r = measured_report()
    r["pre_tool_hook"]["can_blok"] = d(True)
    assert any("pre_tool_hook.can_blok" in p for p in conf.validate_report(r))
    r = measured_report()
    r["pre_tool_hook"]["can_block"]["confidence"] = 99
    assert any("pre_tool_hook.can_block" in p and "confidence" in p for p in conf.validate_report(r))


def test_a_hook_that_does_not_exist_cannot_fire():
    r = measured_report()
    r["pre_tool_hook"]["exists"] = d(False)
    probs = conf.validate_report(r)
    assert any("pre_tool_hook.exists" in p and "fires_on_this_surface" in p for p in probs)
    r = measured_report()
    r["pre_tool_hook"]["exists"] = d(True)
    r["pre_tool_hook"]["fires_on_this_surface"] = d(False)
    assert conf.validate_report(r) == []


def _flatten(t):
    out = []
    for k, v in t.items():
        if isinstance(v, dict) and k != "claimed_status":
            out.extend(k + "." + c for c in v)
        else:
            out.append(k)
    return out


def test_the_template_has_every_leaf_the_validator_requires():
    t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert sorted(_flatten(t)) == sorted(conf.LEAVES)
    assert "claimed_status" not in t


def test_the_status_strings_are_produced_in_one_place():
    src = TOOL.read_text(encoding="utf-8")
    assert src.count(FULL) == 1 and src.count(PARTIAL) == 1


def test_this_module_does_not_mutate_the_template_between_runs():
    a = copy.deepcopy(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    conf.validate_report(a)
    assert a == json.loads(TEMPLATE.read_text(encoding="utf-8"))
