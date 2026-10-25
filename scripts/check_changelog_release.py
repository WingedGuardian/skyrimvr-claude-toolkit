#!/usr/bin/env python3
"""Refuse a release whose CHANGELOG still holds work above the tagged version's section.

    python scripts/check_changelog_release.py vX.Y.Z [CHANGELOG.md]

The release workflow publishes only the tagged version's section as the release notes. Entries left under an
`## Unreleased` heading ABOVE that section are therefore shipped in the code but missing from the notes (v4.0.0
review L4a M4: a 201-line Unreleased section sat above the v4.0.0 heading). An empty Unreleased heading is fine.
The tagged version's own heading must carry its date, not "unreleased": the heading ships in the zip's CHANGELOG
and the release notes (v4.0.0 review2 C-docs L5: `## v4.0.0 — unreleased` passed).

Exit 0 = the version's section exists and nothing populated sits above it; 1 = refused (the reason is printed);
2 = bad usage or unreadable file.
"""
from __future__ import annotations

import re
import sys

HEADING = re.compile(r"^#{1,3} +(.*?)\s*$")


def problem(text: str, version: str) -> str:
    """The reason to refuse, or "" when the CHANGELOG is ready for `version` (no leading v)."""
    ver = re.escape(version)
    own = re.compile(r"^\[?v?" + ver + r"\]?([^0-9.]|$)")
    section, populated = None, set()               # the Unreleased heading we are under; those with entries
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            title = m.group(1)
            if own.match(title):
                if populated:
                    return "CHANGELOG.md has entries under '{}' above the v{} section: fold them in first".format(
                        sorted(populated)[0], version)
                if re.search(r"\bunreleased\b", title, flags=re.I):
                    return "the v{} heading still says 'unreleased' (heading: {}): date it before tagging".format(
                        version, line.strip())
                return ""
            if re.match(r"^\[?unreleased\b", title, re.I):
                section = title
            elif line.startswith("## "):
                section = None                     # another version's section is not pending work
            continue                               # a ### subheading keeps the section it is in
        if section is not None and line.strip():
            populated.add(section)
    return "CHANGELOG.md has no section for v{}".format(version)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not 1 <= len(argv) <= 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    version = argv[0][1:] if argv[0].startswith("v") else argv[0]
    try:
        with open(argv[1] if len(argv) == 2 else "CHANGELOG.md", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print("cannot read the CHANGELOG: {}".format(e), file=sys.stderr)
        return 2
    why = problem(text, version)
    if why:
        print(why)
        return 1
    print("CHANGELOG ready for v{}".format(version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
