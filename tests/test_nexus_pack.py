"""The Nexus pack: is it the same shape every time?

WHY THIS EXISTS. Across eleven releases the pack landed in six different shapes and
four different places. v3.2-3.4 wrote `nexus-vX-description.txt`; v3.5.1 switched to
`UPLOAD-NOTES.txt` plus `mod-page-full-rewrite.txt`; v3.5.4 added a `PATCH-ONLY`
file; v3.7 renamed the changelog; **v3.5 and v3.5.3 shipped EMPTY directories**;
`nexus-v3.8/` contains a zip named 3.8.1; and v3.9 split in two, with the real pack
on a network share and only `UPLOAD-NOTES.txt` in the canonical home.

Every one of those passed review, because nothing asserted the shape. These tests
are that assertion.

The zip is never downloaded here: `build-nexus-pack.sh` skips the fetch when the
asset is already present, so each test pre-places a stub. That keeps the suite
offline and still exercises the completeness logic, which is the part that failed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import BASH, REPO

SCRIPT = REPO / "scripts" / "build-nexus-pack.sh"
TAG = "v9.9"
VERSION = "9.9"
ZIP = "skyrim-ai-assistant-toolkit-%s.zip" % VERSION

REQUIRED = {
    ZIP,
    "NEXUS-CHANGELOG-%s.txt" % TAG,
    "NEXUS-DESCRIPTION-%s.txt" % TAG,
    "RELEASE-MANIFEST.txt",
    "UPLOAD-NOTES.md",
}


def _run(root: Path, *args):
    env = {
        "NEXUS_PACK_ROOT": str(root).replace("\\", "/"),
        "PATH": subprocess.os.environ.get("PATH", ""),
        "SYSTEMROOT": subprocess.os.environ.get("SYSTEMROOT", ""),
        # Without SystemDrive, py.exe / the python3 Store alias create a literal `%SystemDrive%/ProgramData/...`
        # cache folder in the current directory -- the repo, under pytest (found in Plan 4 Task 11).
        "SYSTEMDRIVE": subprocess.os.environ.get("SYSTEMDRIVE", ""),
    }
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True,
                          text=True, env=env, timeout=180)


def _stage(tmp_path: Path, with_zip=True):
    """A pack home with the release asset already fetched."""
    dest = tmp_path / "_CLAUDE_OUTPUT" / ("nexus-%s" % TAG)
    dest.mkdir(parents=True)
    if with_zip:
        (dest / ZIP).write_bytes(b"PK\x03\x04stub")
    cl = tmp_path / "changelog.txt"
    cl.write_text("Fixed - a thing\nAdded - another thing\n", encoding="utf-8")
    de = tmp_path / "description.txt"
    de.write_text("[b]Toolkit[/b] page text\n", encoding="utf-8")
    return dest, cl, de


def test_a_complete_pack_has_exactly_the_required_shape(tmp_path):
    dest, cl, de = _stage(tmp_path)
    p = _run(tmp_path, TAG, str(cl), str(de))
    assert p.returncode == 0, p.stdout + p.stderr
    got = {f.name for f in dest.iterdir()}
    assert REQUIRED <= got, "missing from pack: %s" % (REQUIRED - got)
    assert not (got - REQUIRED), "unexpected strays in pack: %s" % (got - REQUIRED)


def test_it_refuses_when_the_changelog_was_never_authored(tmp_path):
    """v3.9's canonical home held only UPLOAD-NOTES.txt and nothing objected."""
    dest, _, de = _stage(tmp_path)
    p = _run(tmp_path, TAG, "", str(de))
    assert p.returncode == 1, "an unauthored changelog must not pass:\n" + p.stdout
    assert "NEXUS-CHANGELOG" in (p.stdout + p.stderr)
    # and it must leave the raw block behind to edit, not nothing at all
    assert (dest / ("NEXUS-CHANGELOG-%s.DRAFT.txt" % TAG)).exists() or "DRAFT" in p.stdout


def test_it_refuses_when_the_description_was_never_authored(tmp_path):
    dest, cl, _ = _stage(tmp_path)
    p = _run(tmp_path, TAG, str(cl))
    assert p.returncode == 1, "a missing description must not pass:\n" + p.stdout
    assert "NEXUS-DESCRIPTION" in (p.stdout + p.stderr)


def test_it_refuses_a_bare_version_that_is_not_a_tag(tmp_path):
    """`nexus-v3.8/` holds a zip named 3.8.1 because nothing tied the two together."""
    p = _run(tmp_path, VERSION)
    assert p.returncode == 1
    assert "tag like" in (p.stdout + p.stderr)


def test_it_refuses_rather_than_guessing_a_home(tmp_path):
    """The whole failure was a pack written wherever the wind was blowing. With no
    resolvable root it must stop, not improvise."""
    p = subprocess.run(
        [BASH, str(SCRIPT), TAG],
        capture_output=True, text=True, timeout=180,
        env={"NEXUS_PACK_ROOT": str(tmp_path / "does-not-exist").replace("\\", "/"),
             "PATH": subprocess.os.environ.get("PATH", ""),
             "SYSTEMROOT": subprocess.os.environ.get("SYSTEMROOT", ""),
             "SYSTEMDRIVE": subprocess.os.environ.get("SYSTEMDRIVE", "")},
    )
    assert p.returncode == 1
    assert "does not exist" in (p.stdout + p.stderr)


def test_the_manifest_records_what_was_actually_packed(tmp_path):
    dest, cl, de = _stage(tmp_path)
    p = _run(tmp_path, TAG, str(cl), str(de))
    assert p.returncode == 0, p.stdout + p.stderr
    manifest = (dest / "RELEASE-MANIFEST.txt").read_text(encoding="utf-8")
    for field in ("tag", "sha256", "size_bytes", "source", "packed_utc"):
        assert field in manifest, "manifest lacks %s" % field
    assert TAG in manifest
