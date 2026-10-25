"""tools/resaver_native.py: admission, pinning, backup ordering and receipt checks of the native backend (port task 4).

The native executable is replaced by a recording fake (`launch`), so every case runs on any platform with no build.
Saves are invented bytes in tmp_path; the permanent save store is a synthetic project's (tmp_path), never a real one.
Every refusal asserts what it must PREVENT: for a write, that the native tool never ran the write (and, for a backup
failure, never ran at all).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
import resaver_native as rn  # noqa: E402

STUB = b"stub native build"


class Native:
    """Canned native answers. `calls` records every launch's arguments (after `--json`)."""

    def __init__(self):
        self.calls = []
        self.preview = {"ok": True, "op": "clean", "apply": False, "dryRun": True, "deltaVerified": True,
                        "targets": ["thread:00000011"], "wroteFile": False}
        self.applied = dict(self.preview, apply=True, dryRun=False, wroteFile=True, verified=True)
        self.raw = None
        self.on_apply = None
        self.write_skse = b"synthetic cosave"     # the native writer carries the input's co-save over

    def __call__(self, cmd):
        args = cmd[cmd.index("--json") + 1:]
        self.calls.append(args)
        if self.raw is not None:
            return SimpleNamespace(stdout=self.raw, returncode=0)
        if "--apply" not in args:
            return SimpleNamespace(stdout=json.dumps(self.preview).encode(), returncode=0)
        out = Path(next(a for a in args[2:] if a.lower().endswith(".ess")))
        out.write_bytes(b"edited")
        if self.write_skse is not None:
            out.with_suffix(".skse").write_bytes(self.write_skse)
        if self.on_apply:
            self.on_apply()
        return SimpleNamespace(stdout=json.dumps(self.applied).encode(), returncode=0)

    def writes(self):
        return [c for c in self.calls if "--apply" in c]


@pytest.fixture
def native(tmp_path, monkeypatch):
    build = tmp_path / "build"
    build.mkdir()
    (build / rn.EXECUTABLE).write_bytes(STUB)
    (build / "BUILD-INFO.json").write_text(json.dumps({"upstream_commit": rn.UPSTREAM_COMMIT, "executable": rn.EXECUTABLE,
                                                       "sha256": hashlib.sha256(STUB).hexdigest()}), encoding="utf-8")
    fake = Native()
    monkeypatch.setattr(rn, "BUILD_DIR", build)
    monkeypatch.setattr(rn, "launch", fake)
    monkeypatch.setenv("TOOLKIT_TEST_MODE", "1")
    monkeypatch.setenv("RESAVER_NATIVE_INTERPRETER", "python")     # lets executable() admit the stub off Windows
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


def refused(fake, *argv, match):
    code, out = call(fake, *argv)
    assert code == 2 and not out["ok"], out
    assert match in out["error"], out["error"]
    return out


# ------------------------------------------------------------------------------------------------ a good write

def test_a_good_write_is_backed_up_previewed_applied_and_receipted(native):
    code, out = call(native, "clean", native.source, native.out, "--undefined", "--apply")
    assert code == 0 and out["ok"] and out["backend"] == "ReSaver Plus native", out
    assert [("--apply" in c) for c in native.calls] == [False, True]      # preview, then the write
    assert len(out["pairBackups"]) == 1 and out["pairBackups"][0].startswith("core-")
    assert out["inputHashes"][".skse"] == out["outputHashes"][".skse"] is not None
    assert native.source.read_bytes() == b"synthetic input"


def test_reads_never_back_up_or_preview(native, monkeypatch):
    monkeypatch.setattr(rn, "backup", lambda *a: pytest.fail("a read backed up"))
    native.preview = {"ok": True, "op": "info", "changeForms": 4}
    code, out = call(native, "info", native.source)
    assert code == 0 and out["changeForms"] == 4 and len(native.calls) == 1


# ------------------------------------------------------------------------------------------------ admission

