#!/usr/bin/env python3
"""Build the release zip from COMMITTED git objects -- the one build the workflow and a local release share.

    python scripts/build_release_zip.py <rev> <outdir> [--version V] [--allow-dirty]
    bash scripts/build-release-zip.sh   <rev> <outdir> [--version V] [--allow-dirty]

Run inside the repo. The zip is `git archive <rev>` minus EXCLUDE, under one root folder
`<product-slug>-<version>/`, plus a BUILD-INFO file naming the commit. Nothing that is not in the commit can
reach it: not an untracked file, not an ignored one, not an uncommitted edit. (The old build rsync'ed the
checkout, so any of those went to every user.)

Refusals:
  exit 3  the working tree is dirty (`git status --porcelain` lists something). The tree is then not the
          commit being shipped, and whoever reviewed "the tree" reviewed something else. --allow-dirty
          waives this one check (local experiments only; CI never passes it). The zip is still built from
          the commit, never from the tree.
  exit 4  <rev> is unknown, is not HEAD or an ancestor of HEAD (a build of a commit nobody has checked out
          here), or the commit carries its own BUILD-INFO (a tree must not vouch for itself); or something would
          make the archive differ from the committed bytes: machine-local attributes (core.attributesFile,
          $GIT_DIR/info/attributes), a committed filter/export-subst/export-ignore/ident attribute, or a
          submodule (it would ship as an empty folder).
  exit 5  the post-build proof failed: the zip's file list is not the commit's tree minus EXCLUDE, or a member's
          bytes are not its blob's (beyond the committed CRLF/LF rule). Nothing is written.
  exit 2  bad usage.

Deterministic: entries in git's order, timestamps from the commit, fixed compression. Two builds of one
commit on one machine are byte-identical; across machines compare member hashes (zlib may differ).
Line endings are git's with core.autocrlf off, so .gitattributes alone decides them, whoever builds.
"""
from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

# Repo-relative path PREFIXES that never ship: the path itself, or anything under it (is_excluded) -- never a bare
# top-level name match, so excluding scripts/layer2-spike leaves the rest of scripts/ shipping. One list: the
# payload checker asserts their absence on the zip. tests/ holds tests/fixtures/routes-harness/ among others;
# .github/ is CI; scripts/layer2-spike/ is the Plan 6 Layer 2 lock research probes (developer-only, kept in the repo).
# Native fixture authors are also maintainer-only. Exclude them explicitly here,
# never through export-ignore: the archive proof must account for every omission.
# scripts/guard-corpus/ and scripts/guard-latency.py read tests/guard_corpus/ (which never ships), so in a zip they
# could only crash: maintainer-only, kept in the repo (review L4b L1).
EXCLUDE = ("tests", ".github", "scripts/layer2-spike", "tools/resaver-cli/tests", "scripts/guard-corpus",
           "scripts/guard-latency.py")


def is_excluded(path: str) -> bool:
    """True when a repo-relative POSIX path is an EXCLUDE entry or lies under one."""
    return any(path == e or path.startswith(e + "/") for e in EXCLUDE)
BUILD_INFO = "BUILD-INFO"


class Refused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


_EMPTY_ATTRS = None


def _empty_attributes_file() -> str:
    """An empty file for core.attributesFile, so neither a configured one nor the DEFAULT global file
    ($XDG_CONFIG_HOME/git/attributes, ~/.config/git/attributes -- used when the key is unset) is read
    (re-review N2: MEASURED there, a default global `CHANGELOG.md export-ignore` dropped the file, exit 0).
    The tool's own temp file, removed by the tool at exit (the stated exception to the Recycle-Bin rule)."""
    global _EMPTY_ATTRS
    if _EMPTY_ATTRS is None:
        fd, _EMPTY_ATTRS = tempfile.mkstemp(prefix="build-release-attrs-")
        os.close(fd)
        atexit.register(lambda p=_EMPTY_ATTRS: os.path.exists(p) and os.remove(p))
    return _EMPTY_ATTRS


def _git(*args, check=True, text=True, stdin=None):
    # Every git call is independent of this machine's attributes: the system file is off
    # (GIT_ATTR_NOSYSTEM), the global one is an empty file, line endings are git's committed rules only.
    env = dict(os.environ, GIT_ATTR_NOSYSTEM="1")
    r = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                        "-c", "core.attributesFile=" + _empty_attributes_file().replace("\\", "/"), *args],
                       capture_output=True, text=text, timeout=300, env=env, input=stdin)
    if check and r.returncode != 0:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise Refused(4, "git {} failed: {}".format(" ".join(args[:2]), err.strip()))
    return r


def resolve(rev: str) -> str:
    r = _git("rev-parse", "--verify", "--quiet", rev + "^{commit}", check=False)
    if r.returncode != 0 or not r.stdout.strip():
        raise Refused(4, "unknown revision: {}".format(rev))
    return r.stdout.strip()


