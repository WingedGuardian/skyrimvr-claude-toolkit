"""tools/resaver_native.py's stock tier (tools/resaver_stock.py): the user's own unpatched resaverpp-cli.exe.

The executable is a recording fake (`launch`) that answers from a synthetic --help built from option NAMES only, so
every case runs anywhere with no build. Saves are invented bytes in tmp_path; the permanent save store is a synthetic
project's. Every refusal asserts what it must prevent: a write that must not happen never reaches the fake.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
import resaver_native as rn  # noqa: E402
import resaver_stock  # noqa: E402

SKYRIM_51 = ["--purge-script", "--delete-forms", "--keep-instances", "--remove-unattached", "--remove-undefined",
             "--terminate-undefined", "--reset-havok", "--purify-formlists", "--batch", "--batch-all", "--compare",
             "--transplant", "--report", "--fix-mcm", "--remove-nonexistent", "--quests", "--journal", "--player",
             "--npcs", "--crash-log", "--path-idles", "--charged", "--spawned", "-o", "--top", "--mods", "--sources",
             "--dry-run"]
SKYRIM_56 = SKYRIM_51 + ["--describe", "--no-esl"]


def help_text(version, options):
    """A --help in upstream's layout (CliHelp.h): a version line, then one option per line."""
    return "ReSaver++ version {} command line\nusage: resaverpp-cli <save.ess> [actions] [-o <out.ess>]\n\n{}\n".format(
        version, "\n".join("  {:<26}does a thing".format(o) for o in options))


class Stock:
    """Canned stock answers. `calls` records every launch's arguments after the executable."""

    def __init__(self, exe):
        self.exe = exe
        self.calls = []
        self.version, self.options = "56", SKYRIM_56
        self.write_cosave = True
        self.exit = 0
        self.reread_exit = 0
        self.on_write = None

    def __call__(self, cmd):
        args = cmd[cmd.index(str(self.exe)) + 1:]
        self.calls.append(args)
        if args == ["--help"]:
            return SimpleNamespace(stdout=help_text(self.version, self.options).encode(), stderr=b"", returncode=0)
        if "-o" in args:
            out = Path(args[args.index("-o") + 1])
            out.write_bytes(b"edited")
            if self.write_cosave:
                out.with_suffix(".skse").write_bytes(b"synthetic cosave")
            if self.on_write:
                self.on_write()
            return SimpleNamespace(stdout=b"wrote it", stderr=b"", returncode=self.exit)
        if len(args) == 1 and Path(args[0]).name == "Output.ess":
            return SimpleNamespace(stdout=b"report of output", stderr=b"", returncode=self.reread_exit)
        return SimpleNamespace(stdout=("REPORT " + " ".join(args[1:])).encode(), stderr=b"", returncode=self.exit)

    def runs(self):
        return [c for c in self.calls if c != ["--help"]]

    def writes(self):
        return [c for c in self.calls if "-o" in c]


@pytest.fixture
def stock(tmp_path, monkeypatch):
    build = tmp_path / "build"
    build.mkdir()                                       # no BUILD-INFO.json: the patched tier is not built
    exe = tmp_path / "bin" / "resaverpp-cli.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"the user's own stock build")
    fake = Stock(exe)
    monkeypatch.setattr(rn, "BUILD_DIR", build)
    monkeypatch.setattr(rn, "launch", fake)
    monkeypatch.setenv("TOOLKIT_TEST_MODE", "1")
    monkeypatch.setenv("RESAVER_NATIVE_INTERPRETER", "python")
    monkeypatch.setenv("RESAVER_EXE", str(exe))
    monkeypatch.delenv("RESAVER_NATIVE_BUILD", raising=False)
    fake.project = tmp_path / "project"
    fake.project.mkdir()
    saves = tmp_path / "saves"
    saves.mkdir()
    fake.source = saves / "Input.ess"
    fake.source.write_bytes(b"synthetic input")
    fake.source.with_suffix(".skse").write_bytes(b"synthetic cosave")
    fake.out = saves / "Output.ess"
    fake.build = build
    return fake


