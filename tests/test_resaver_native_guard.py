"""The guard and the native ReSaver Plus backend (port task 6).

S-RESAVER-NATIVE-DIRECT denies ReSaver Plus's own executables when they would write a save with none of the adapter's
checks: the CLI with `-o`, `--batch`, `--batch-all` or the structured `--apply`, and the GUI at all. Reads stay
allowed. The adapter run directly (`python tools/resaver_native.py ... --apply`) is judged like the wrapper
(S-RESAVER-APPLY / the save policy), and the adapter itself refuses an existing output even where the guard allows.
"""
from __future__ import annotations

import sys

import pytest

from conftest import REPO, GAME_ROOT

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, codes, engine, paths, rules, shell_rules  # noqa: E402


def run(cmd, tmp_path, shell="bash"):
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME_ROOT})
    a = audit.Audit(str(tmp_path))
    return shell_rules.decide(cmd, r, a, shell=shell), a


def fired(a):
    return {rid for rid, _ in a.fired}


@pytest.mark.parametrize("cmd,program", [
    ("resaverpp-cli x.ess --remove-undefined -o y.ess", "resaverpp-cli.exe"),
    ("resaverpp-cli x.ess --batch list.txt", "resaverpp-cli.exe"),
    ("resaverpp-cli x.ess --batch-all", "resaverpp-cli.exe"),
    ("resaverpp-cli --json clean x.ess y.ess --apply", "resaverpp-cli.exe"),
    ('"C:/kit/tools/resaver-plus/build/resaverpp-cli.exe" x.ess --reset-havok -o y.ess', "resaverpp-cli.exe"),
    ("bash -c 'tools/resaver-plus/build/resaverpp-cli.exe x.ess --purify-formlists -o y.ess'", "resaverpp-cli.exe"),
    ('OUT=y.ess; resaverpp-cli x.ess --remove-unattached -o "$OUT"', "resaverpp-cli.exe"),
    ("resaverpp-cli x.ess $FLAGS", "resaverpp-cli.exe"),
    ("ReSaverPP.exe", "ReSaverPP.exe"),
    ("start ReSaverPP.exe x.ess", "ReSaverPP.exe"),
], ids=["dash-o", "batch", "batch-all", "structured-apply", "quoted-exe", "bash-c", "variable-output",
        "unproven-args", "gui", "gui-via-start"])
def test_a_direct_native_write_is_denied(tmp_path, cmd, program):
    v, a = run(cmd, tmp_path)
    assert v.decision == "deny", (cmd, v)
    assert v.reason == rules.message("S-RESAVER-NATIVE-DIRECT", file=program)
    assert "S-RESAVER-NATIVE-DIRECT" in fired(a)


@pytest.mark.parametrize("cmd", [
    "resaverpp-cli x.ess --top 20",
    "resaverpp-cli Script.pex --psc",
    "resaverpp-cli --json info x.ess",
    "resaverpp-cli x.ess --compare y.ess",
    "echo resaverpp-cli x.ess -o y.ess",
    "grep -n -- '-o' tools/resaver-plus/README.md  # resaverpp-cli",
], ids=["report", "decompile", "structured-read", "compare", "echo-is-text", "grep-mention"])
def test_a_native_read_or_a_mention_is_not_denied(tmp_path, cmd):
    _v, a = run(cmd, tmp_path)
    assert "S-RESAVER-NATIVE-DIRECT" not in fired(a), cmd


def test_powershell_spelling_is_denied(tmp_path):
    v, _a = run("& 'C:\\kit\\tools\\resaver-plus\\build\\resaverpp-cli.exe' x.ess --remove-undefined -o y.ess",
                tmp_path, shell="powershell")
    assert v.decision == "deny" and "resaverpp-cli.exe" in v.reason


def test_the_rule_is_never_shadowed():
    assert "shell.S-RESAVER-NATIVE-DIRECT" in engine.NEVER_SHADOW
    assert "shell.S-RESAVER-NATIVE-DIRECT" in codes.decisive_codes()


@pytest.mark.parametrize("cmd", [
    "python tools/resaver_native.py clean x.ess out.ess --apply",
    "python -B tools/resaver_native.py reset-havok x.ess out.ess --apply",
    "py -3 tools/resaver_native.py clean x.ess out.ess --apply",
    "RESAVER_BACKEND=native bash tools/resaver-cli.sh clean x.ess out.ess --apply",
], ids=["python", "python-B", "py-launcher", "wrapper-with-backend"])
def test_the_adapter_run_directly_is_a_resaver_write(cmd):
    t = shell_rules.resaver_target(cmd, "bash")
    assert t is not None and t[0] in shell_rules.RESAVER_OPS, cmd
    calls = shell_rules.resaver_write_calls(cmd, "bash")
    assert calls and calls[0][1] == ["out.ess"], calls


def test_the_adapter_preview_is_not_a_write():
    assert shell_rules.resaver_target("python tools/resaver_native.py clean x.ess out.ess", "bash") is None


def test_the_adapter_refuses_an_existing_output_even_where_the_guard_allows(tmp_path, monkeypatch):
    """The guard backs up and allows a ReSaver write to a save it can place; the native adapter adds the stricter
    new-output rule itself (an existing output member is refused before anything runs)."""
    import resaver_native as rn
    src, out = tmp_path / "In.ess", tmp_path / "Out.ess"
    src.write_bytes(b"synthetic")
    out.write_bytes(b"existing")
    with pytest.raises(rn.Refused, match="existing output"):
        rn.invocation(["clean", str(src), str(out), "--apply"], project=tmp_path)