def check_tree(sha: str, allow_dirty: bool) -> None:
    dirty = _git("status", "--porcelain", "--untracked-files=all").stdout.splitlines()
    if dirty and not allow_dirty:
        raise Refused(3, "the working tree is dirty; commit or stash first (or --allow-dirty for a local "
                         "experiment -- the zip is still built from the commit):\n  " + "\n  ".join(dirty[:40]))
    if _git("merge-base", "--is-ancestor", sha, "HEAD", check=False).returncode != 0:
        raise Refused(4, "{} is not HEAD or an ancestor of HEAD; check out the commit you mean to ship".format(sha))
    if _git("cat-file", "-e", "{}:{}".format(sha, BUILD_INFO), check=False).returncode == 0:
        raise Refused(4, "the commit carries its own {}: a tree must not vouch for itself".format(BUILD_INFO))


# Attributes that make `git archive` output something other than the committed bytes: a filter (LFS, any
# smudge) injects content, export-subst expands $Format:...$ (the author's e-mail among them), export-ignore
# drops files silently, ident rewrites $Id$. `text`/`eol` stay allowed: they are deterministic and committed.
ALTERING_ATTRS = ("filter", "export-subst", "export-ignore", "ident")


def altering_attributes(text: str):
    """The ALTERING_ATTRS a .gitattributes text SETS, parsed as git does (re-review N5): a line is a pattern
    then whitespace-separated attributes; `-x` unsets and `!x` unspecifies (both harmless); `x` or `x=v` sets.
    A pattern that merely contains a name (`residents/**`) is not an attribute."""
    found = set()
    for ln in text.splitlines():
        parts = ln.split()
        if not parts or parts[0].startswith("#"):
            continue
        for attr in parts[1:]:
            # `-x` (unset) and `!x` (unspecified) never equal a name, so only a SET attribute matches
            if attr.split("=", 1)[0] in ALTERING_ATTRS:
                found.add(attr.split("=", 1)[0])
    return sorted(found)


def check_archive_inputs(sha: str) -> None:
    """Refuse anything OUTSIDE the commit that would change the archive, and anything in it a zip cannot carry.

    Review M1/M2 (MEASURED there, each exit 0 before): `$GIT_DIR/info/attributes` with export-ignore dropped a
    committed file, with eol=crlf rewrote one, and a configured smudge filter injected arbitrary content; a
    gitlink shipped as an empty folder."""
    # core.attributesFile and the system file are neutralised in _git(); $GIT_DIR/info/attributes cannot be,
    # so its mere presence refuses (re-review N2).
    info = Path(_git("rev-parse", "--git-path", "info/attributes").stdout.strip())
    if info.exists():
        raise Refused(4, "{} exists: machine-local attributes would alter the archive".format(info.as_posix()))
    tree = _git("ls-tree", "-r", "--full-tree", sha).stdout.splitlines()
    links = [ln.split("\t", 1)[-1] for ln in tree if ln.startswith("160000 ")]
    if links:
        raise Refused(4, "submodule(s) in the commit ({}): they would ship as empty folders".format(", ".join(links)))
    for ln in tree:
        path = ln.split("\t", 1)[-1]
        if path == ".gitattributes" or path.endswith("/.gitattributes"):
            bad = altering_attributes(_git("show", "{}:{}".format(sha, path)).stdout)
            if bad:
                raise Refused(4, "{} uses {}: the archive would not be the committed bytes".format(path, ", ".join(bad)))


def default_version(sha: str) -> str:
    r = _git("describe", "--tags", "--exact-match", sha, check=False)
    tag = r.stdout.strip() if r.returncode == 0 else ""
    return tag[1:] if tag.startswith("v") else (tag or "dev-" + sha[:12])


def release_slug(sha: str) -> str:
    """Read product identity from the selected commit, like every archive member."""
    try:
        name = json.loads(_git("show", "{}:package.json".format(sha)).stdout)["name"]
    except (ValueError, KeyError, TypeError):
        raise Refused(4, "the committed package.json has no readable product name")
    if not isinstance(name, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        raise Refused(4, "the committed product name must be a lowercase, dash-separated slug")
    return name


def build(sha: str, version: str, outdir: Path) -> Path:
    name = "{}-{}".format(release_slug(sha), version)
    tar_bytes = _archive(sha)
    when = int(_git("show", "-s", "--format=%ct", sha).stdout.strip())
    stamp = _zip_time(when)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / (name + ".zip")
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf, \
            zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _dir(zf, name + "/", stamp)
        for m in tf.getmembers():
            if m.name == "pax_global_header" or m.type == tarfile.XGLTYPE:
                continue
            arc = name + "/" + m.name.rstrip("/")
            if m.isdir():
                _dir(zf, arc + "/", stamp)
            elif m.isfile():
                zi = zipfile.ZipInfo(arc, date_time=stamp)
                zi.external_attr = (0o100000 | (m.mode & 0o777)) << 16
                zi.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zi, tf.extractfile(m).read(), compresslevel=9)
            elif m.issym():
                raise Refused(4, "symlink in the commit ({}): a zip cannot carry it faithfully".format(m.name))
        info = "commit={}\nversion={}\nbuilt-from=git archive (committed objects only)\n".format(sha, version)
        zi = zipfile.ZipInfo(name + "/" + BUILD_INFO, date_time=stamp)
        zi.external_attr = (0o100644) << 16
        zi.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(zi, info.encode("utf-8"), compresslevel=9)
    problems = prove(buf.getvalue(), sha, name)
    if problems:
        raise Refused(5, "the zip is NOT the commit's files ({} problem(s)); nothing written:\n  {}".format(
            len(problems), "\n  ".join(problems[:40])))
    out.write_bytes(buf.getvalue())
    return out


