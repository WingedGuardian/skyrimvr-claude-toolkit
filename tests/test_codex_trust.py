"""codex_trust -- does Codex actually run the toolkit's hooks? (Plan 4 Task 8)

The hash scheme is checked against hashes Codex 0.160.0 itself reported (trust_vectors.json, computed for
a neutral root so no personal path is committed). The match against hashes Codex stored after a REAL
`/hooks` approval is a live check (Task 0 Step 6: 2/2 trusted, exact key; Task 14 Phase C), not a fixture,
because those keys hold the user's path.

Every config written here lives in tmp_path; the user's ~/.codex/config.toml is never read or written.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO, BS

sys.path.insert(0, str(REPO / "tools"))
from skyguard import codex_trust as ct  # noqa: E402

VECTORS = json.loads((REPO / "tests" / "fixtures" / "codex" / "0.160.0" / "trust_vectors.json")
                     .read_text(encoding="utf-8"))
TEMPLATE = (REPO / "agent" / "targets" / "codex" / "hooks.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("v", VECTORS, ids=[v["label"] for v in VECTORS])
def test_every_measured_vector_reproduces(v):
    assert ct.hook_hash(v["event_key"], v["group"], v["handler"], v["windows"]) == v["codex_hash"]


def test_off_windows_the_posix_command_is_hashed():
    v = VECTORS[0]
    assert ct.hook_hash(v["event_key"], v["group"], v["handler"], False) != v["codex_hash"]
    h = ct.normalised_handler(v["event_key"], v["handler"], False)
    assert h["command"] == v["handler"]["command"]


def test_failure_mode_is_not_hashed_and_the_default_context_limit_is_not_either():
    by = {v["label"]: v for v in VECTORS}
    assert by["v1 PreToolUse + failureMode block only"]["codex_hash"] == by["v1 PreToolUse (Task 8 template)"]["codex_hash"]
    v = by["v1 PreToolUse (Task 8 template)"]
    assert "failureMode" not in ct.normalised_handler(v["event_key"], dict(v["handler"], failureMode="block"), True)


# --------------------------------------------------------------------------------------------------
# a fixture install + a Codex config in tmp_path
# --------------------------------------------------------------------------------------------------
def _install(tmp_path: Path, name: str = "Skyrim VR (x86)") -> Path:
    root = tmp_path / name
    (root / ".codex").mkdir(parents=True)
    win = str(root.resolve()).replace(BS, "/")
    text = TEMPLATE.replace("{{CODEX_ROOT_POSIX}}", "/x" + win).replace("{{CODEX_ROOT}}", win)
    (root / ".codex" / "hooks.json").write_text(text, encoding="utf-8", newline="\n")
    return root


def _expected(root: Path) -> dict:
    return ct.expected_entries(root / ".codex" / "hooks.json", True)


def _toml_key(k: str) -> str:
    assert "'" not in k
    return "'" + k + "'"


def _config(tmp_path: Path, entries: dict, project: Path = None, extra: str = "") -> Path:
    lines = ['model = "x"', ""]
    if project is not None:
        lines += ["[projects.{}]".format(_toml_key(str(project.resolve()).lower())), 'trust_level = "trusted"', ""]
    lines += ["[hooks.state]", ""]
    for k, fields in entries.items():
        lines.append("[hooks.state.{}]".format(_toml_key(k)))
        for f, val in fields.items():
            lines.append("{} = {}".format(f, json.dumps(val)))
        lines.append("")
    p = tmp_path / "config.toml"
    p.write_text("\n".join(lines) + extra, encoding="utf-8")
    return p


def _report(tmp_path, root, cfg):
    return ct.trust_report(root / ".codex" / "hooks.json", cfg, root, windows=True)


def _statuses(rep) -> list:
    return [r["status"] for r in rep["hooks"]]


def test_the_state_keys_are_spelled_like_codex_spells_them(tmp_path):
    """MEASURED (Task 0 Step 6): `<absolute hooks.json path, Windows spelling>:<snake event>:<g>:<h>`."""
    root = _install(tmp_path)
    keys = sorted(_expected(root))
    base = str((root / ".codex" / "hooks.json").resolve())
    assert keys == [base + ":pre_tool_use:0:0", base + ":session_start:0:0"]


def test_trusted_when_every_stored_hash_matches(tmp_path):
    root = _install(tmp_path)
    exp = _expected(root)
    rep = _report(tmp_path, root, _config(tmp_path, {k: {"trusted_hash": h} for k, h in exp.items()}, root))
    assert rep["overall"] == "trusted" and _statuses(rep) == ["trusted", "trusted"], rep
    assert rep["project_trust"] == "trusted"
    assert ct.exit_code(rep) == 0


def test_untrusted_with_no_state_table(tmp_path):
    root = _install(tmp_path)
    rep = _report(tmp_path, root, _config(tmp_path, {}))
    assert rep["overall"] == "untrusted" and _statuses(rep) == ["untrusted", "untrusted"], rep
    assert ct.exit_code(rep) == 1


def test_modified_when_one_differs_and_another_matches(tmp_path):
    root = _install(tmp_path)
    (pre, h1), (ses, h2) = sorted(_expected(root).items())
    rep = _report(tmp_path, root, _config(tmp_path, {pre: {"trusted_hash": "sha256:" + "0" * 64},
                                                     ses: {"trusted_hash": h2}}))
    assert _statuses(rep) == ["modified", "trusted"] and rep["overall"] == "modified", rep
    assert ct.exit_code(rep) == 1


def test_unknown_when_no_stored_hash_matches(tmp_path):
    """Every stored hash differs: the scheme itself may have changed -- never guessed as 'modified'."""
    root = _install(tmp_path)
    exp = _expected(root)
    rep = _report(tmp_path, root, _config(tmp_path, {k: {"trusted_hash": "sha256:" + "1" * 64} for k in exp}))
    assert _statuses(rep) == ["unknown", "unknown"] and rep["overall"] == "unknown", rep
    assert ct.exit_code(rep) == 2


def test_disabled_when_enabled_is_false(tmp_path):
    root = _install(tmp_path)
    (pre, h1), (ses, h2) = sorted(_expected(root).items())
    rep = _report(tmp_path, root, _config(tmp_path, {pre: {"trusted_hash": h1, "enabled": False},
                                                     ses: {"trusted_hash": h2}}))
    assert _statuses(rep) == ["disabled", "trusted"] and rep["overall"] == "disabled", rep
    assert ct.exit_code(rep) == 1


def test_unknown_on_an_unreadable_config(tmp_path):
    root = _install(tmp_path)
    for cfg in (tmp_path / "missing.toml", tmp_path):            # absent; a directory
        rep = _report(tmp_path, root, cfg)
        assert rep["overall"] == "unknown" and rep["error"], rep
        assert ct.exit_code(rep) == 2
    bad = tmp_path / "bad.toml"
    bad.write_bytes(b"\xff\xfe not utf-8")
    assert _report(tmp_path, root, bad)["overall"] == "unknown"


def test_unknown_on_an_unreadable_hooks_json(tmp_path):
    root = _install(tmp_path)
    (root / ".codex" / "hooks.json").write_text("{not json", encoding="utf-8")
    rep = _report(tmp_path, root, _config(tmp_path, {}))
    assert rep["overall"] == "unknown" and "hooks.json" in rep["error"], rep


def test_a_key_stored_only_in_another_case_is_unknown_not_trusted(tmp_path):
    """Whether Codex matches state keys case-insensitively is UNMEASURED: never err toward trusted."""
    root = _install(tmp_path)
    exp = _expected(root)
    rep = _report(tmp_path, root, _config(tmp_path, {k.lower(): {"trusted_hash": h} for k, h in exp.items()}))
    assert _statuses(rep) == ["unknown", "unknown"] and rep["overall"] == "unknown", rep
    assert {r["matched"] for r in rep["hooks"]} == {"casefold"}
    assert ct.exit_code(rep) == 2


def test_case_variant_duplicates_are_not_collapsed(tmp_path):
    """The exact key AND a lower-case twin: which one Codex reads is unmeasured -- report both, decide nothing."""
    root = _install(tmp_path)
    (pre, h1), (ses, h2) = sorted(_expected(root).items())
    rep = _report(tmp_path, root, _config(tmp_path, {pre: {"trusted_hash": h1},
                                                     pre.lower(): {"trusted_hash": "sha256:" + "2" * 64},
                                                     ses: {"trusted_hash": h2}}))
    row = rep["hooks"][0]
    assert row["status"] == "unknown" and sorted(row["variants"]) == sorted([pre, pre.lower()]), row
    assert rep["hooks"][1]["status"] == "trusted"
    assert rep["overall"] == "unknown"


@pytest.mark.parametrize("ch", list("%!$`"))
def test_a_root_the_hooks_cannot_run_from_is_never_reported_as_protected(tmp_path, ch):
    """cmd.exe expands %NAME% (and !NAME! under delayed expansion) inside the quoted -File path; bash
    expands $ and ` inside the quoted path: the hooks cannot run there, approved or not."""
    root = _install(tmp_path, "Skyrim " + ch + "x VR")
    exp = _expected(root)
    rep = _report(tmp_path, root, _config(tmp_path, {k: {"trusted_hash": h} for k, h in exp.items()}))
    assert _statuses(rep) == ["trusted", "trusted"]
    assert rep["overall"] == "unrunnable" and repr(ch) in rep["root_problem"], rep
    assert ct.exit_code(rep) == 1
    # also when Codex's config cannot be read: the root alone already decides it
    rep = _report(tmp_path, root, tmp_path / "absent.toml")
    assert rep["overall"] == "unrunnable" and ct.exit_code(rep) == 1


# --------------------------------------------------------------------------------------------------
# the narrow reader (Python < 3.11) agrees with tomllib, and refuses what it cannot read
# --------------------------------------------------------------------------------------------------
def test_narrow_reader_reads_the_shapes_codex_writes(tmp_path):
    root = _install(tmp_path)
    exp = _expected(root)
    cfg = _config(tmp_path, {k: {"trusted_hash": h, "enabled": True} for k, h in exp.items()}, root,
                  extra='\n[tui]\nhooks = "not ours"\n')
    tables = ct._read_tables_narrow(cfg.read_text(encoding="utf-8"))
    for k, h in exp.items():
        assert tables["hooks.state:" + k] == {"trusted_hash": h, "enabled": True}
    assert tables["projects:" + str(root.resolve()).lower()] == {"trust_level": "trusted"}


def test_narrow_reader_agrees_with_tomllib(tmp_path):
    """A separate test, so a Python without tomllib reports a SKIP here instead of passing silently."""
    tomllib = pytest.importorskip("tomllib")
    root = _install(tmp_path)
    exp = _expected(root)
    cfg = _config(tmp_path, {k: {"trusted_hash": h, "enabled": True} for k, h in exp.items()}, root,
                  extra='\n[tui]\nhooks = "not ours"\n')
    tables = ct._read_tables_narrow(cfg.read_text(encoding="utf-8"))
    assert ct._tables_from_doc(tomllib.loads(cfg.read_text(encoding="utf-8"))) == tables


@pytest.mark.parametrize("extra", [
    "\n[hooks.state.'k']\ntrusted_hash = \"sha256:x\"\nnote = 1\n",            # an unknown key in a trust table
    "\n[hooks.state]\n'k' = { trusted_hash = \"sha256:x\" }\n",              # an inline table
    "\n[hooks]\nstate = { }\n",                                              # trust data as a value
], ids=["unknown-key", "inline-table", "hooks-value"])
def test_narrow_reader_refuses_what_it_cannot_parse(tmp_path, extra):
    with pytest.raises(ct.Unreadable):
        ct._read_tables_narrow('model = "x"\n' + extra)


def test_narrow_reader_refuses_a_dotted_root_key():
    with pytest.raises(ct.Unreadable):
        ct._read_tables_narrow("hooks.state.'k'.trusted_hash = \"sha256:x\"\n[tui]\n")


# --------------------------------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------------------------------
def _cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "codex_trust.py"), *args],
                          capture_output=True, text=True, timeout=60)


def test_cli_exit_codes(tmp_path):
    root = _install(tmp_path)
    exp = ct.expected_entries(root / ".codex" / "hooks.json")     # this platform's spelling, as the CLI uses
    good = _config(tmp_path, {k: {"trusted_hash": h} for k, h in exp.items()}, root)
    r = _cli("report", "--root", str(root), "--codex-config", str(good))
    assert r.returncode == 0 and json.loads(r.stdout)["overall"] == "trusted", r.stdout + r.stderr
    none = tmp_path / "none"
    none.mkdir()
    r = _cli("report", "--root", str(root), "--codex-config", str(_config(none, {})))
    assert r.returncode == 1 and json.loads(r.stdout)["overall"] == "untrusted"
    r = _cli("report", "--root", str(root), "--codex-config", str(tmp_path / "absent.toml"))
    assert r.returncode == 2 and json.loads(r.stdout)["overall"] == "unknown"


def test_the_reader_never_writes_the_config(tmp_path):
    root = _install(tmp_path)
    cfg = _config(tmp_path, {})
    before = cfg.read_bytes(), cfg.stat().st_mtime_ns
    _cli("report", "--root", str(root), "--codex-config", str(cfg))
    assert (cfg.read_bytes(), cfg.stat().st_mtime_ns) == before
    src = (REPO / "tools" / "skyguard" / "codex_trust.py").read_text(encoding="utf-8")
    assert "write_text" not in src and "write_bytes" not in src and '"w"' not in src
