"""Host patch grammars, extracted from the qualified X4 adapter at 0ad8f78.

Codex 0.160.0 and OpenCode V1 1.18.34 have different header semantics. These
readers return only affected operations/paths; shell parsing stays with profiles.
The captured Codex oracle is historical version evidence, not a current-host claim.
"""
from __future__ import annotations

import re

OPS = ("add", "update", "delete", "move_from", "move_to")
BEGIN, END = "*** Begin Patch", "*** End Patch"

#: Rust's char::is_whitespace (Unicode White_Space), which str::trim uses. NOT Python's
#: str.isspace: that adds U+001C..U+001F, so `\x1c*** Delete File:` would be a header here and is
#: not one to Codex (MEASURED, row fs-indented-after-add).
_RUST_WS = ("\t\n\x0b\x0c\r \x85\xa0          "
            "      　")
#: JavaScript's String.prototype.trim (WhiteSpace + LineTerminator): no U+0085, plus U+FEFF.
_JS_WS = ("\t\n\x0b\x0c\r \xa0          "
          "      　﻿")

class PatchParseError(ValueError):
    pass


# --- Codex 0.160.0 ------------------------------------------------------------------------------

_ADD, _DELETE, _UPDATE = "*** Add File: ", "*** Delete File: ", "*** Update File: "
_MOVE, _EOF, _ENV = "*** Move to: ", "*** End of File", "*** Environment ID:"
_CTX, _CTX_EMPTY = "@@ ", "@@"


def _rust_lines(s: str) -> list[str]:
    """str::lines(): split at \\n, drop ONE \\r before it, no empty piece after a final \\n."""
    parts = s.split("\n")
    out = [p[:-1] if p.endswith("\r") else p for p in parts[:-1]]   # each ended by a \n
    if parts[-1]:
        out.append(parts[-1])                        # an unterminated last line keeps a bare \r
    return out


def _codex_boundaries(lines: list[str]) -> list[str]:
    """parser.rs check_patch_boundaries_lenient: strict first, else a `<<EOF` heredoc wrapper."""
    def strict(ls):
        first = ls[0].strip(_RUST_WS) if ls else None
        last = ls[-1].strip(_RUST_WS) if ls else None
        if first == BEGIN and last == END:
            return None
        if first != BEGIN:
            return "The first line of the patch must be '*** Begin Patch'"
        return "The last line of the patch must be '*** End Patch'"
    err = strict(lines)
    if err is None:
        return lines
    if len(lines) >= 4 and lines[0] in ("<<EOF", "<<'EOF'", '<<"EOF"') and lines[-1].endswith("EOF"):
        inner = lines[1:-1]
        err2 = strict(inner)
        if err2 is None:
            return inner
        raise PatchParseError(f"invalid patch: {err2}")
    raise PatchParseError(f"invalid patch: {err}")


class _Codex:
    """streaming_parser.rs StreamingPatchParser, keeping only what decides which files a patch
    touches: the hunks, and per Update chunk whether it holds lines and ends at End of File."""

    def __init__(self):
        self.mode = "not_started"
        self.update_line_no = 0
        self.hunks: list[dict] = []
        self.env = None
        self.n = 0

    def fail(self, msg: str):
        raise PatchParseError(f"invalid hunk at line {self.n}, {msg}")

    def _last_update(self):
        return self.hunks[-1] if self.hunks and self.hunks[-1]["op"] == "update" else None

    def ensure_update_not_empty(self, line: str):
        h = self._last_update()
        if h is None:
            return
        if not h["chunks"] and self.mode == "update":
            raise PatchParseError(f"invalid hunk at line {self.update_line_no}, Update file hunk for "
                                  f"path '{h['path']}' is empty")
        if h["chunks"] and not h["chunks"][-1]["lines"]:
            self.fail("Update hunk does not contain any lines" if line == END
                      else f"Unexpected line found in update hunk: {line!r}")

    def headers(self, trimmed: str) -> bool:
        if self.mode == "started" and trimmed.startswith(_ENV):
            if self.env is not None:
                raise PatchParseError("invalid patch: apply_patch environment_id cannot be specified more than once")
            env = trimmed[len(_ENV):].strip(_RUST_WS)
            if not env:
                raise PatchParseError("invalid patch: apply_patch environment_id cannot be empty")
            self.env = env
            return True
        if trimmed == END:
            self.ensure_update_not_empty(trimmed)
            self.mode = "ended"
            return True
        for marker, op in ((_ADD, "add"), (_DELETE, "delete"), (_UPDATE, "update")):
            if trimmed.startswith(marker):
                self.ensure_update_not_empty(trimmed)
                self.hunks.append({"op": op, "path": trimmed[len(marker):], "move": None, "chunks": []})
                self.mode = op
                if op == "update":
                    self.update_line_no = self.n
                return True
        return False

    def line(self, line: str):
        trimmed = line.strip(_RUST_WS)
        if self.mode == "not_started":
            if trimmed == BEGIN:
                self.mode = "started"
                return
            raise PatchParseError("invalid patch: The first line of the patch must be '*** Begin Patch'")
        if self.mode in ("started", "add", "delete"):
            if self.headers(trimmed):
                return
            if self.mode == "add" and line.startswith("+"):
                return
            self.fail(f"{trimmed!r} is not a valid hunk header")
        if self.mode == "ended":
            if trimmed:
                raise PatchParseError("invalid patch: The last line of the patch must be '*** End Patch'")
            return
        # update: headers are recognised on the RIGHT-trimmed line only -- an indented
        # `  *** Delete File: x` inside an Update is a context line (MEASURED, indented-after-update)
        u = line.rstrip(_RUST_WS)
        if self.headers(u):
            return
        h = self.hunks[-1]
        chunks = h["chunks"]
        if chunks and chunks[-1]["eof"]:
            if not u:
                return
            if u != _CTX_EMPTY and not u.startswith(_CTX):
                self.fail(f"Expected update hunk to start with a @@ context marker, got: {line!r}")
        if not chunks and h["move"] is None and u.startswith(_MOVE):
            h["move"] = u[len(_MOVE):]
            return
        if (u == _CTX_EMPTY or u.startswith(_CTX)) and chunks and not chunks[-1]["lines"]:
            self.fail(f"Unexpected line found in update hunk: {line!r}")
        if u == _CTX_EMPTY or u.startswith(_CTX):
            chunks.append({"lines": False, "eof": False})
            return
        if u == _EOF:
            if chunks and not chunks[-1]["lines"]:
                self.fail("Update hunk does not contain any lines")
            if chunks:
                chunks[-1]["eof"] = True
            return
        if line == "" or line[0] in " +-":
            if not chunks:
                chunks.append({"lines": False, "eof": False})
            chunks[-1]["lines"] = True
            return
        if chunks and chunks[-1]["lines"]:
            self.fail(f"Expected update hunk to start with a @@ context marker, got: {line!r}")
        self.fail(f"Unexpected line found in update hunk: {line!r}")

    def finish(self, last: str):
        if last:
            self.n += 1
            if last.strip(_RUST_WS) == END:
                self.ensure_update_not_empty(last.strip(_RUST_WS))
                self.mode = "ended"
            else:
                self.line(last)
        if self.mode != "ended":
            raise PatchParseError("invalid patch: The last line of the patch must be '*** End Patch'")


