"""tools/mirror-launch-logs.py: a one-shot, append-only mirror of the game's logs.

The promise is that a mirror copy is never rewritten and never lost: an unchanged log costs no write, a grown log
costs only its new bytes, and a truncated or replaced log (the next launch) leaves the previous copy kept under a
new name. The logs here are hand-authored.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

from conftest import REPO

TOOL = REPO / "tools" / "mirror-launch-logs.py"
_spec = importlib.util.spec_from_file_location("mirror_launch_logs", TOOL)
mlog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mlog)


def kept_copies(folder, name):
    return sorted(p for p in folder.iterdir() if p.name.startswith(name + ".replaced-"))


# --------------------------------------------------------------------------- mirror()

def test_new_then_unchanged_does_not_rewrite_the_copy(tmp_path):
    src, dst = tmp_path / "skse.log", tmp_path / "m" / "skse.log"
    src.write_bytes(b"a" * (2 * 1024 * 1024 + 7))
    assert mlog.mirror(src, dst) == ("NEW", src.stat().st_size, None)
    stamp = dst.stat().st_mtime_ns
    assert mlog.mirror(src, dst) == ("UNCHANGED", 0, None)
    assert dst.stat().st_mtime_ns == stamp


def test_a_grown_log_appends_only_the_new_bytes(tmp_path):
    src, dst = tmp_path / "skse.log", tmp_path / "m" / "skse.log"
    src.write_bytes(b"line one\n" * 1000)
    mlog.mirror(src, dst)
    with src.open("ab") as f:
        f.write(b"new line\n")
    assert mlog.mirror(src, dst) == ("APPENDED", 9, None)
    assert dst.read_bytes() == src.read_bytes()


def test_an_empty_copy_then_a_grown_log_appends(tmp_path):
    src, dst = tmp_path / "skse.log", tmp_path / "m" / "skse.log"
    src.write_bytes(b"")
    assert mlog.mirror(src, dst)[0] == "NEW"
    src.write_bytes(b"started\n")
    assert mlog.mirror(src, dst) == ("APPENDED", 8, None)
    assert dst.read_bytes() == b"started\n"


@pytest.mark.parametrize("first, second", [
    (b"old launch\n" * 2000, b"new launch\n"),        # truncated: the next launch starts the log again
    (b"x" * 12000, b"y" * 12000),                     # replaced, same size
    (b"y" * 12000, b"y" * 8000 + b"z" * 5000),        # replaced, bigger, prefix agrees but the tail does not
    (b"some text\n", b""),                            # emptied
])
def test_a_truncated_or_replaced_log_keeps_the_previous_copy(tmp_path, first, second):
    src, dst = tmp_path / "skse.log", tmp_path / "m" / "skse.log"
    src.write_bytes(first)
    mlog.mirror(src, dst)
    src.write_bytes(second)
    status, written, kept = mlog.mirror(src, dst)
    assert status == "REPLACED" and written == len(second)
    assert dst.read_bytes() == second
    assert kept is not None and kept.read_bytes() == first, "the previous launch's copy was lost"
    assert kept_copies(dst.parent, "skse.log") == [kept]


def test_two_replacements_in_one_second_keep_both(tmp_path):
    src, dst = tmp_path / "skse.log", tmp_path / "m" / "skse.log"
    for body in (b"first\n", b"second!\n", b"3\n"):
        src.write_bytes(body)
        mlog.mirror(src, dst)
    os.utime(dst, None)
    bodies = sorted(p.read_bytes() for p in kept_copies(dst.parent, "skse.log"))
    assert bodies == [b"first\n", b"second!\n"]


class _ShrinksAfterFirstRead:
    """A source handle whose file is truncated by "the game" right after the first block is read."""

    def __init__(self, path):
        self._f = open(path, "rb")
        self._path = path
        self._reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()

    def fileno(self):
        return self._f.fileno()

    def seek(self, pos):
        return self._f.seek(pos)

    def read(self, n=-1):
        self._reads += 1
        data = self._f.read(n)
        if self._reads == 1:
            os.truncate(self._path, 0)
        return data


def test_a_log_that_shrinks_mid_read_is_short_and_kept_on_the_next_pass(tmp_path, monkeypatch):
    src, dst = tmp_path / "Papyrus.0.log", tmp_path / "m" / "Papyrus.0.log"
    body = b"entry\n" * 400_000                       # 2.4 MB: more than one CHUNK
    src.write_bytes(body)
    monkeypatch.setattr(mlog, "open_source", _ShrinksAfterFirstRead)
    status, written, _ = mlog.mirror(src, dst)
    assert status == "SHORT" and written == mlog.CHUNK, (status, written)
    assert dst.read_bytes() == body[:mlog.CHUNK]
    monkeypatch.undo()
    src.write_bytes(b"next launch\n")
    status, _, kept = mlog.mirror(src, dst)
    assert status == "REPLACED" and kept.read_bytes() == body[:mlog.CHUNK]
    assert dst.read_bytes() == b"next launch\n"


# --------------------------------------------------------------------------- the command

@pytest.fixture
def game_logs(tmp_path):
    skse = tmp_path / "My Games" / "Skyrim VR" / "SKSE"
    papyrus = tmp_path / "My Games" / "Skyrim VR" / "Logs" / "Script"
    skse.mkdir(parents=True)
    papyrus.mkdir(parents=True)
    (skse / "sksevr.log").write_text("checking plugin X.dll\n", encoding="utf-8")
    (skse / "ExamplePlugin.log").write_text("hello\n", encoding="utf-8")
    (skse / "crash-2026-01-01-00-00-00.log").write_text("Unhandled exception\n", encoding="utf-8")
    (skse / "notes.txt").write_text("not a log\n", encoding="utf-8")
    (papyrus / "Papyrus.0.log").write_text("[01/01/2026 - 00:00:00AM] Papyrus log opened\n", encoding="utf-8")
    return skse, papyrus


def run(*args, env=None):
    e = dict(os.environ)
    for k in ("SKSE_LOG_DIR", "SKSE_CRASH_DIR", "PAPYRUS_LOG_DIR"):
        e.pop(k, None)
    e.update(env or {})
    return subprocess.run([sys.executable, "-B", str(TOOL), *map(str, args)], capture_output=True, text=True,
                          timeout=120, env=e)


def env_for(skse, papyrus):
    return {"SKSE_LOG_DIR": str(skse), "SKSE_CRASH_DIR": str(skse), "PAPYRUS_LOG_DIR": str(papyrus)}


def test_the_command_mirrors_each_log_once_then_finds_nothing_new(tmp_path, game_logs):
    skse, papyrus = game_logs
    dest = tmp_path / "mirror"
    r = run("--dest", dest, env=env_for(skse, papyrus))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4 file(s): 4 NEW, 0 APPENDED, 0 UNCHANGED, 0 REPLACED, 0 SHORT, 0 FAILED" in r.stdout, r.stdout
    assert (dest / "SKSE" / "sksevr.log").is_file() and (dest / "Papyrus" / "Papyrus.0.log").is_file()
    # The crash dump lives in the SKSE folder: mirrored once, not twice.
    assert sorted(p.name for p in dest.rglob("crash-*")) == ["crash-2026-01-01-00-00-00.log"]
    assert not list(dest.rglob("notes.txt"))
    r = run("--dest", dest, env=env_for(skse, papyrus))
    assert "4 file(s): 0 NEW, 0 APPENDED, 4 UNCHANGED" in r.stdout, r.stdout


def test_the_command_reports_appends_and_replacements(tmp_path, game_logs):
    skse, papyrus = game_logs
    dest = tmp_path / "mirror"
    run("--dest", dest, env=env_for(skse, papyrus))
    with (skse / "ExamplePlugin.log").open("ab") as f:
        f.write(b"more\n")
    (papyrus / "Papyrus.0.log").write_text("new session\n", encoding="utf-8")
    r = run("--dest", dest, env=env_for(skse, papyrus))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "APPENDED  SKSE/ExamplePlugin.log  +5 bytes" in r.stdout, r.stdout
    replaced = next(ln for ln in r.stdout.splitlines() if "Papyrus/Papyrus.0.log" in ln)
    assert replaced.lstrip().startswith("REPLACED") and "previous copy kept as Papyrus.0.log.replaced-" in replaced
    assert len(kept_copies(dest / "Papyrus", "Papyrus.0.log")) == 1


def test_missing_folders_are_named_not_an_error(tmp_path):
    nowhere = tmp_path / "nowhere"
    r = run("--dest", tmp_path / "mirror", env=env_for(nowhere, nowhere))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not found: nothing to copy" in r.stdout and "0 file(s)" in r.stdout


@pytest.mark.parametrize("args", [["--bogus"], ["extra"], ["--dest"]])
def test_bad_usage_is_exit_2(tmp_path, args):
    r = run(*args)
    assert r.returncode == 2 and r.stderr.startswith("usage:"), r.stdout + r.stderr


def test_the_default_destination_is_the_toolkits_unshipped_backups_folder():
    """.claude/backups/ is ignored by git and refused by the release payload check, so the mirror never ships."""
    assert mlog.DEFAULT_DEST == REPO / ".claude" / "backups" / "log-mirror"