@pytest.mark.parametrize("argv,match", [
    (["bogus-op", "{src}"], "supported operation"),
    (["info", "{src}", "--apply"], "Unknown or repeated option"),
    (["clean", "{src}", "{out}", "--undefined", "--undefined", "--apply"], "Unknown or repeated option"),
    (["dump", "{src}", "--limit"], "Missing option value"),
    (["clean", "{src}", "--apply"], "one explicit new output"),
    (["clean", "{src}", "{src}", "--apply"], "existing output .ess"),
    (["info", "{src}", "extra"], "Too many positional"),
], ids=["unknown-op", "flag-not-allowed", "repeated-flag", "missing-value", "apply-without-output",
        "output-is-input", "extra-positional"])
def test_admission_refuses_outside_the_contract(native, argv, match):
    refused(native, *[a.format(src=native.source, out=native.out) for a in argv], match=match)
    assert not native.calls


def test_an_existing_output_ess_is_refused(native):
    native.out.write_bytes(b"someone's save")
    refused(native, "clean", native.source, native.out, "--apply", match="existing output .ess")
    assert not native.calls and native.out.read_bytes() == b"someone's save"


def test_an_existing_output_skse_alone_is_refused(native):
    native.out.with_suffix(".skse").write_bytes(b"orphan cosave")
    refused(native, "clean", native.source, native.out, "--apply", match="existing output .skse")
    assert not native.calls and not native.out.exists()


def test_a_missing_output_folder_is_refused(native):
    refused(native, "clean", native.source, native.out.parent / "nowhere" / "Out.ess", "--apply",
            match="Output directory must already exist")
    assert not native.calls


def test_an_output_inside_the_permanent_save_store_is_refused(native):
    store = rn.store_root(native.project)
    store.mkdir(parents=True, exist_ok=True)
    refused(native, "clean", native.source, store / "Out.ess", "--apply", match="permanent save store")
    assert not native.calls


def _symlink(link: Path, target: Path):
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError) as exc:
        pytest.skip("creating symlinks unavailable on this host ({})".format(type(exc).__name__))


def test_a_dangling_output_link_is_an_existing_member(native):
    _symlink(native.out, native.out.parent / "gone.ess")
    refused(native, "clean", native.source, native.out, "--apply", match="existing output .ess")
    assert not native.calls


def test_a_dangling_input_link_is_refused(native):
    link = native.source.parent / "Dangling.ess"
    _symlink(link, native.source.parent / "gone.ess")
    refused(native, "info", link, match="dangling link")
    assert not native.calls


# ------------------------------------------------------------------------------------------------ pinning

def test_no_build_info_is_refused_and_names_the_builder(native):
    (native.build / "BUILD-INFO.json").unlink()
    refused(native, "info", native.source, match="build.py")
    assert not native.calls


def test_a_changed_executable_is_refused(native):
    (native.build / rn.EXECUTABLE).write_bytes(STUB + b"!")
    refused(native, "info", native.source, match="SHA-256 mismatch")
    assert not native.calls


def test_a_build_of_another_upstream_commit_is_refused(native):
    info = json.loads((native.build / "BUILD-INFO.json").read_text(encoding="utf-8"))
    (native.build / "BUILD-INFO.json").write_text(json.dumps(dict(info, upstream_commit="1" * 40)), encoding="utf-8")
    refused(native, "info", native.source, match="not the pinned")
    assert not native.calls


def test_the_build_seam_is_honoured_only_in_test_mode(native, monkeypatch, tmp_path):
    monkeypatch.setenv("RESAVER_NATIVE_BUILD", str(tmp_path / "elsewhere"))
    assert rn.build_dir() == tmp_path / "elsewhere"
    monkeypatch.delenv("TOOLKIT_TEST_MODE")
    assert rn.build_dir() == rn.BUILD_DIR


def test_verify_reports_the_pinned_build(native):
    code, out = call(native, "--verify")
    assert code == 0 and out["sha256"] == hashlib.sha256(STUB).hexdigest() and not native.calls


# ------------------------------------------------------------------------------------------------ backup ordering

def test_a_backup_failure_means_the_native_tool_never_runs(native, monkeypatch):
    def broken(*_a):
        raise OSError("store unwritable")
    monkeypatch.setattr(rn, "backup", broken)
    refused(native, "clean", native.source, native.out, "--apply", match="store unwritable")
    assert native.calls == [] and not native.out.exists()


