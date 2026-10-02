"""`echo "$TEXT" | grep -qE PATTERN`, reproduced: a pattern matches if it matches ANY LINE.

grep is line-oriented, and its [[:space:]] never crosses a newline. Python's \\s does, so every ported
pattern is applied to each line separately. Patterns are written in Python syntax by hand; this
module does not translate POSIX syntax.

ASCII semantics (re.ASCII): the bash hooks' grep treats \\s, \\b and case folding as ASCII-only, while
Python's Unicode defaults would match NBSP as \\s and the Kelvin sign as "k".
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def _compile(pattern: str, icase: bool):
    return re.compile(pattern, re.ASCII | (re.IGNORECASE if icase else 0))


def grep(pattern: str, text: str, icase: bool = True) -> bool:
    rx = _compile(pattern, icase)
    # Controller ruling (Task 4 fix round 1): Windows Git-Bash grep is the reference. It treats CR before
    # LF as part of the line ending; MEASURED there, `$` matches after one, two or three trailing CRs
    # (`printf 'a.txt\r\r\n' | grep -qiE 'txt$'` exits 0) but not a CR followed by more text. Linux GNU grep
    # does not do this. So every trailing CR is dropped from each line before matching.
    return any(rx.search(line.rstrip("\r")) for line in text.split("\n"))
