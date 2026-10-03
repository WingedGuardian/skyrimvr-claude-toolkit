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


def split_dotstar(pattern: str):
    """(first, then) when `pattern` is `first.*then` with ONE top-level `.*` and no top-level `|`, else None.

    `A.*B` tried from every A start is quadratic on a line full of A's (`rm rm rm ...`, 6666 starts x 20 KB
    measured at 2.8 s), and a hook that answers after its timeout has its refusal dropped. It is equivalent to
    "A matches, then B matches at or after A's first end", which is linear (see grep).
    """
    depth, cls, i, cut = 0, False, 0, -1
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if cls:
            cls = ch != "]"
        elif ch == "[":
            cls = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            if ch == "|":
                return None
            if ch == "." and pattern[i + 1:i + 2] == "*":
                if cut >= 0:
                    return None
                cut = i
        i += 1
    if cut <= 0 or cut + 2 >= len(pattern):
        return None
    return pattern[:cut], pattern[cut + 2:]


@lru_cache(maxsize=None)
def _plan(pattern: str, icase: bool):
    parts = split_dotstar(pattern)
    if parts is None:
        return (_compile(pattern, icase), None)
    return (_compile(parts[0], icase), _compile(parts[1], icase))


def _line_matches(first, then, line: str) -> bool:
    m = first.search(line)
    if m is None:
        return False
    return then is None or then.search(line, m.end()) is not None


def grep(pattern: str, text: str, icase: bool = True) -> bool:
    first, then = _plan(pattern, icase)
    # Controller ruling (Task 4 fix round 1): Windows Git-Bash grep is the reference. It treats CR before
    # LF as part of the line ending; MEASURED there, `$` matches after one, two or three trailing CRs
    # (`printf 'a.txt\r\r\n' | grep -qiE 'txt$'` exits 0) but not a CR followed by more text. Linux GNU grep
    # does not do this. So every trailing CR is dropped from each line before matching.
    return any(_line_matches(first, then, line.rstrip("\r")) for line in text.split("\n"))
