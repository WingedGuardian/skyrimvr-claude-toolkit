"""ADAPTING.md may not say more than the code and the measurements say.

Two kinds of check. (1) The FACTS regions: every table row and bullet in them carries a citation that must
resolve ([src: file::needle], [repo: file::needle], [measured: file#heading], [spec: ID]). (2) The CONTRACT
and GUIDANCE outside those regions state what the toolkit does, so they are compared with the constants of
the code they describe (field names, decisions, deadlines, fault names, status words): if the code moves, the
document goes red instead of lying.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, neutral  # noqa: E402
from skyguard.verdict import DECISIONS  # noqa: E402

DOC = REPO / "ADAPTING.md"
SPEC = REPO / "docs" / "evidence" / "2026-09-30-universal-agent-support-design.md"
CITE = re.compile(r"\[(src|repo|spec|measured): ([^\]]+)\]")
OTHER_AGENTS = re.compile(r"\b(OpenCode|Cursor|Gemini|Aider|Cline|Copilot|Windsurf|Zed|Continue)\b")
UNMEASURED = re.compile(r"not measured|unmeasured", re.I)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


conf = _load("adapter_conformance_for_claims", "tools/adapter_conformance.py")
tpl = _load("adapter_template_for_claims", "tools/adapting/adapter_template.py")


def facts(text: str):
    regions = re.findall(r"<!-- facts:begin -->(.*?)<!-- facts:end -->", text, re.S)
    assert regions, "ADAPTING.md has no facts region"
    lines = "\n".join(regions).splitlines()
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("| ---") or s.startswith("|---"):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if s.startswith("|") and (nxt.startswith("| ---") or nxt.startswith("|---")):
            continue                                   # a table header row
        if s.startswith("|") or s.startswith("- "):
            out.append(s)
    return out


def resolve(kind: str, target: str, repo: Path = REPO):
    """-> None when it resolves, else the problem."""
    if kind == "spec":
        text = SPEC.read_text(encoding="utf-8")
        return None if re.search(r"^\| %s \|" % re.escape(target.strip()), text, re.M) else "no spec row " + target
    sep = "::" if kind in ("src", "repo") else "#"
    path, _, needle = target.partition(sep)
    f = repo / path.strip()
    if not f.is_file():
        return "no file " + path
    return None if (not needle or needle.strip() in f.read_text(encoding="utf-8", errors="replace")) else \
        "{} does not contain {!r}".format(path, needle.strip())


def problems(text: str, repo: Path = REPO):
    out = []
    for line in facts(text):
        cites = CITE.findall(line)
        if not cites:
            out.append("uncited fact: " + line[:80])
        for kind, target in cites:
            p = resolve(kind, target, repo)
            if p:
                out.append("{} [{}: {}]".format(p, kind, target))
    for ln in text.splitlines():
        if OTHER_AGENTS.search(ln) and not UNMEASURED.search(ln):
            out.append("names an agent the toolkit has not measured without saying so: " + ln[:80])
    return out


def section(text: str, n: int) -> str:
    m = re.search(r"^## %d\. .*?(?=^## \d+\. |\Z)" % n, text, re.S | re.M)
    assert m, "ADAPTING.md has no section %d" % n
    return m.group(0)


def ticked(text: str):
    return re.findall(r"`([^`]+)`", text)


# ---- the brief's gate ---------------------------------------------------------------------------------
def test_adapting_md_has_no_unbacked_claim():
    assert problems(DOC.read_text(encoding="utf-8")) == []


def test_every_surface_and_agent_row_cites_the_decision_that_governs_it():
    t = DOC.read_text(encoding="utf-8")
    assert "[spec: D14]" in t and "[spec: D8]" in t


def test_the_status_words_in_the_doc_are_exactly_the_ones_the_validator_derives():
    t = DOC.read_text(encoding="utf-8")
    assert conf.STATUS_PARTIAL == "partially supported: Layers 0-2"
    assert conf.STATUS_FULL == "locally adapted, self-verified"
    for word in (conf.STATUS_PARTIAL, conf.STATUS_FULL):
        assert word in section(t, 0), "section 0 must carry the status word {!r}".format(word)


def test_the_gate_can_fail():
    bad = ("<!-- facts:begin -->\n| Claim | Source |\n| --- | --- |\n| an uncited claim | none |\n"
           "| a bad cite | [spec: E999] [src: tools/skyguard/nope.py::x] |\n<!-- facts:end -->\n"
           "OpenCode blocks writes.\n")
    got = problems(bad)
    assert any("uncited" in g for g in got) and any("E999" in g for g in got)
    assert any("nope.py" in g for g in got) and any("OpenCode" in g for g in got)


def test_the_gate_accepts_a_well_cited_row():
    good = ("<!-- facts:begin -->\n| Claim | Source |\n| --- | --- |\n"
            "| Codex turns ask into deny | [src: tools/skyguard/adapters/codex.py::def render] |\n<!-- facts:end -->\n"
            "OpenCode: not measured.\n")
    assert problems(good) == []


def test_a_needle_that_is_absent_from_a_real_file_is_reported():
    bad = ("<!-- facts:begin -->\n| c |\n| --- |\n| x | [src: tools/skyguard/neutral.py::no such needle zzz] |\n"
           "| y | [measured: docs/evidence/2026-10-05-plan7-adapter-latency.md#no such heading] |\n"
           "<!-- facts:end -->\n")
    got = problems(bad)
    assert len(got) == 2 and all("does not contain" in g for g in got)


def test_every_file_the_doc_tells_the_agent_to_use_exists():
    t = DOC.read_text(encoding="utf-8")
    rels = re.findall(r"`((?:tools|tests|agent|docs)/[A-Za-z0-9_./-]+\.[a-z]+)`", t)
    assert rels, "the doc names no files: this check would be vacuous"
    for rel in rels:
        assert (REPO / rel).exists() or "<" in rel, "ADAPTING.md names a file that does not exist: " + rel


# ---- the contract and the guidance, against the code they describe ----------------------------------------
def test_every_cited_marker_region_is_balanced_and_not_empty():
    t = DOC.read_text(encoding="utf-8")
    assert t.count("<!-- facts:begin -->") == t.count("<!-- facts:end -->") >= 2
    assert len(facts(t)) >= 10


def test_the_request_table_names_exactly_the_fields_the_engine_accepts():
    sec = section(DOC.read_text(encoding="utf-8"), 4)
    table = sec.split("Answer (stdout)")[0]
    first_col = [ln.split("|")[1] for ln in table.splitlines() if ln.startswith("| `")]
    names = {w for cell in first_col for w in ticked(cell)}
    assert names == set(neutral.FIELDS), (sorted(names ^ set(neutral.FIELDS)))


def test_the_request_table_lists_every_kind_shell_dialect_and_op():
    sec = section(DOC.read_text(encoding="utf-8"), 4)
    for word in neutral.KINDS + neutral.SHELLS + neutral.OPS:
        assert "`{}`".format(word) in sec or '"{}"'.format(word) in sec, word + " is not in the contract"


def test_the_contract_names_the_decisions_the_marker_and_the_unjudged_text():
    sec = section(DOC.read_text(encoding="utf-8"), 4)
    assert neutral.OK in sec and neutral.UNJUDGED in sec
    assert "|".join(DECISIONS) in sec
    assert "`partial`" in sec or "partial" in sec


def test_the_backup_ops_in_the_doc_are_the_engines_backup_ops():
    sec = section(DOC.read_text(encoding="utf-8"), 4)
    m = re.search(r"take a backup:\s*((?:`\w+`(?:,\s*)?(?:and\s+)?)+)", sec)
    assert m, "the contract must say which ops take a backup"
    assert tuple(sorted(re.findall(r"`(\w+)`", m.group(1)))) == tuple(sorted(engine._BACKUP_OPS))
    assert "default `update`" in sec


def test_the_adapter_deadlines_in_the_doc_match_the_template():
    t = DOC.read_text(encoding="utf-8")
    sec6 = section(t, 6)
    assert "{} s".format(tpl.ADAPTER_TIMEOUT_S) in sec6, "section 6 must state the adapter deadline"
    assert tpl.ADAPTER_TIMEOUT_S > tpl.ENGINE_BUDGET_S, "the adapter must outlive the engine's own watchdog"
    assert "SKYGUARD_ADAPTER_TIMEOUT_S" in sec6 and "SKYGUARD_ADAPTER_TIMEOUT_S" in (
        REPO / "tools" / "adapter_conformance.py").read_text(encoding="utf-8")


def test_the_slot_names_in_the_doc_are_the_names_the_template_defines():
    sec6 = section(DOC.read_text(encoding="utf-8"), 6)
    src = (REPO / "tools" / "adapting" / "adapter_template.py").read_text(encoding="utf-8")
    slot_block = src[src.index("# SLOTS-BEGIN"):src.index("# SLOTS-END")]
    for name in ("AGENT_NAME", "STATIC_BLOCK", "map_payload", "render_block", "render_allow", "render_ask"):
        assert name in slot_block, name + " is no longer a slot in the template"
        assert "`{}`".format(name) in sec6, name + " is missing from section 6"


def test_the_conformance_rows_and_faults_the_doc_describes_exist_in_the_tool():
    t = DOC.read_text(encoding="utf-8")
    sec6 = section(t, 6)
    for cid in conf.REQUIRED + conf.SHELL_PAIR:
        assert "`{}`".format(cid) in sec6, "section 6 must name the required case " + cid
    faults = set(conf.FAULTS) | set(conf.VERDICT_FAULTS)
    named = set(re.findall(r"`(engine-[a-z0-9-]+)`", sec6))
    assert named == faults, sorted(named ^ faults)
    for phrase in ("CONFORMANCE PASS", "UNVERIFIED STRUCTURE", conf.SHELL_NOTE.split(" (")[0], conf.UNMAPPED_NOTE,
                   "--port", "--command", "--profile", "--cases", "--adapter-dir", "JSON list") + conf.TOOL_LISTS:
        assert phrase in sec6, phrase + " missing from section 6"
    for base in conf.DECOY.values():
        assert "`{}:<tool>`".format(base) in sec6, "section 6 must say how to name a decoy per tool: " + base
    code = (REPO / "tools" / "adapter_conformance.py").read_text(encoding="utf-8")
    for phrase in ("CONFORMANCE PASS", "UNVERIFIED STRUCTURE", "SHELL UNGUARDED", '"--port"', '"--command"'):
        assert phrase in code, phrase + " is no longer printed or accepted by the tool"


def test_the_worked_examples_map_only_their_listed_tools_as_the_doc_says():
    sec6 = section(DOC.read_text(encoding="utf-8"), 6)
    assert "WRITE_TOOLS" in sec6 and "SHELL_TOOLS" in sec6 and conf.UNMAPPED_NOTE in sec6
    for ex in ("json_deny.slots.py", "exit2.slots.py"):
        src = (REPO / "tools" / "adapting" / "examples" / ex).read_text(encoding="ascii")
        assert "WRITE_TOOLS = (" in src and "SHELL_TOOLS = (" in src and conf.UNMAPPED_NOTE in src, ex
        assert src.count(" in WRITE_TOOLS:") == 1 and src.count(" in SHELL_TOOLS:") == 1, ex


def test_the_live_proof_covers_the_shell_tool_and_a_broken_hook_the_live_session_runs():
    sec7 = section(DOC.read_text(encoding="utf-8"), 7)
    for leaf in conf.PROOF_BOOLS[1:]:
        assert leaf in sec7, leaf + " is not explained in section 7"
    assert "echo x > Data/Canary.esp" in sec7
    flat = " ".join(sec7.split())
    assert "copy's" in flat and "fresh" in flat and "GUARD INERT" in flat
    assert "rename `run.py`" not in flat, "the old broken-hook step changed a file the live hook never runs"


def test_the_layer_2_lock_is_not_promised():
    # Plan 8 T4: Layer 2 ships in v4.0.0 as an opt-in lock, so the doc says "off by default" (it used to say "not yet
    # released"); either way an adapted agent is never told the lock protects it.
    t = DOC.read_text(encoding="utf-8")
    assert "off by default" in section(t, 0) and "off by default" in section(t, 2)
    assert "not yet released" not in t
    assert "where installed" not in t
    assert not re.search(r"\bPlan \d", t), "the doc must not promise a plan by number"
    philosophy = (REPO / "docs" / "safety-philosophy.md").read_text(encoding="utf-8")
    assert not re.search(r"^#+ Layer \d", philosophy, re.M), "safety-philosophy.md numbers 'layers' that clash with ADAPTING's"


def test_the_doc_says_what_python_means_off_windows():
    t = " ".join(DOC.read_text(encoding="utf-8").split("## 0.")[0].split())
    assert "python3" in t and "py -3" in t


def test_the_unjudged_mcp_text_in_the_doc_is_the_entrys():
    assert neutral.UNJUDGED_MCP in section(DOC.read_text(encoding="utf-8"), 4)


def test_the_profile_regex_rule_is_stated_the_way_the_tool_applies_it():
    sec6 = section(DOC.read_text(encoding="utf-8"), 6)
    assert "fullmatch" in sec6.replace("full match", "fullmatch") or "whole" in sec6
    src = (REPO / "tools" / "adapter_conformance.py").read_text(encoding="utf-8")
    assert "re.fullmatch" in src and "re.DOTALL" in src


def test_the_capability_report_leaves_in_the_doc_exist_in_the_template_and_the_tool():
    sec3 = section(DOC.read_text(encoding="utf-8"), 3)
    for path in ("payload_capture.files", "proof"):
        assert path.split(".")[0] in sec3
    assert "report" in sec3 and "derive" in sec3
    for gating in conf.GATING + (conf.TIMEOUT_GATE,):
        assert gating in sec3, "section 3 must say which leaves gate the status: " + gating
    flat = " ".join(sec3.split())
    assert "at least {} seconds".format(conf.ADAPTER_TIMEOUT_S + conf.HOOK_TIMEOUT_MARGIN_S) in flat
    assert "{} s deadline plus a {} s margin".format(tpl.ADAPTER_TIMEOUT_S, conf.HOOK_TIMEOUT_MARGIN_S) in flat
    assert "Four leaves gate" in flat and len(conf.GATING) == 4


def test_the_ask_rule_and_the_failure_rule_match_the_template():
    sec4 = section(DOC.read_text(encoding="utf-8"), 4)
    assert "exit code is not 0" in sec4 or "exit code other than 0" in sec4
    assert "first line" in sec4
    assert "unknown" in sec4 and "deny" in sec4
    assert hasattr(tpl, "ASK_PREFIX") and "render_ask" in sec4 + section(DOC.read_text(encoding="utf-8"), 6)


def test_the_doc_states_the_known_coverage_gaps():
    t = " ".join(DOC.read_text(encoding="utf-8").split())
    for must in ("replay", "devbench", "NOT JUDGED", "sys.__stdout__", "SHELL UNGUARDED", "neutral-pre-tool"):
        assert must.lower() in t.lower(), "the document no longer states: " + must


def test_the_doc_only_references_sections_that_exist():
    t = DOC.read_text(encoding="utf-8")
    have = set(int(n) for n in re.findall(r"^## (\d+)\. ", t, re.M))
    assert have == set(range(0, max(have) + 1))
    for n in re.findall(r"[Ss]ection (\d+)", t):
        assert int(n) in have, "refers to a section that does not exist: " + n


# ---- neutrality and privacy -------------------------------------------------------------------------------
def test_the_doc_uses_no_agent_specific_construct_beyond_naming_the_two_supported_agents():
    nz = _load("neutrality_for_claims", "tests/test_instruction_neutrality.py")
    hits = [(n, r, l) for n, r, l in nz.violations(DOC.read_text(encoding="utf-8")) if r != "claude-name"]
    assert not hits, hits


def test_the_doc_contains_no_personal_path():
    t = DOC.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:[\\/](Users|GOG)|/Users/\w+|AppData", t)