def call(fake, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = rn.entry([str(a) for a in argv], project=fake.project)
    return code, json.loads(buf.getvalue())


# ------------------------------------------------------------------------------------------------ reads

def test_a_read_passes_the_tools_text_through(stock):
    code, out = call(stock, "info", stock.source)
    assert code == 0 and out["tier"] == "stock" and out["resaverVersion"] == "56" and out["testedVersion"] is True, out
    assert out["text"] == "REPORT " and stock.runs() == [[str(stock.source)]]
    assert "pairBackups" not in out


def test_an_option_the_version_lacks_exits_3_and_runs_nothing(stock):
    stock.version, stock.options = "51", SKYRIM_51           # --describe arrived after 51
    code, out = call(stock, "describe", stock.source)
    assert code == resaver_stock.CAPABILITY_EXIT == 3 and out["cannot"] is True, out
    assert "version (51) cannot do describe" in out["error"] and "--describe" in out["error"]
    assert stock.runs() == []


def test_an_untested_version_still_works_and_says_so(stock):
    stock.version = "57"
    code, out = call(stock, "describe", stock.source)
    assert code == 0 and out["testedVersion"] is False and "untested version" in out["note"], out
    assert stock.runs() == [[str(stock.source), "--describe"]]


def test_a_structured_operation_needs_the_patched_build(stock):
    code, out = call(stock, "find-refs", stock.source, "0A012345")
    assert code == 3 and "patched ReSaver Plus build" in out["error"] and "build.py" in out["error"], out
    assert stock.runs() == []


def test_a_read_takes_no_options_and_checks_its_arguments(stock):
    for argv, match in ((("info", stock.source, "--top"), "takes no options"),
                        (("compare", stock.source), "takes 1 argument"),
                        (("info", stock.source.with_name("Missing.ess")), "existing ESS input")):
        code, out = call(stock, *argv)
        assert code == 2 and match in out["error"], out
    assert stock.runs() == []


# ------------------------------------------------------------------------------------------------ writes

def test_a_stock_write_is_backed_up_first_written_new_and_reread(stock):
    code, out = call(stock, "clean", stock.source, stock.out, "--unattached", "--undefined", "--apply")
    assert code == 0 and out["tier"] == "stock", out
    assert stock.writes() == [[str(stock.source), "--remove-unattached", "--remove-undefined", "-o",
                               str(stock.out.resolve())]]
    assert stock.runs()[-1] == [str(stock.out.resolve())]                  # re-read by the same build
    assert len(out["pairBackups"]) == 1 and out["pairBackups"][0].startswith("core-")
    assert "not previewed" in out["verification"] and "Load-test it in game" in out["verification"]
    assert stock.source.read_bytes() == b"synthetic input"


@pytest.mark.parametrize("op,flag,stock_option", [("reset-havok", None, "--reset-havok"),
                                                  ("cleanse-formlists", None, "--purify-formlists")])
def test_each_stock_write_maps_to_its_stock_option(stock, op, flag, stock_option):
    code, out = call(stock, op, stock.source, stock.out, "--apply")
    assert code == 0, out
    assert stock.writes() == [[str(stock.source), stock_option, "-o", str(stock.out.resolve())]]


def test_without_apply_a_stock_build_cannot_preview_and_nothing_runs(stock):
    code, out = call(stock, "clean", stock.source, stock.out, "--unattached")
    assert code == 3 and "cannot preview" in out["error"], out
    assert stock.runs() == [] and not stock.out.exists()


def test_a_missing_write_option_exits_3_before_any_backup(stock):
    stock.options = [o for o in SKYRIM_56 if o != "--purify-formlists"]
    code, out = call(stock, "cleanse-formlists", stock.source, stock.out, "--apply")
    assert code == 3 and "--purify-formlists" in out["error"], out
    assert stock.runs() == []
    store = rn.store_root(stock.project)
    assert not store.exists() or not any(store.rglob("*.ess"))


def test_clean_needs_a_named_kind_on_a_stock_build(stock):
    code, out = call(stock, "clean", stock.source, stock.out, "--apply")
    assert code == 2 and "needs at least one of" in out["error"], out
    assert stock.writes() == []


def test_an_existing_output_is_refused_before_anything_runs(stock):
    stock.out.write_bytes(b"someone's save")
    code, out = call(stock, "clean", stock.source, stock.out, "--unattached", "--apply")
    assert code == 2 and "existing output" in out["error"], out
    assert stock.writes() == [] and stock.out.read_bytes() == b"someone's save"


def test_a_missing_cosave_fails_and_keeps_the_candidate(stock):
    stock.write_cosave = False
    code, out = call(stock, "clean", stock.source, stock.out, "--unattached", "--apply")
    assert code == 2 and "without its .skse co-save" in out["error"], out
    assert stock.out.read_bytes() == b"edited"                 # retained for inspection, never deleted


def test_an_output_the_build_cannot_reread_fails(stock):
    stock.reread_exit = 1
    code, out = call(stock, "clean", stock.source, stock.out, "--unattached", "--apply")
    assert code == 2 and "could not re-read its output" in out["error"], out


def test_a_failed_write_is_an_error(stock):
    stock.exit = 1
    code, out = call(stock, "clean", stock.source, stock.out, "--unattached", "--apply")
    assert code == 2 and "did not complete clean" in out["error"], out


def test_a_source_changed_during_the_write_fails(stock):
    stock.on_write = lambda: stock.source.write_bytes(b"changed underneath")
    code, out = call(stock, "clean", stock.source, stock.out, "--unattached", "--apply")
    assert code == 2 and "Source pair changed" in out["error"], out


# ------------------------------------------------------------------------------------------------ which tier

def test_a_broken_patched_build_never_falls_back_to_stock(stock):
    (stock.build / "BUILD-INFO.json").write_text(json.dumps({"upstream_commit": "0" * 40}), encoding="utf-8")
    code, out = call(stock, "info", stock.source)
    assert code == 2 and "pinned" in out["error"], out
    assert stock.calls == []


def test_no_build_and_no_stock_keeps_the_not_built_refusal(stock, monkeypatch):
    monkeypatch.delenv("RESAVER_EXE")
    code, out = call(stock, "info", stock.source)
    assert code == 2 and "ReSaver Plus is not built" in out["error"] and "RESAVER_EXE" in out["error"], out


def test_a_relative_or_missing_stock_path_is_refused(stock, monkeypatch):
    for value, match in (("resaverpp-cli.exe", "must be absolute"), (str(stock.exe) + ".gone", "does not exist")):
        monkeypatch.setenv("RESAVER_EXE", value)
        code, out = call(stock, "info", stock.source)
        assert code == 2 and match in out["error"], out
    assert stock.calls == []


def test_the_executable_changing_mid_call_is_refused(stock, monkeypatch):
    real = stock.__call__

    def swap(cmd):
        answer = real(cmd)
        stock.exe.write_bytes(b"a different binary")     # after --help, before the read
        return answer
    monkeypatch.setattr(rn, "launch", swap)
    code, out = call(stock, "info", stock.source)
    assert code == 2 and "changed since this call started" in out["error"], out


def test_verify_reports_the_stock_build(stock):
    code, out = call(stock, "--verify")
    assert code == 0 and out["backend"] == "stock" and out["resaverVersion"] == "56", out
    assert out["sha256"] == hashlib.sha256(stock.exe.read_bytes()).hexdigest()
    assert "describe" in out["reads"] and out["writes"] == sorted(resaver_stock.STOCK_WRITES)


def test_the_stock_module_has_no_entry_point_of_its_own():
    """The guard judges ReSaver writes by the adapter's name (shell_rules S-RESAVER-APPLY: `resaver_native`);
    a second runnable adapter would be an unjudged write path."""
    source = (REPO / "tools" / "resaver_stock.py").read_text(encoding="utf-8")
    assert "__main__" not in source and "sys.argv" not in source
