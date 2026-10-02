"""`echo "$TEXT" | grep -qE PATTERN`, reproduced: a pattern matches if it matches ANY LINE.

grep is line-oriented, and its [[:space:]] never crosses a newline. Python's \\s does, so every ported
pattern is applied to each line separately. Patterns are written in Python syntax by hand; this
module does not translate POSIX syntax.
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def _compile(pattern: str, icase: bool):
    return re.compile(pattern, re.IGNORECASE if icase else 0)


def grep(pattern: str, text: str, icase: bool = True) -> bool:
    rx = _compile(pattern, icase)
    return any(rx.search(line) for line in text.split("\n"))
