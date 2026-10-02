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
    return any(rx.search(line) for line in text.split("\n"))