def _archive(sha: str) -> bytes:
    pathspec = ["."] + [":(exclude,literal){}".format(e) for e in EXCLUDE]   # literal: a prefix, never a glob
    return _git("archive", "--format=tar", sha, "--", *pathspec, text=False).stdout


def expected_files(sha: str):
    """{path: blob id} of the commit, minus the documented release exclusions (EXCLUDE)."""
    out = {}
    for ln in _git("ls-tree", "-r", "--full-tree", sha).stdout.splitlines():
        meta, path = ln.split("\t", 1)
        if not is_excluded(path):
            out[path] = meta.split()[2]
    return out


def _blobs(oids):
    """{oid: bytes} through one `git cat-file --batch`."""
    uniq = sorted(set(oids))
    raw = _git("cat-file", "--batch", text=False, stdin=("\n".join(uniq) + "\n").encode()).stdout
    out, i = {}, 0
    for oid in uniq:
        nl = raw.index(b"\n", i)
        head = raw[i:nl].split()
        size = int(head[2])
        out[oid] = raw[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
    return out


def prove(zip_bytes: bytes, sha: str, name: str):
    """Post-build proof (re-review N2), independent of how the archive was made: the zip's file list is the
    commit's tree minus EXCLUDE (plus BUILD-INFO), and every member's bytes are its blob's -- the only change
    allowed is a declared text line-ending rule (CRLF <-> LF). Duplicate members and binary conversions
    refuse. Returns the problems (empty = proven)."""
    want = expected_files(sha)
    got = {}
    problems, seen = [], set()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for zi in zf.infolist():
            identity = zi.filename.rstrip("/")
            if identity in seen:
                problems.append("duplicate zip member: " + zi.filename)
            seen.add(identity)
            if zi.is_dir():
                continue
            rel = zi.filename[len(name) + 1:] if zi.filename.startswith(name + "/") else None
            if rel is None:
                got["<outside the root> " + zi.filename] = b""
            elif rel != BUILD_INFO:
                got[rel] = zf.read(zi)
    problems += ["missing from the zip: " + p for p in sorted(set(want) - set(got))]
    problems += ["not in the commit: " + p for p in sorted(set(got) - set(want))]
    blobs = _blobs(want[p] for p in want if p in got)
    raw = _git("check-attr", "--source", sha, "-z", "--stdin", "text", "eol", text=False,
               stdin=b"".join(p.encode("utf-8") + b"\0" for p in sorted(set(want) & set(got)))).stdout.split(b"\0")
    attributes = {}
    for index in range(0, len(raw) - 2, 3):
        attributes.setdefault(raw[index].decode("utf-8"), {})[raw[index + 1]] = raw[index + 2]
    for p in sorted(set(want) & set(got)):
        original = blobs[want[p]]
        if got[p] == original:
            continue
        attrs = attributes.get(p, {})
        text = (attrs.get(b"text") != b"unset" and
                (attrs.get(b"text") in (b"set", b"auto") or attrs.get(b"eol") in (b"lf", b"crlf")))
        if (text and b"\0" not in got[p] and b"\0" not in original and
                got[p].replace(b"\r\n", b"\n") == original.replace(b"\r\n", b"\n")):
            continue
        problems.append("bytes differ from the committed blob: " + p)
    return problems


def _zip_time(epoch: int):
    import time
    t = time.gmtime(max(epoch, 315532800))   # zip cannot store a date before 1980
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def _dir(zf, arc, stamp):
    zi = zipfile.ZipInfo(arc, date_time=stamp)
    zi.external_attr = (0o040755 << 16) | 0x10
    zf.writestr(zi, b"")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rev")
    ap.add_argument("outdir")
    ap.add_argument("--version", default=None)
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args(argv)
    try:
        sha = resolve(a.rev)
        check_tree(sha, a.allow_dirty)
        check_archive_inputs(sha)
        version = a.version or default_version(sha)
        if not version or "/" in version or "\\" in version or version.startswith("."):
            raise Refused(2, "bad --version: {!r}".format(version))
        out = build(sha, version, Path(a.outdir))
    except Refused as e:
        sys.stderr.write("build-release-zip: REFUSED: {}\n".format(e))
        return e.code
    print("ZIP={}".format(out.as_posix()))
    print("COMMIT={}".format(sha))
    print("VERSION={}".format(version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