def parse_patch(text: str) -> list[tuple[str, str]]:
    """Codex's reading: parser.rs parse_patch (lenient mode, as Codex runs it) -> the hunks."""
    if not isinstance(text, str):
        raise PatchParseError("patch text is not a string")
    lines = _codex_boundaries(_rust_lines(text.strip(_RUST_WS)))
    body = "\n".join(lines)
    p = _Codex()
    pieces = body.split("\n")
    for piece in pieces[:-1]:                        # push_delta: each \n-terminated line,
        p.n += 1                                     # one more trailing \r dropped
        p.line(piece[:-1] if piece.endswith("\r") else piece)
    p.finish(pieces[-1])
    ops: list[tuple[str, str]] = []
    for h in p.hunks:
        if h["op"] == "update" and h["move"] is not None:
            ops += [("move_from", h["path"]), ("move_to", h["move"])]
        else:
            ops.append((h["op"], h["path"]))
    if not ops:
        raise PatchParseError("the patch touches no file")
    return ops


# --- OpenCode v1.18.34 --------------------------------------------------------------------------

_OC_HEREDOC = re.compile(
    "^(?:cat[%s]+)?<<['\"]?([A-Za-z0-9_]+)['\"]?[%s]*\n([\\s\\S]*?)\n\\1[%s]*\\Z" % ((_JS_WS,) * 3))


def parse_patch_opencode(text: str) -> list[tuple[str, str]]:
    """OpenCode's reading: patch/index.ts parsePatch. Headers count only at column 0, any line it
    does not know is skipped, a hunk body runs to the next line starting `***`, paths are trimmed.
    Raises where OpenCode's apply_patch tool fails the call: no Begin/End, or no hunks."""
    if not isinstance(text, str):
        raise PatchParseError("patch text is not a string")
    cleaned = text.strip(_JS_WS)
    m = _OC_HEREDOC.match(cleaned)
    if m:
        cleaned = m.group(2)
    lines = cleaned.split("\n")
    begin = next((i for i, ln in enumerate(lines) if ln.strip(_JS_WS) == BEGIN), -1)
    end = next((i for i, ln in enumerate(lines) if ln.strip(_JS_WS) == END), -1)
    if begin == -1 or end == -1 or begin >= end:
        raise PatchParseError("Invalid patch format: missing Begin/End markers")

    def body_end(i: int) -> int:                     # parseAddFileContent / parseUpdateFileChunks
        while i < len(lines) and not lines[i].startswith("***"):
            i += 1
        return i

    ops: list[tuple[str, str]] = []
    i = begin + 1
    while i < end:
        line = lines[i]
        kind = next((k for k in ("Add", "Delete", "Update") if line.startswith(f"*** {k} File:")), None)
        if kind is None:
            i += 1
            continue
        path = line[len(f"*** {kind} File:"):].strip(_JS_WS)
        nxt, move = i + 1, None
        if kind == "Update" and nxt < len(lines) and lines[nxt].startswith("*** Move to:"):
            move = lines[nxt][len("*** Move to:"):].strip(_JS_WS)
            nxt += 1
        if not path:                                 # parsePatchHeader returned null
            i += 1
            continue
        if kind == "Add":
            ops.append(("add", path))
            i = body_end(nxt)
        elif kind == "Delete":
            ops.append(("delete", path))
            i = nxt
        else:
            ops += [("move_from", path), ("move_to", move)] if move else [("update", path)]
            i = body_end(nxt)
    if not ops:
        raise PatchParseError("apply_patch verification failed: no hunks found")
    return ops
