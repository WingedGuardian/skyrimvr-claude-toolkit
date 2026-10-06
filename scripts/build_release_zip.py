#!/usr/bin/env python3
"""Build the release zip from COMMITTED git objects -- the one build the workflow and a local release share.

    python scripts/build_release_zip.py <rev> <outdir> [--version V] [--allow-dirty]
    bash scripts/build-release-zip.sh   <rev> <outdir> [--version V] [--allow-dirty]

Run inside the repo. The zip is `git archive <rev>` minus EXCLUDE, under one root folder
`<NAME_PREFIX>-<version>/`, plus a BUILD-INFO file naming the commit. Nothing that is not in the commit can
reach it: not an untracked file, not an ignored one, not an uncommitted edit. (The old build rsync'ed the
checkout, so any of those went to every user.)

Refusals:
  exit 3  the working tree is dirty (`git status --porcelain` lists something). The tree is then not the
          commit being shipped, and whoever reviewed "the tree" reviewed something else. --allow-dirty
          waives this one check (local experiments only; CI never passes it). The zip is still built from
          the commit, never from the tree.
  exit 4  <rev> is unknown, is not HEAD or an ancestor of HEAD (a build of a commit nobody has checked out
          here), or the commit carries its own BUILD-INFO (a tree must not vouch for itself).
  exit 2  bad usage.

Deterministic: entries in git's order, timestamps from the commit, fixed compression. Two builds of one
commit on one machine are byte-identical; across machines compare member hashes (zlib may differ).
Line endings are git's with core.autocrlf off, so .gitattributes alone decides them, whoever builds.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

NAME_PREFIX = "skyrimvr-claude-toolkit"
# Top-level paths that never ship. One list: the payload checker asserts their absence on the zip.
# tests/ holds tests/fixtures/routes-harness/ among others; .github/ is CI.
EXCLUDE = ("tests", ".github")
BUILD_INFO = "BUILD-INFO"


class Refused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _git(*args, check=True, text=True):
    r = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", *args],
                       capture_output=True, text=text, timeout=300)
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


def default_version(sha: str) -> str:
    r = _git("describe", "--tags", "--exact-match", sha, check=False)
    tag = r.stdout.strip() if r.returncode == 0 else ""
    return tag[1:] if tag.startswith("v") else (tag or "dev-" + sha[:12])


def build(sha: str, version: str, outdir: Path) -> Path:
    name = "{}-{}".format(NAME_PREFIX, version)
    pathspec = ["."] + [":(exclude){}".format(e) for e in EXCLUDE]
    tar_bytes = _git("archive", "--format=tar", sha, "--", *pathspec, text=False).stdout
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
    out.write_bytes(buf.getvalue())
    return out


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
