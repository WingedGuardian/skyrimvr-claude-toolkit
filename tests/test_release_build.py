"""The release zip is built from COMMITTED git objects only (Plan 8 T10.1).

The old build was `rsync -a --exclude ...` of the checkout, so an untracked file, an ignored file or an
uncommitted edit on the machine that built it went to every user. scripts/build-release-zip.sh builds from
`git archive <rev>`: nothing outside the commit can reach the zip. It refuses a dirty tree (the tree is
then not what was reviewed) and a rev that is not HEAD or an ancestor of it, and it records the commit
in BUILD-INFO at the zip root, so the verifier can prove which commit an artifact came from.

Each test builds a tiny throwaway repo, never this one: the builder must not depend on our tree, and the
mutation harness copies the repo without .git.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import BASH, REPO

BUILDER_SH = REPO / "scripts" / "build-release-zip.sh"
BUILDER_PY = REPO / "scripts" / "build_release_zip.py"


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          cwd=str(repo), capture_output=True, text=True, timeout=60, check=True).stdout.strip()


def _repo(tmp_path):
    r = tmp_path / "src"
    for rel, text in {"README.md": "hello\n", "setup.sh": "#!/usr/bin/env bash\necho setup\n",
                      "sub/a.txt": "a\n", "tests/test_x.py": "def test(): pass\n",
                      ".github/workflows/ci.yml": "name: x\n", ".gitignore": "ignored.txt\n"}.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    _git(r, "init", "-q", "-b", "master")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one")
    return r


def _build(repo, *args, out=None):
    out = out or repo.parent / "out"
    r = subprocess.run([sys.executable, "-B", str(BUILDER_PY), *args, str(out)], cwd=str(repo),
                       capture_output=True, text=True, timeout=120)
    return r, out


def _zip(out):
    zips = sorted(Path(out).glob("*.zip"))
    assert len(zips) == 1, zips
    return zips[0]


def _names(z):
    with zipfile.ZipFile(z) as zf:
        return zf.namelist()


def test_a_clean_build_has_one_versioned_root_and_build_info(tmp_path):
    repo = _repo(tmp_path)
    r, out = _build(repo, "HEAD", "--version", "9.9.9")
    assert r.returncode == 0, r.stdout + r.stderr
    z = _zip(out)
    assert z.name == "skyrimvr-claude-toolkit-9.9.9.zip"
    names = _names(z)
    assert {n.split("/", 1)[0] for n in names} == {"skyrimvr-claude-toolkit-9.9.9"}, names
    root = "skyrimvr-claude-toolkit-9.9.9/"
    assert root + "README.md" in names and root + "sub/a.txt" in names and root + "setup.sh" in names
    with zipfile.ZipFile(z) as zf:
        info = zf.read(root + "BUILD-INFO").decode()
    assert "commit=" + _git(repo, "rev-parse", "HEAD") + "\n" in info, info
    assert "version=9.9.9\n" in info, info


def test_tests_and_github_are_not_shipped(tmp_path):
    repo = _repo(tmp_path)
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr
    names = _names(_zip(out))
    assert not [n for n in names if "/tests/" in n or "/.github/" in n], names


def test_an_untracked_file_refuses_the_build_and_never_reaches_the_zip(tmp_path):
    repo = _repo(tmp_path)
    (repo / "stray.txt").write_text("not committed\n")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "stray.txt" in r.stdout + r.stderr and not list(Path(out).glob("*.zip"))
    r, out = _build(repo, "HEAD", "--version", "1", "--allow-dirty")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [n for n in _names(_zip(out)) if n.endswith("stray.txt")]


def test_an_uncommitted_edit_refuses_and_the_zip_carries_the_committed_bytes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("EDITED\n")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 3, r.stdout + r.stderr
    r, out = _build(repo, "HEAD", "--version", "1", "--allow-dirty")
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(_zip(out)) as zf:
        assert zf.read("skyrimvr-claude-toolkit-1/README.md") == b"hello\n"


def test_an_ignored_file_does_not_count_as_dirty_and_is_not_shipped(tmp_path):
    repo = _repo(tmp_path)
    (repo / "ignored.txt").write_text("x\n")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [n for n in _names(_zip(out)) if n.endswith("ignored.txt")]


def test_a_rev_that_is_not_an_ancestor_of_head_is_refused(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.txt").write_text("s\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side")
    side = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "master")
    r, out = _build(repo, side, "--version", "1")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "ancestor" in r.stdout + r.stderr
    # ...and --allow-dirty does not waive it: it is about what was committed, not about the tree.
    r, _ = _build(repo, side, "--version", "1", "--allow-dirty")
    assert r.returncode == 4, r.stdout + r.stderr


def test_an_unknown_rev_is_refused(tmp_path):
    repo = _repo(tmp_path)
    r, _ = _build(repo, "no-such-rev", "--version", "1")
    assert r.returncode == 4, r.stdout + r.stderr


def test_an_older_ancestor_builds_its_own_bytes(tmp_path):
    repo = _repo(tmp_path)
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "two")
    r, out = _build(repo, first, "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(_zip(out)) as zf:
        assert zf.read("skyrimvr-claude-toolkit-1/README.md") == b"hello\n"
        assert ("commit=" + first) in zf.read("skyrimvr-claude-toolkit-1/BUILD-INFO").decode()


def test_the_version_defaults_to_an_exact_tag(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "tag", "v4.5.6")
    r, out = _build(repo, "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _zip(out).name == "skyrimvr-claude-toolkit-4.5.6.zip"


def test_two_builds_of_one_commit_are_byte_identical(tmp_path):
    repo = _repo(tmp_path)
    a, out_a = _build(repo, "HEAD", "--version", "1", out=tmp_path / "a")
    b, out_b = _build(repo, "HEAD", "--version", "1", out=tmp_path / "b")
    assert a.returncode == b.returncode == 0
    assert _zip(out_a).read_bytes() == _zip(out_b).read_bytes()


def test_the_build_info_is_a_file_the_commit_cannot_supply(tmp_path):
    """A committed BUILD-INFO would let a tree vouch for itself: the builder refuses one."""
    repo = _repo(tmp_path)
    (repo / "BUILD-INFO").write_text("commit=" + "0" * 40 + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "forged")
    r, _ = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "BUILD-INFO" in r.stdout + r.stderr


def test_the_shell_entry_runs_the_same_builder(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "sh-out"
    r = subprocess.run([BASH, str(BUILDER_SH), "HEAD", str(out), "--version", "2"], cwd=str(repo),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _zip(out).name == "skyrimvr-claude-toolkit-2.zip"
    assert "ZIP=" in r.stdout and "COMMIT=" in r.stdout


def test_the_excludes_are_one_list_the_tests_can_read():
    spec = importlib.util.spec_from_file_location("build_release_zip", BUILDER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod.EXCLUDE) == {"tests", ".github"}, mod.EXCLUDE


@pytest.mark.parametrize("args", [[], ["HEAD"], ["HEAD", "out", "--bogus"]])
def test_bad_usage_is_exit_2(tmp_path, args):
    r = subprocess.run([sys.executable, "-B", str(BUILDER_PY), *args], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr


# ------------------------------------------------------------------ fix round 1 (review M1, M2)

def _commit_file(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add " + rel)


def test_machine_local_info_attributes_refuse_the_build(tmp_path):
    """MEASURED by the review: `$GIT_DIR/info/attributes` export-ignore silently dropped a committed file."""
    repo = _repo(tmp_path)
    info = repo / ".git" / "info" / "attributes"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("README.md export-ignore\n", encoding="utf-8")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 4 and "info/attributes" in r.stderr, r.stdout + r.stderr
    info.write_text("# a comment only\n\n", encoding="utf-8")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_configured_attributes_file_refuses_the_build(tmp_path):
    repo = _repo(tmp_path)
    (tmp_path / "attrs").write_text("*.md eol=crlf\n", encoding="utf-8")
    _git(repo, "config", "core.attributesFile", str(tmp_path / "attrs"))
    r, _ = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 4 and "core.attributesFile" in r.stderr, r.stdout + r.stderr


@pytest.mark.parametrize("attr", ["*.md filter=lfs diff=lfs merge=lfs -text", "*.txt export-subst",
                                  "sub/* export-ignore", "*.sh ident"])
def test_a_committed_attribute_that_alters_the_archive_refuses(tmp_path, attr):
    """A filter (LFS or any smudge) injects content; export-subst expands $Format$ (the author's e-mail among
    them); export-ignore drops files; ident rewrites $Id$."""
    repo = _repo(tmp_path)
    _commit_file(repo, ".gitattributes", "*.sh text eol=lf\n" + attr + "\n")
    r, _ = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 4 and ".gitattributes" in r.stderr, (attr, r.stdout + r.stderr)


def test_plain_eol_attributes_still_build(tmp_path):
    repo = _repo(tmp_path)
    _commit_file(repo, ".gitattributes", "# eol only\n*.sh text eol=lf\n*.png binary\n")
    r, _ = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_submodule_refuses_the_build(tmp_path):
    """A gitlink shipped as an empty folder with exit 0."""
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo", "160000,{},mod".format(sha))
    _git(repo, "commit", "-q", "-m", "gitlink")
    r, _ = _build(repo, "HEAD", "--version", "1", "--allow-dirty")
    assert r.returncode == 4 and "submodule" in r.stderr, r.stdout + r.stderr
