#!/usr/bin/env python3
"""Generate GUARD-FILES.sha256: the sha256 of every guard file this release ships (Plan 6 Task 12, R12).

    python scripts/gen-guard-hashes.py              write GUARD-FILES.sha256 at the repo root
    python scripts/gen-guard-hashes.py --check      exit 1 when the committed file differs from a fresh one (CI)
    python scripts/gen-guard-hashes.py --verify DIR exit 1 when a file the manifest in DIR names is missing there or
                                                    hashes differently (the release payload, before it is zipped)

At every session start tools/skyguard/session.py hashes the files this manifest names and banners the ones that differ
(`{n} guard files differ from release {ver} ...`), so an edit to the guards -- the user's, or an agent's that the
S-SELF-GUARD rule let through -- is visible in the next session whatever route made it.

WHICH FILES: every TRACKED file whose path is a guard file by self_rules.install_kind -- the same definition the rule
uses, so the two can never disagree -- except the files setup.sh rewrites on every install (SETUP_WRITES), whose shipped
hash would differ on every machine and turn the banner into noise.

WHICH BYTES: the working-tree file, as checked out. .gitattributes pins every guard file type's line endings (eol=lf;
the Codex .ps1 entry eol=crlf), so a Windows checkout, the Linux CI checkout and the rsync-built release zip all hold
the same bytes, and those are the bytes a user extracts.

Format (session.py's reader): `# release <ver>`, then `<sha256>  <relative path>` per file, sorted, LF line endings.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = "GUARD-FILES.sha256"
SETUP_WRITES = frozenset((".codex/hooks.json",))     # setup.sh fills in the install root (setup.sh: CODEX_DEFS)
_VERSION = re.compile(r"^#{1,3} .*?\bv?(\d+\.\d+\.\d+)\b", re.M)


def _install_kind():
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import self_rules
    return self_rules.install_kind, self_rules.GUARD


def release(repo: Path = REPO) -> str:
    """The version the CHANGELOG's newest heading names (`## Unreleased (v4.0.0 in progress)` -> 4.0.0)."""
    m = _VERSION.search((repo / "CHANGELOG.md").read_text(encoding="utf-8"))
    return m.group(1) if m else "dev"


def guard_files(repo: Path = REPO):
    kind, guard = _install_kind()
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
    names = [n for n in out.decode("utf-8").split("\0") if n]
    return sorted(n for n in names if n not in SETUP_WRITES and kind(n.lower().split("/")) == guard)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(repo: Path = REPO) -> str:
    lines = ["# release " + release(repo)]
    lines += ["%s  %s" % (_sha(repo / n), n) for n in guard_files(repo)]
    return "\n".join(lines) + "\n"


def verify(root: Path) -> list:
    """Problems with the manifest in `root`: a named file that is missing there, or hashes differently."""
    problems = []
    text = (root / MANIFEST).read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        digest, rel = line.split("  ", 1)
        f = root / rel
        if not f.is_file():
            problems.append("missing: " + rel)
        elif _sha(f) != digest:
            problems.append("differs: " + rel)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="fail when the committed manifest is stale")
    g.add_argument("--verify", type=Path, metavar="DIR", help="check a payload folder against its own manifest")
    a = ap.parse_args(argv)
    if a.verify:
        problems = verify(a.verify)
        for p in problems:
            print("GUARD-FILES: " + p)
        print("GUARD-FILES: %s" % ("FAIL" if problems else "ok"))
        return 1 if problems else 0
    fresh = render()
    target = REPO / MANIFEST
    if a.check:
        have = target.read_text(encoding="utf-8") if target.is_file() else ""
        if have != fresh:
            old, new = set(have.splitlines()), set(fresh.splitlines())
            for line in sorted(old - new):
                print("- " + line)
            for line in sorted(new - old):
                print("+ " + line)
            print("GUARD-FILES.sha256 is stale: run python scripts/gen-guard-hashes.py and commit it.")
            return 1
        print("GUARD-FILES.sha256 is current (%d files)." % (len(fresh.splitlines()) - 1))
        return 0
    target.write_bytes(fresh.encode("utf-8"))
    print("wrote %s (%d files)" % (MANIFEST, len(fresh.splitlines()) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
