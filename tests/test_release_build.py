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
import os
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
                      ".github/workflows/ci.yml": "name: x\n", ".gitignore": "ignored.txt\n",
                      "package.json": '{"name":"skyrim-ai-assistant-toolkit"}\n'}.items():
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
    assert z.name == "skyrim-ai-assistant-toolkit-9.9.9.zip"
    names = _names(z)
    assert {n.split("/", 1)[0] for n in names} == {"skyrim-ai-assistant-toolkit-9.9.9"}, names
    root = "skyrim-ai-assistant-toolkit-9.9.9/"
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


def test_native_fixture_authoring_is_excluded_without_hiding_runtime_or_siblings(tmp_path):
    repo = _repo(tmp_path)
    paths = ("tools/resaver-cli/tests/NativeSafetyProbe.java",
             "tools/resaver-cli/tests-extra/keep.java", "tools/resaver-cli/ResaverCLI.java")
    for rel in paths:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture source\n")
    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-q", "-m", "native fixture authoring")
    result, output = _build(repo, "HEAD", "--version", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    names = _names(_zip(output))
    root = "skyrim-ai-assistant-toolkit-1/"
    assert root + paths[0] not in names
    assert root + paths[1] in names and root + paths[2] in names


def test_maintainer_only_corpus_scripts_are_excluded_and_their_siblings_ship():
    """review L4b L1: these read tests/guard_corpus/ at import, so in the zip they could only crash."""
    spec = importlib.util.spec_from_file_location("brz_corpus", BUILDER_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.is_excluded("scripts/guard-corpus/record.py") and m.is_excluded("scripts/guard-latency.py")
    assert not m.is_excluded("scripts/gen_agent_trees.py") and not m.is_excluded("scripts/guard-latency.pyc.md")


def test_the_maintainer_superpowers_tree_is_excluded_but_cited_evidence_ships(tmp_path):
    """docs/superpowers/ (the maintainer's plans, handoffs and measurements) stays in the repo and out of the zip
    (user ruling 2026-10-20); docs/evidence/, which the shipped docs cite, still ships."""
    repo = _repo(tmp_path)
    paths = ("docs/superpowers/plans/plan.md", "docs/superpowers/measurements/m.md", "docs/evidence/m.md",
             "docs/superpowers.md")
    for rel in paths:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x\n")
    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-q", "-m", "maintainer docs")
    result, output = _build(repo, "HEAD", "--version", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    names = _names(_zip(output))
    root = "skyrim-ai-assistant-toolkit-1/"
    assert not [n for n in names if n.startswith(root + "docs/superpowers/")], names
    assert root + "docs/evidence/m.md" in names and root + "docs/superpowers.md" in names


def test_the_layer2_spike_folder_is_excluded_by_prefix_not_its_siblings(tmp_path):
    """scripts/layer2-spike/ (developer-only Layer 2 lock probes) stays in the repo and out of the zip. The rule is
    a PATH PREFIX (the folder itself, or anything under it), never a top-level name: scripts/ still ships, and a
    sibling that merely starts with the same characters (scripts/layer2-spikeX/) is not swallowed."""
    repo = _repo(tmp_path)
    for rel in ("scripts/layer2-spike/probe.py", "scripts/layer2-spike/sub/notes.md",
                "scripts/layer2-spikeX/keep.py", "scripts/layer2-spike.md", "scripts/other.py"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "spike")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr
    root = "skyrim-ai-assistant-toolkit-1/"
    names = _names(_zip(out))
    assert not [n for n in names if n.startswith(root + "scripts/layer2-spike/")], names
    for keep in ("scripts/layer2-spikeX/keep.py", "scripts/layer2-spike.md", "scripts/other.py", "README.md"):
        assert root + keep in names, (keep, names)
    spec = importlib.util.spec_from_file_location("brz_prefix", BUILDER_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.is_excluded("scripts/layer2-spike") and m.is_excluded("scripts/layer2-spike/a/b.py")
    assert not m.is_excluded("scripts/layer2-spikeX/keep.py") and not m.is_excluded("scripts/other.py")


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
        assert zf.read("skyrim-ai-assistant-toolkit-1/README.md") == b"hello\n"


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
        assert zf.read("skyrim-ai-assistant-toolkit-1/README.md") == b"hello\n"
        assert ("commit=" + first) in zf.read("skyrim-ai-assistant-toolkit-1/BUILD-INFO").decode()


def test_the_version_defaults_to_an_exact_tag(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "tag", "v4.5.6")
    r, out = _build(repo, "HEAD")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _zip(out).name == "skyrim-ai-assistant-toolkit-4.5.6.zip"


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
    assert _zip(out).name == "skyrim-ai-assistant-toolkit-2.zip"
    assert "ZIP=" in r.stdout and "COMMIT=" in r.stdout


def test_the_excludes_are_one_list_the_tests_can_read():
    spec = importlib.util.spec_from_file_location("build_release_zip", BUILDER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod.EXCLUDE) == {"tests", ".github", "scripts/layer2-spike", "tools/resaver-cli/tests",
                                "scripts/guard-corpus", "scripts/guard-latency.py",
                                "docs/superpowers"}, mod.EXCLUDE


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
    """MEASURED by the review: `$GIT_DIR/info/attributes` export-ignore silently dropped a committed file. It
    cannot be neutralised by config, so its mere presence refuses (re-review N2)."""
    repo = _repo(tmp_path)
    info = repo / ".git" / "info" / "attributes"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text("README.md export-ignore\n", encoding="utf-8")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 4 and "info/attributes" in r.stderr, r.stdout + r.stderr
    info.write_text("# a comment only\n", encoding="utf-8")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 4, r.stdout + r.stderr


def _env_with_home(tmp_path):
    home = tmp_path / "fakehome"
    (home / ".config" / "git").mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    return home, env


def test_a_configured_attributes_file_is_neutralised(tmp_path):
    """Re-review N2: a configured core.attributesFile no longer reaches the archive (it is forced empty)."""
    repo = _repo(tmp_path)
    (tmp_path / "attrs").write_text("*.md eol=crlf\nREADME.md export-ignore\n", encoding="utf-8")
    _git(repo, "config", "core.attributesFile", str(tmp_path / "attrs"))
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(_zip(out)) as zf:
        assert zf.read("skyrim-ai-assistant-toolkit-1/README.md") == b"hello\n"


def test_the_default_global_attributes_file_is_neutralised(tmp_path):
    """Re-review N2, MEASURED there: with core.attributesFile UNSET, git reads $XDG_CONFIG_HOME/git/attributes,
    and `export-ignore` there silently dropped a file (exit 0). Now: never a silently smaller zip."""
    repo = _repo(tmp_path)
    home, env = _env_with_home(tmp_path)
    (home / ".config" / "git" / "attributes").write_text("README.md export-ignore\n*.txt eol=crlf\n",
                                                          encoding="utf-8")
    out = tmp_path / "out-global"
    r = subprocess.run([sys.executable, "-B", str(BUILDER_PY), "HEAD", "--version", "1", str(out)], cwd=str(repo),
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    with zipfile.ZipFile(_zip(out)) as zf:
        assert zf.read("skyrim-ai-assistant-toolkit-1/README.md") == b"hello\n"
        assert zf.read("skyrim-ai-assistant-toolkit-1/sub/a.txt") == b"a\n"


def _mod_in(repo, monkeypatch):
    monkeypatch.chdir(repo)
    spec = importlib.util.spec_from_file_location("brz_" + repo.name, BUILDER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rezip(src: bytes, drop=None, change=None):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(buf, "w") as zout:
        for zi in zin.infolist():
            if drop and zi.filename.endswith(drop):
                continue
            data = zin.read(zi)
            if change and zi.filename.endswith(change[0]):
                data = change[1](data)
            zout.writestr(zi, data)
    return buf.getvalue()


def test_the_post_build_proof_catches_a_missing_or_altered_member(tmp_path, monkeypatch):
    """Re-review N2: whatever made the archive, the zip must be the commit's tree minus EXCLUDE, byte for byte
    (CRLF/LF aside). Each problem kind is planted on a good zip."""
    repo = _repo(tmp_path)
    _commit_file(repo, ".gitattributes", "README.md text eol=lf\n")
    r, out = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0
    good = _zip(out).read_bytes()
    m = _mod_in(repo, monkeypatch)
    sha = _git(repo, "rev-parse", "HEAD")
    name = "skyrim-ai-assistant-toolkit-1"
    assert m.prove(good, sha, name) == []
    assert m.prove(_rezip(good, change=("README.md", lambda d: d.replace(b"\n", b"\r\n"))), sha, name) == []
    assert any("missing from the zip: README.md" in p for p in m.prove(_rezip(good, drop="README.md"), sha, name))
    assert any("bytes differ" in p and "sub/a.txt" in p
               for p in m.prove(_rezip(good, change=("sub/a.txt", lambda d: b"injected\n")), sha, name))
    extra = _rezip(good)
    import io
    buf = io.BytesIO(extra)
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr(name + "/stray.txt", "x")
    assert any("not in the commit: stray.txt" in p for p in m.prove(buf.getvalue(), sha, name))


def test_the_post_build_proof_rejects_duplicate_members_even_if_last_is_correct(tmp_path, monkeypatch):
    import io
    repo = _repo(tmp_path)
    result, out = _build(repo, "HEAD", "--version", "1")
    assert result.returncode == 0, result.stderr
    m = _mod_in(repo, monkeypatch)
    sha = _git(repo, "rev-parse", "HEAD")
    good = _zip(out).read_bytes()
    corrupt_first = _rezip(good, change=("README.md", lambda data: b"wrong first member"))
    buffer = io.BytesIO(corrupt_first)
    with zipfile.ZipFile(buffer, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("skyrim-ai-assistant-toolkit-1/README.md", b"hello\n")
    assert any("duplicate" in p for p in m.prove(buffer.getvalue(), sha, "skyrim-ai-assistant-toolkit-1"))


@pytest.mark.parametrize("attributes,data", [("payload.bin -text\n", b"opaque\r\nbytes"),
                                             ("", b"undeclared\r\nbytes"),
                                             ("payload.bin text=auto eol=lf\n", b"binary\0\r\nbytes")])
def test_the_post_build_proof_does_not_normalize_binary_or_undeclared_bytes(tmp_path, monkeypatch, attributes, data):
    repo = _repo(tmp_path)
    _commit_file(repo, ".gitattributes", attributes)
    (repo / "payload.bin").write_bytes(data)
    _git(repo, "add", "--", "payload.bin")
    _git(repo, "commit", "-q", "-m", "opaque payload")
    result, out = _build(repo, "HEAD", "--version", "1")
    assert result.returncode == 0, result.stderr
    m = _mod_in(repo, monkeypatch)
    sha = _git(repo, "rev-parse", "HEAD")
    assert any("bytes differ" in p for p in m.prove(
        _rezip(_zip(out).read_bytes(), change=("payload.bin", lambda value: value.replace(b"\r\n", b"\n"))),
        sha, "skyrim-ai-assistant-toolkit-1"))


def test_a_build_whose_archive_differs_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    """The proof is wired into the build: an archive missing a file (as an unseen attribute source would make
    it) refuses with exit 5 and no zip."""
    import io
    import tarfile
    repo = _repo(tmp_path)
    m = _mod_in(repo, monkeypatch)
    real = m._archive

    def smaller(sha):
        src = tarfile.open(fileobj=io.BytesIO(real(sha)))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as dst:
            for mem in src.getmembers():
                if not mem.name.endswith("README.md"):
                    dst.addfile(mem, src.extractfile(mem) if mem.isfile() else None)
        return buf.getvalue()

    monkeypatch.setattr(m, "_archive", smaller)
    sha = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(m.Refused) as e:
        m.build(sha, "1", tmp_path / "out-proof")
    assert e.value.code == 5 and "README.md" in str(e.value)
    assert not list((tmp_path / "out-proof").glob("*.zip"))


@pytest.mark.parametrize("text,want", [
    ("residents/** text\nidentity.txt text eol=lf\n", []),            # names in a PATTERN are not attributes
    ("*.sh -ident !filter\n", []),                                      # unset / unspecified are harmless
    ("ident text\n", []),                                               # a FILE named like an attribute
    ("*.sh text ident\n", ["ident"]),
    ("*.bin filter=lfs -text\n", ["filter"]),
    ("# *.txt export-subst\n*.md export-ignore\n", ["export-ignore"]),
])
def test_gitattributes_are_parsed_not_substring_matched(text, want):
    """Re-review N5: `residents/**` contains "ident" and must not refuse a build."""
    spec = importlib.util.spec_from_file_location("brz_parse", BUILDER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.altering_attributes(text) == want


def test_a_pattern_named_like_an_attribute_still_builds(tmp_path):
    repo = _repo(tmp_path)
    _commit_file(repo, ".gitattributes", "residents/** text eol=lf\n")
    r, _ = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr


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


def test_an_autocrlf_checkout_under_our_attributes_is_not_a_dirty_tree(tmp_path):
    """CI run 38178385999 (windows-latest): the runner checks out with core.autocrlf=true, and the four files no
    .gitattributes rule covered (.gitignore, LICENSE, SETUP_PROMPT.txt, tests/guard_corpus/game-root.txt) came out
    CRLF. The builder's `git status` runs with core.autocrlf=false, so once git re-read them they were "modified"
    and the release build refused a pristine checkout as dirty. Reproduced here on any OS: OUR .gitattributes, an
    autocrlf checkout, then fresh mtimes so status must re-read every file instead of trusting the index stat."""
    repo = _repo(tmp_path)
    extra = {"LICENSE": "MIT\nline two\n", "SETUP_PROMPT.txt": "a\nb\n", "tests/guard_corpus/game-root.txt": "x\ny\n",
             "docs/NOTES": "an extensionless\ntext file\n"}
    for rel, text in extra.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    (repo / ".gitattributes").write_bytes((REPO / ".gitattributes").read_bytes())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "our attributes")
    tracked = _git(repo, "ls-files").splitlines()
    for rel in tracked:
        (repo / rel).unlink()
    subprocess.run(["git", "-c", "core.autocrlf=true", "checkout", "-f", "--", "."], cwd=str(repo),
                   capture_output=True, text=True, timeout=60, check=True)
    later = os.stat(repo / ".git" / "index").st_mtime + 10
    for rel in tracked:
        os.utime(repo / rel, (later, later))
    r, _ = _build(repo, "HEAD", "--version", "1")
    assert r.returncode == 0, r.stdout + r.stderr


def test_archive_name_comes_from_the_selected_commit_not_the_producer_checkout(tmp_path):
    repo = _repo(tmp_path)
    _commit_file(repo, "package.json", '{"name":"historical-slug"}\n')
    ancestor = _git(repo, "rev-parse", "HEAD")
    _commit_file(repo, "package.json", '{"name":"future-slug"}\n')
    result, out = _build(repo, ancestor, "--version", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    archive = _zip(out)
    assert archive.name == "historical-slug-1.zip", archive.name
    with zipfile.ZipFile(archive) as z:
        assert z.read("historical-slug-1/package.json") == b'{"name":"historical-slug"}\n'


def test_the_real_ci_build_step_keeps_its_log_outside_the_checkout(tmp_path):
    import yaml
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    script = next(s["run"] for s in workflow["jobs"]["release-build"]["steps"]
                  if s.get("name") == "Build the zip from the commit")
    repo = _repo(tmp_path)
    for filename in ("build-release-zip.sh", "build_release_zip.py"):
        _commit_file(repo, "scripts/" + filename, (REPO / "scripts" / filename).read_text())
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    env_file = temp / "github-env"
    env_file.touch()
    env = dict(os.environ, RUNNER_TEMP=temp.as_posix(), GITHUB_ENV=env_file.as_posix(),
               PYTHON=sys.executable.replace("\\", "/"))
    result = subprocess.run([BASH, "-c", script], cwd=repo, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    assert Path(values["ZIP"]).is_file() and values["COMMIT"] == _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "status", "--porcelain") == "", "the workflow itself dirtied the checkout"


@pytest.mark.parametrize("identity", [None, "not json", "{}", '{"name":null}', '{"name":""}',
                                    '{"name":"../outside"}'],
                         ids=["missing", "invalid-json", "missing-name", "nonstring", "empty", "traversal"])
def test_a_missing_or_invalid_committed_product_slug_refuses(tmp_path, identity):
    repo = _repo(tmp_path)
    if identity is not None:
        _commit_file(repo, "package.json", identity)
    else:
        _git(repo, "rm", "package.json")
        _git(repo, "commit", "-q", "-m", "fixture without identity")
    result, out = _build(repo, "HEAD", "--version", "1")
    assert result.returncode == 4 and not list(out.glob("*.zip")), result.stdout + result.stderr


def test_a_submodule_refuses_the_build(tmp_path):
    """A gitlink shipped as an empty folder with exit 0."""
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo", "160000,{},mod".format(sha))
    _git(repo, "commit", "-q", "-m", "gitlink")
    r, _ = _build(repo, "HEAD", "--version", "1", "--allow-dirty")
    assert r.returncode == 4 and "submodule" in r.stderr, r.stdout + r.stderr