def test_a_backup_that_records_nothing_is_a_failure(native, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import save_store
    monkeypatch.setattr(save_store, "backup_pairs", lambda *a, **k: [])
    refused(native, "clean", native.source, native.out, "--apply", match="recorded nothing")
    assert native.calls == []


def test_the_backup_happens_before_the_preview(native, monkeypatch):
    order = []
    real = rn.backup
    monkeypatch.setattr(rn, "backup", lambda *a: order.append("backup") or real(*a))
    native_call = native.__call__
    monkeypatch.setattr(rn, "launch", lambda cmd: order.append("apply" if "--apply" in cmd else "preview")
                        or native_call(cmd))
    code, _out = call(native, "clean", native.source, native.out, "--apply")
    assert code == 0 and order == ["backup", "preview", "apply"]


# ------------------------------------------------------------------------------------------------ receipts

def test_a_preview_without_a_verified_delta_writes_nothing(native):
    native.preview = dict(native.preview, deltaVerified=False)
    refused(native, "clean", native.source, native.out, "--apply", match="plan is unverified")
    assert not native.writes() and not native.out.exists()


def test_an_applied_receipt_that_differs_from_the_preview_is_an_error(native):
    native.applied = dict(native.applied, targets=["thread:00000099"])
    refused(native, "clean", native.source, native.out, "--apply", match="differs from preview")
    assert native.out.exists()                        # candidate retained for inspection, never deleted


@pytest.mark.parametrize("key,match", [("verified", "not verified on reopen"),
                                       ("wroteFile", "no file written"),
                                       ("deltaVerified", "lacks a verified delta")])
def test_an_incomplete_applied_receipt_is_an_error(native, key, match):
    native.applied = dict(native.applied, **{key: False})
    refused(native, "clean", native.source, native.out, "--apply", match=match)


def test_a_source_changed_during_the_write_is_an_error(native):
    native.on_apply = lambda: native.source.write_bytes(b"changed underneath")
    refused(native, "clean", native.source, native.out, "--apply", match="Source pair changed")


def test_a_cosave_changed_by_the_write_is_an_error(native):
    native.write_skse = b"different cosave"
    refused(native, "clean", native.source, native.out, "--apply", match="Co-save bytes or presence")


def test_a_cosave_dropped_by_the_write_is_an_error(native):
    native.write_skse = None
    refused(native, "clean", native.source, native.out, "--apply", match="Co-save bytes or presence")


def test_the_cosave_carried_over_unchanged_passes(native):
    code, out = call(native, "clean", native.source, native.out, "--apply")
    assert code == 0 and out["outputHashes"][".skse"] == out["inputHashes"][".skse"]


@pytest.mark.parametrize("raw", [b"not json", b"\xff\xfe{}", b"[1, 2]"], ids=["not-json", "not-utf8", "not-object"])
def test_invalid_structured_output_is_an_error(native, raw):
    native.raw = raw
    refused(native, "info", native.source, match="invalid structured output")


def test_a_native_rejection_is_passed_through_as_an_error(native):
    native.raw = json.dumps({"ok": False, "error": "decoder said no"}).encode()
    refused(native, "info", native.source, match="decoder said no")


# ------------------------------------------------------------------------------------------------ tools/resaver-plus.py

def _plus():
    import importlib.util
    spec = importlib.util.spec_from_file_location("resaver_plus_reports", REPO / "tools" / "resaver-plus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_report_adapter_runs_the_pinned_build_read_only(native, monkeypatch):
    plus = _plus()
    seen = []
    monkeypatch.setattr(plus.subprocess, "run", lambda cmd, **k: seen.append(cmd) or SimpleNamespace(returncode=0))
    assert plus.main(["report", str(native.source), "--top", "5"]) == 0
    assert seen[0][-3:] == [str(native.source.resolve()), "--top", "5"]
    assert not any(a in ("-o", "--apply", "--batch") for a in seen[0])


def test_the_report_adapter_refuses_without_a_build(native, capsys):
    (native.build / "BUILD-INFO.json").unlink()
    assert _plus().main(["report", str(native.source)]) == 2
    assert "build.py" in capsys.readouterr().err
