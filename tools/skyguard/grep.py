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


# The only `A` prefixes the split is applied to. Splitting `A.*B` into "A matches, then B matches after A's FIRST
# end" is equivalent ONLY when no later A-match can end earlier, and that is not true of regexes in general
# (`a+.*a` on `aa`, `(abc|a).*b` on `abc`). It is equivalent for these patterns (measured: every real call in
# the history corpus and 76k fuzzed lines agree with the unsplit regex), so they are listed, not inferred.
SPLIT_SAFE_FIRST = frozenset([
    r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s",
    r"(^|[;&|(`]|\s)(mv|cp|move|copy)\s",
    r"sed\s+-i",
    r"(automod|SpookysAutomod)",
    r"Remove-ItemProperty",
    r"(?<![/\\])[/\\]+Data[/\\]+SKSE[/\\]+Plugins[/\\]+",
])


def split_dotstar(pattern: str):
    """(first, then) when `pattern` is `first.*then` for a first in SPLIT_SAFE_FIRST, else None (use the full regex).

    `A.*B` tried from every A start is quadratic on a line full of A's (`rm rm rm ...`, 6666 starts x 20 KB
    measured at 2.8 s), and a hook that answers after its timeout has its refusal dropped. The structural
    checks below are a second line of defence: a lazy or possessive quantifier, a backreference, an inline flag
    or lookahead, a second `.*` anywhere, a top-level `|`, or the `.*` inside a group all refuse.
    """
    if re.search(r"\.\*[?+]|\\[1-9]|\(\?(?!:|<![/\\\[])", pattern) or pattern.count(".*") != 1:
        return None
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
                cut = i
        i += 1
    if cut <= 0 or cut + 2 >= len(pattern) or pattern[:cut] not in SPLIT_SAFE_FIRST:
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
