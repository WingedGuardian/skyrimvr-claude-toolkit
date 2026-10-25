#!/usr/bin/env python3
"""Carry what a 3.x user wrote into a 4.0 install. Nothing is deleted; every change is backed up first.

WHY. The documented update is "extract the new zip over your install". That replaces every file the
toolkit ships -- including three a 3.x user may have edited: CLAUDE.md (and AGENTS.md), the hook settings
in .claude/settings.json, and KNOWLEDGEBASE.md (3.x told you to append findings to it until 3.9). This tool
finds the 3.x copies and keeps a byte-exact copy of EVERY one, shipped or not ("shipped" never means "no
copy"); it then decides for each whether it is exactly what a release shipped, and carries the text of every
one that is not. The paths setup.sh had filled into CLAUDE.md are carried as data in any case
(.claude/backups/migrate-3x/carried-paths.env), so a path corrected by hand survives 4.0's fresh detection.

WHERE THE 3.x COPIES COME FROM (the first that is a 3.x install wins; `--from DIR` overrides all three):
  1. the install itself, when the update has not been extracted yet (its CLAUDE.md is still 3.x);
  2. the newest `.claude/backups/pre-update-<stamp>/` -- what `tools/pre-update-snapshot.sh` writes;
  3. the kb-guard store `.claude/backups/kb/` (3.9's session-kb-guard): the newest 3.x-shaped CLAUDE.md /
     AGENTS.md copies (4.x copies, stored if a 4.0 session started before setup, are skipped by their header),
     and the knowledgebase copy from before the first 4.x copy. The copies used are named. Settings are not
     kept there, and the tool says so.

"EXACTLY WHAT A RELEASE SHIPPED" is decided against tools/migrate-3x-known.json, built from every
pre-4 tag by scripts/gen_migrate_3x_known.py. setup.sh wrote your paths into CLAUDE.md, so a hash of the
installed file can never equal a hash of the template. The comparison therefore NORMALISES the file first:
  - CRLF -> LF and a leading BOM dropped (an editor may do either; neither is an edit);
  - the mod-manager block setup spliced in is collapsed back to `{{MOD_MANAGER_PATHS}}`, but only when it
    is, line for line, one of the blocks that release's setup.sh could write (paths inside it are
    normalised the same way as below);
  - each template line that carried a `{{PLACEHOLDER}}` is matched at its own line number, the value in
    the placeholder's position captured, and the line put back to its template form. A captured value may
    not contain a backtick or a newline, the same placeholder must hold the same value everywhere, and when
    the install recorded its paths (.claude/skyrim-paths.env, 3.9+) the value must equal the recorded one;
  - the result is hashed and looked up. Any difference anywhere else -- one character -- is "edited".
  A file judged "edited" that was not is harmless: you get a copy you did not need. The reverse would lose
  your text, so every rule above errs toward "edited".

WHAT HAPPENS TO AN EDITED FILE
  CLAUDE.md / AGENTS.md  -> copied byte for byte into a marked block at the end of KNOWLEDGEBASE.local.md
                            (created if absent), keyed by the file's SHA-256.
  KNOWLEDGEBASE.md       -> if it is a shipped version with text appended, the appended text goes into a
                            marked block; otherwise the block points at the byte-exact copy.
  .claude/settings.json  -> saved as .claude/settings.json.3x-backup (never overwritten: a second, different
                            one gets a hash suffix), and the hook entries are compared with the 4.0 file so
                            you are told which of yours 4.0 already covers (its widened matchers) and which
                            you must re-add.
  Before anything is written, every source file and the old KNOWLEDGEBASE.local.md are copied into
  .claude/backups/migrate-3x/<stamp>/ and read back, and MIGRATION.txt there says how to undo each change --
  written even when a later step fails, listing what was done.

IDEMPOTENT. Each copied and carried file is recorded by name and content hash in .claude/backups/migrate-3x/state.json
and found again by its marker in KNOWLEDGEBASE.local.md. A second run changes nothing, and a block you
deleted after sorting it does not come back.

CODEX. No 3.x release shipped Codex hook definitions (the generator asserts it), so a 3.x user has nothing
to re-approve: the 4.0 `.codex/hooks.json` is approved once in Codex's /hooks. This tool never touches .codex/.

Usage:  python tools/migrate-3x.py [--root <install>] [--from <dir>] [--dry-run]
Exit:   0 = done (including "nothing to carry over") · 1 = something could not be kept (nothing half-written)
        2 = usage error, or --root is not a toolkit install
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KNOWN_FILE = HERE / "migrate-3x-known.json"
LOCAL_KB = "KNOWLEDGEBASE.local.md"
DOCS = ("CLAUDE.md", "AGENTS.md")
KB = "KNOWLEDGEBASE.md"
SETTINGS = ".claude/settings.json"
ENV = ".claude/skyrim-paths.env"
STORE = ".claude/backups/migrate-3x"
MARK = "skyrim-toolkit migrate-3x"
LIVE = "the install itself"
# The 4.x generator's first line (scripts/gen_agent_trees.py). A file that starts with it is not a 3.x file.
GENERATED_4X = re.compile(r"\A(?:﻿)?<!-- GENERATED by scripts/gen_agent_trees\.py")
# Present in every 4.x .claude/settings.json and in no 3.x one (protect-mcp.sh is new in 4.0).
SETTINGS_4X = "protect-mcp.sh"
# skyrim-paths.env key -> CLAUDE.md placeholder
ENV_KEYS = {"SKYRIM_GAME_ROOT": "GAME_ROOT", "SKYRIM_CONFIG_DIR": "CONFIG_DIR", "SKYRIM_LOADORDER_DIR": "LOADORDER_DIR"}
PH = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


# ----------------------------------------------------------------------------------------------- text --

def norm(data: bytes) -> str:
    """BOM dropped, CRLF folded. Undecodable bytes survive (surrogateescape) so they still count as content."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", "surrogateescape").replace("\r\n", "\n")


def verbatim(data: bytes) -> str:
    """The file as text, byte for byte (line endings kept); only a leading BOM is dropped."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", "surrogateescape")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(text: str):
    try:
        return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except ValueError:
        return None


# ------------------------------------------------------------------------------------- template match --

def _line_regex(template_line: str):
    """A template line -> (compiled regex, [placeholder names]). A value may not hold a backtick: every 3.x
    placeholder sits inside backticks, so text with one in it is the user's, not setup's."""
    parts, names, pos = [], [], 0
    for m in PH.finditer(template_line):
        parts.append(re.escape(template_line[pos:m.start()]))
        parts.append("([^`\n]*)")
        names.append(m.group(1))
        pos = m.end()
    parts.append(re.escape(template_line[pos:]))
    return re.compile("".join(parts) + r"\Z"), names


def _fill(lines, spec, values) -> bool:
    """Put each placeholder line back to its template form, collecting values. False on any mismatch."""
    for idx, tline in spec.get("placeholders", []):
        if idx >= len(lines):
            return False
        rx, names = _line_regex(tline)
        m = rx.match(lines[idx])
        if not m:
            return False
        for name, value in zip(names, m.groups()):
            if values.setdefault(name, value) != value:
                return False             # the same placeholder rendered two different ways: an edit
        lines[idx] = tline
    return True


def match_values(text: str, spec: dict, env=None):
    """If `text` (normalised) is a setup.sh rendering of the template `spec` describes and nothing else, the
    placeholder values setup.sh filled in (mod-manager block included); otherwise None."""
    lines = text.split("\n")
    block = spec.get("block")
    values: dict = {}
    if block:
        start = block["index"]
        tail = spec["lines"] - start - 1
        end = len(lines) - tail
        if start < 0 or end <= start:
            return None
        # setup.sh never ran: the placeholder line itself is still there, which is the template, not an edit.
        unrendered = lines[start:end] == ["{{MOD_MANAGER_PATHS}}"]
        if not unrendered and not any(_block_matches(lines[start:end], v, values) for v in block["variants"]):
            return None
        lines = lines[:start] + ["{{MOD_MANAGER_PATHS}}"] + lines[end:]
    if "lines" in spec and len(lines) != spec["lines"]:
        return None
    if not _fill(lines, spec, values):
        return None
    for name, recorded in (env or {}).items():
        if name in values and values[name] != recorded:
            return None                  # the file says one path, the install recorded another: an edit
    return values if sha("\n".join(lines)) == spec["sha256"] else None


def matches(text: str, spec: dict, env=None) -> bool:
    return match_values(text, spec, env) is not None


def _block_matches(block_lines, variant, into=None) -> bool:
    lines, values = list(block_lines), {}
    if len(lines) != variant["lines"]:
        return False
    if not _fill(lines, variant, values):
        return False
    if sha("\n".join(lines)) != variant["sha256"]:
        return False
    if into is not None:
        into.update(values)
    return True


def extract_paths(text: str, known: dict, env=None):
    """Every value setup.sh filled into a 3.x CLAUDE.md -> ({placeholder: value}, how). A shipped rendering
    gives them exactly; an edited file is searched line by line with each template's placeholder lines (the
    newest template that yields the most values wins), so a path is carried even from a file you changed."""
    for spec in known["docs"].get("CLAUDE.md", []):
        v = match_values(text, spec, env)
        if v is not None:
            return v, "exact"
    lines = text.split("\n")
    best: dict = {}
    for spec in reversed(known["docs"].get("CLAUDE.md", [])):
        top = [t for _, t in spec.get("placeholders", [])]
        tlines = list(top)
        for variant in spec.get("block", {}).get("variants", []):
            tlines += [t for _, t in variant.get("placeholders", [])]
        found: dict = {}
        hit = 0
        for tline in tlines:
            rx, names = _line_regex(tline)
            for line in lines:
                m = rx.match(line)
                if m:
                    hit += tline in top
                    for name, value in zip(names, m.groups()):
                        found.setdefault(name, value)
                    break
        if top and hit == len(top):
            return found, "searched"          # the newest template whose every path line is still there
        if len(found) > len(best):
            best = found
    return best, "searched"


def read_env(path: Path):
    """The three recorded paths from a 3.9 skyrim-paths.env (single-quoted shell, '\\'' escapes). {} if
    absent or unreadable -- the cross-check is then skipped, never failed."""
    out = {}
    try:
        text = norm(path.read_bytes())
    except OSError:
        return out
    for line in text.split("\n"):
        m = re.match(r"([A-Z_]+)=(.*)\Z", line.strip())
        if not m or m.group(1) not in ENV_KEYS:
            continue
        raw, val, ok = m.group(2), [], True
        for q in re.finditer(r"'([^']*)'|\\'|(.)", raw):
            if q.group(1) is not None:
                val.append(q.group(1))
            elif q.group(0) == "\\'":
                val.append("'")
            else:
                ok = False
                break
        if ok:
            out[ENV_KEYS[m.group(1)]] = "".join(val)
    return out


# ------------------------------------------------------------------------------------- classification --

def classify_doc(name: str, data: bytes, known: dict, env=None):
    """-> ("4x"|"pristine"|"edited", detail)."""
    text = norm(data)
    if GENERATED_4X.match(text):
        return "4x", None
    for spec in known["docs"].get(name, []):
        if matches(text, spec, env):
            return "pristine", spec["tags"]
    return "edited", None


def kb_appended(data: bytes, known: dict):
    """If the knowledgebase is a shipped version with text added at the END, that text; else None. The LONGEST
    shipped version that is a prefix wins: an older release's file can be a byte prefix of a newer one's (v3.8's of
    v3.9.1's), and the shortest match would hand the newer release's own text over as the user's notes."""
    text = norm(data)
    raw = text.encode("utf-8", "surrogateescape")
    for spec in sorted(known["docs"].get(KB, []), key=lambda s: s["bytes"], reverse=True):
        n = spec["bytes"]
        if len(raw) > n and hashlib.sha256(raw[:n]).hexdigest() == spec["sha256"]:
            return raw[n:].decode("utf-8", "surrogateescape")
    return None


def classify_settings(data: bytes, known: dict):
    text = norm(data)
    if SETTINGS_4X in text:
        return "4x", None
    h, c = sha(text), canonical_json(text)
    ch = sha(c) if c is not None else None
    for spec in known["settings"]:
        if h == spec["sha256"] or (ch is not None and ch == spec["canonical_sha256"]):
            return "pristine", spec["tags"]
    return "edited", None


# --------------------------------------------------------------------------------------- source sets --

def _stamp_key(name: str):
    """`<prefix>YYYYmmdd_HHMMSS[_n]` -> sortable key. The suffix is only ever a collision counter AFTER the
    six-digit time, so it is matched there -- splitting at the last `_` would read the time as a counter."""
    m = re.match(r"(.*_\d{6})(?:_(\d+))?\Z", name)
    return (m.group(1), int(m.group(2) or 1)) if m else (name, 1)


def _newest(dirs):
    dirs = [d for d in dirs if d.is_dir()]
    return max(dirs, key=lambda d: _stamp_key(d.name)) if dirs else None


class SourceSet:
    def __init__(self, label: str, files: dict, env: dict, codex: bool):
        self.label, self.files, self.env, self.codex = label, files, env, codex

    def era(self) -> str:
        """"4" if anything in it is unmistakably 4.x, "3" if it holds any toolkit file, else "none"."""
        for name in DOCS:
            p = self.files.get(name)
            if p and GENERATED_4X.match(norm(p.read_bytes())):
                return "4"
        p = self.files.get(SETTINGS)
        if p and SETTINGS_4X in norm(p.read_bytes()):
            return "4"
        if self.codex:
            return "4"
        return "3" if self.files else "none"


def _label(text: str) -> str:
    """A path as display text that every later encode accepts. Undecodable bytes (U+DC80..U+DCFF) and a lone
    surrogate a Windows path can carry both become visible escapes (\\udcxx) -- a label is shown, never used."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _set_from_dir(label: str, base: Path) -> SourceSet:
    files = {n: base / n for n in (*DOCS, KB, SETTINGS) if (base / n).is_file()}
    return SourceSet(label, files, read_env(base / ENV), _is_4x_codex(base / ".codex" / "hooks.json"))


def _is_4x_codex(path: Path) -> bool:
    """Recognize the managed 4.x entry routes, never the mere presence of a user's Codex definitions."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return False
    for event, route in (("SessionStart", "session-start"), ("PreToolUse", "pre-tool")):
        groups = hooks.get(event)
        commands = [hook.get(key) for group in groups if isinstance(group, dict)
                    for hook in (group.get("hooks") if isinstance(group.get("hooks"), list) else [])
                    if isinstance(hook, dict) and hook.get("type") == "command"
                    for key in ("command", "commandWindows")] if isinstance(groups, list) else []
        pattern = r'[/\\]\.codex[/\\]hooks[/\\]skyguard-entry\.(?:sh|ps1)"?\s+' + route + r'(?:\s|$)'
        if not any(isinstance(command, str) and re.search(pattern, command) for command in commands):
            return False
    return True


def _is_4x_doc(path: Path) -> bool:
    return GENERATED_4X.match(norm(path.read_bytes())) is not None


def _kb_store_set(root: Path):
    """The newest 3.x-shaped CLAUDE.md / AGENTS.md copies in the kb-guard store, and the newest
    KNOWLEDGEBASE.md copy from BEFORE the first 4.x copy that follows them. A 4.0 session started before
    setup (extract, open the agent, ask it to run setup) stores the 4.0 files as the newest copies; skipping
    those by their generated header keeps the last 3.x state reachable. The knowledgebase has no header,
    so it is dated instead: kb-guard copies every changed file in the same run, so the 4.0 one lands in the
    same folder as the first 4.x CLAUDE.md, at or after the cutoff."""
    store = root / ".claude" / "backups" / "kb"
    if not store.is_dir():
        return None
    dirs = sorted((d for d in store.iterdir() if d.is_dir()), key=lambda d: _stamp_key(d.name))
    files, chosen = {}, []
    for name in DOCS:
        ok = [d for d in dirs if (d / name).is_file() and not _is_4x_doc(d / name)]
        if ok:
            files[name] = ok[-1] / name
            chosen.append(_stamp_key(ok[-1].name))
    if not files:
        return None
    last3 = max(chosen)
    cutoff = next((_stamp_key(d.name) for d in dirs if _stamp_key(d.name) > last3
                   and any((d / n).is_file() and _is_4x_doc(d / n) for n in DOCS)), None)
    kb = [d for d in dirs if (d / KB).is_file() and (cutoff is None or _stamp_key(d.name) < cutoff)]
    if kb:
        files[KB] = kb[-1] / KB
    used = ", ".join("{} from {}".format(n, p.parent.name) for n, p in files.items())
    return SourceSet(".claude/backups/kb (the newest 3.x copies: {})".format(used), files, {}, False)


def pick_source(root: Path, from_dir):
    """The 3.x set to migrate from, or None. Order: --from; the live install; the newest pre-update
    snapshot; the kb-guard store. The first that is a 3.x set wins."""
    if from_dir is not None:
        s = _set_from_dir(_label(str(from_dir)), from_dir)
        return s if s.era() == "3" else None
    cands = [_set_from_dir(LIVE, root)]
    snap = _newest(list((root / ".claude" / "backups").glob("pre-update-*")))
    if snap:
        cands.append(_set_from_dir(snap.relative_to(root).as_posix(), snap))
    kb = _kb_store_set(root)
    if kb:
        cands.append(kb)
    for s in cands:
        if s.era() == "3":
            return s
    return None


# ------------------------------------------------------------------------------- KNOWLEDGEBASE.local --

_MARK_RX = re.compile(r"<!-- " + re.escape(MARK) + r" (BEGIN|END) (\S+) sha256=([0-9a-f]{64}) -->\Z")


def existing_blocks(text: str):
    """The (name, sha) of every block in the file, read STRUCTURALLY: after a BEGIN, everything up to its
    own END is content, so a marker inside carried-over text can never be mistaken for a block."""
    found, open_key = set(), None
    for line in text.split("\n"):
        m = _MARK_RX.match(line.rstrip("\r"))
        if not m:
            continue
        kind, key = m.group(1), (m.group(2), m.group(3))
        if open_key is None and kind == "BEGIN":
            open_key = key
        elif open_key is not None and kind == "END" and key == open_key:
            found.add(open_key)
            open_key = None
    return found


def _fence(content: str) -> str:
    """A backtick fence longer than any backtick run that starts a line of the content, so nothing inside
    can close it (CommonMark: a closing fence must be at least as long as the opening one)."""
    longest = 0
    for line in content.split("\n"):
        m = re.match(r"[ \t]*(`+)", line)
        if m:
            longest = max(longest, len(m.group(1)))
    return "`" * max(4, longest + 1)


def _has_prefs_section(text: str) -> bool:
    try:
        sys.path.insert(0, str(HERE))
        from skyguard import prefs           # the parser the session hook really uses
        return prefs.section(text) is not None
    except Exception:                         # noqa: BLE001 -- fall back to the plain heading test
        return re.search(r"(?mi)^##[ \t]+my preferences[ \t]*#*[ \t]*$", text) is not None
    finally:
        if sys.path and sys.path[0] == str(HERE):
            sys.path.pop(0)


def build_block(name: str, digest: str, title: str, intro: str, content, nl: str = "\n") -> str:
    """One marked block. Its own lines end with `nl` (the local knowledgebase's style); the carried `content`
    is inserted exactly as it is -- a CRLF file does not get its carried text's lone LFs rewritten."""
    # The heading comes FIRST: the session hook's preferences section runs up to the next heading, so a marker
    # line ahead of it would be read as part of whatever `## My preferences` section precedes the block.
    head = ["", "## " + title, "<!-- {} BEGIN {} sha256={} -->".format(MARK, name, digest), "", intro]
    end = "<!-- {} END {} sha256={} -->".format(MARK, name, digest) + nl
    if content is None:
        return nl.join(head) + nl + end
    if not content.endswith("\n"):
        content += nl
    f = _fence(content)
    return nl.join(head + ["", f + "text"]) + nl + content + f + nl + end


# --------------------------------------------------------------------------------------- settings --

def _hook_entries(obj):
    out = []
    hooks = obj.get("hooks") if isinstance(obj, dict) else None
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict):
                continue
            matcher = g.get("matcher", "")
            for h in g.get("hooks", []) if isinstance(g.get("hooks"), list) else []:
                if isinstance(h, dict):
                    out.append((event, matcher if isinstance(matcher, str) else "", str(h.get("command", ""))))
    return out


def _script(command: str) -> str:
    m = re.search(r"([A-Za-z0-9_.-]+\.(?:sh|py|ps1))\b", command)
    return m.group(1) if m else command.strip()


def _covers(ref_matcher: str, token: str) -> bool:
    if token in ref_matcher.split("|"):
        return True
    try:
        return re.fullmatch(ref_matcher, token) is not None
    except re.error:
        return False


def settings_report(user_text: str, ref_path: Path, known=None):
    """How a user's 3.x settings differ from 4.0's: hooks of theirs 4.0 lacks, matchers 4.0 widened, commands
    they changed, shipped hooks they removed, extra keys. Report only: the backup keeps all of it."""
    try:
        user = json.loads(user_text)
    except ValueError:
        return ["your settings.json is not valid JSON; the backup keeps it exactly as it was"]
    try:
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["(the 4.0 .claude/settings.json could not be read, so nothing was compared)"]
    lines, ref_entries, user_entries = [], _hook_entries(ref), _hook_entries(user)
    for event, matcher, command in user_entries:
        script = _script(command)
        same = [m for e, m, c in ref_entries if e == event and _script(c) == script]
        label = "{} {} -> {}".format(event, matcher or "(all)", script)
        if not same:
            lines.append("NOT in 4.0, re-add it by hand (in .claude/settings.local.json): " + label)
            continue
        ref_cmds = [c for e, m, c in ref_entries if e == event and _script(c) == script]
        if command not in ref_cmds:
            lines.append("changed command: yours runs `{}`, 4.0 runs `{}`: {}".format(command, ref_cmds[0], label))
        tokens = [t for t in matcher.split("|") if t] if matcher else []
        missing = [t for t in tokens if not any(_covers(m, t) for m in same)]
        if missing:
            lines.append("4.0 runs {} but not for {}: re-add those by hand: {}".format(
                script, "|".join(missing), label))
        elif matcher and matcher not in same:
            lines.append("covered by 4.0's wider matcher ({}): {}".format(" / ".join(same), label))
    shipped = {tuple(h) for spec in (known or {}).get("settings", []) for h in spec.get("hooks", [])}
    mine = {(e, _script(c)) for e, m, c in user_entries}
    for pair in sorted({(e, _script(c)) for e, m, c in ref_entries}):
        if pair in shipped and pair not in mine:
            lines.append("removed by you in 3.x, and 4.0 runs it again: {} -> {} (to keep it off, say so in "
                         ".claude/settings.local.json or ask your agent)".format(*pair))
    for key in sorted(k for k in (user if isinstance(user, dict) else {}) if k != "hooks"):
        if not isinstance(ref, dict) or key not in ref:
            lines.append("top-level key '{}' is not in 4.0's file: move it to .claude/settings.local.json".format(key))
    return lines


# -------------------------------------------------------------------------------------------- main --

def _now():
    return datetime.datetime.now()


def _unique_dir(parent: Path, stamp: str) -> Path:
    d, n = parent / stamp, 2
    while d.exists():
        d, n = parent / "{}_{}".format(stamp, n), n + 1
    return d


def _write_atomic(path: Path, data: bytes):
    tmp = path.with_name(path.name + ".migrate-tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():                      # this tool's own temp file, removed by the tool itself
            tmp.unlink()


def load_state(root: Path) -> dict:
    try:
        return json.loads((root / STORE / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"carried": {}}


class _Failed(Exception):
    pass


def _write_checked(path: Path, data: bytes):
    """Create (never replace) and read back. A copy that does not read back identical is not a backup, and a
    copy that could replace an earlier one is not a backup either: the create is EXCLUSIVE, so no code path
    -- a retry, a second source, a bug -- can overwrite a copy an earlier run made."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        raise _Failed("{} already exists -- an earlier copy is never replaced".format(path))
    if path.read_bytes() != data:
        raise _Failed("{} did not read back identical".format(path.name))


_FENCE_RUN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def _open_fence(text: str):
    """The fence left open at EOF, or None. A closer must match the opener's character,
    be at least as long, and have no trailing text, as in the preferences parser."""
    token = None
    for line in text.splitlines():
        m = _FENCE_RUN.match(line)
        if m:
            fence = m.group(1)
            if token is None:
                token = fence
            elif fence[0] == token[0] and len(fence) >= len(token) and not line[m.end():].strip():
                token = None
    return token


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def paths_file(values: dict, how: str, src_label: str, env: dict) -> str:
    label = re.sub(r"[\r\n]+", " ", src_label)         # a folder name with a newline stays in the comment
    out = ["# Written by tools/migrate-3x.py: the paths your 3.x CLAUDE.md held, read from {}.".format(label),
           "# setup.sh reads this so that a path you had corrected by hand is kept, not re-detected.",
           "# {} (exact = a shipped rendering; searched = found line by line in a file you had edited).".format(how)]
    for name in sorted(values):
        if env.get(name) not in (None, values[name]):
            out.append("# NOTE: 3.9 recorded {} as {} -- the file says otherwise".format(name, _sh_quote(env[name])))
        out.append("SKYRIM_3X_{}={}".format(name, _sh_quote(values[name])))
    return "\n".join(out) + "\n"


def run(root: Path, from_dir=None, dry_run=False, out=print) -> int:
    known = json.loads(KNOWN_FILE.read_text(encoding="utf-8"))
    src = pick_source(root, from_dir)
    if src is None:
        out("migrate-3x: no 3.x files found (this install is already 4.x, or --from holds none) -- nothing to do.")
        return 0
    out("migrate-3x: reading the 3.x files from " + src.label)
    if src.label == LIVE and from_dir is None:
        out("  (the update has not been extracted over this install yet -- fine: run setup.sh again afterwards)")
    if SETTINGS not in src.files:
        out("  {}: not in {} -- NOT checked. If you had changed it, the only copy is a pre-update snapshot "
            "or your own backup.".format(SETTINGS, src.label))
    state = load_state(root)
    carried = state.setdefault("carried", {})
    copied = state.setdefault("copied", {})
    local_path = root / LOCAL_KB
    local_bytes = local_path.read_bytes() if local_path.is_file() else None
    local_text = norm(local_bytes) if local_bytes is not None else ""
    present = existing_blocks(local_text)

    # EVERY 3.x file the update replaces gets a byte-exact copy, shipped or not: "shipped" only decides whether
    # its text is ALSO carried into KNOWLEDGEBASE.local.md. A copy is made once per content (state "copied").
    copies, blocks, settings_actions, notes = [], [], [], []
    verdicts = {}
    for name in (*DOCS, KB, SETTINGS):
        p = src.files.get(name)
        if p is None:
            continue
        data = p.read_bytes()
        digest = sha_bytes(data)
        if name == SETTINGS:
            verdict, _ = classify_settings(data, known)
        elif name == KB:
            verdict = "pristine" if any(sha(norm(data)) == s["sha256"] for s in known["docs"][KB]) else "edited"
        else:
            verdict, _ = classify_doc(name, data, known, src.env)
        if verdict == "4x":
            out("  {}: already a 4.x file -- nothing to keep".format(name))
            continue
        verdicts[name] = verdict
        ckey = "{}:{}".format(name, digest)
        if ckey not in copied:
            copies.append((name, data, ckey))
        key = ckey
        if verdict != "edited":
            continue
        if name == SETTINGS:
            if key in carried:
                continue
            target = root / ".claude" / "settings.json.3x-backup"
            if target.exists():
                if sha_bytes(target.read_bytes()) == digest:
                    target = None
                else:
                    target = root / ".claude" / "settings.json.3x-backup.{}".format(digest[:12])
            settings_actions.append((key, data, target))
            notes += settings_report(norm(data), HERE.parent / SETTINGS, known)
            continue
        if key in carried or (name, digest) in present:
            out("  {}: carried over before ({}) -- left alone".format(
                name, carried.get(key, {}).get("run", "its block is in " + LOCAL_KB)))
            continue
        if name == KB:
            added = kb_appended(data, known)
            if added is not None:
                blocks.append((key, (KB, digest, "Notes you had added to the 3.x KNOWLEDGEBASE.md",
                    "The update replaced `KNOWLEDGEBASE.md`. Yours was a shipped version with the text below added "
                    "at the end; it is copied here. Keep what you still need in this file (preferences go "
                    "up into `## My preferences`), then delete this block from its heading to its END line -- it "
                    "will not come back. The whole 3.x file is kept in {run}.", added)))
            else:
                blocks.append((key, (KB, digest, "Your 3.x KNOWLEDGEBASE.md had edits",
                    "The update replaced `KNOWLEDGEBASE.md`. Yours matched no shipped version, so it held edits of "
                    "yours somewhere in it. It is too large to copy here; the byte-exact file is "
                    "`{run}/KNOWLEDGEBASE.md`. Move your own notes from it into this file, then delete this block "
                    "from its heading to its END line -- it will not come back.", None)))
        else:
            blocks.append((key, (name, digest, "Your 3.x {} (carried over by the 4.0 update)".format(name),
                "The update replaced `{n}`. Yours matched no version the toolkit shipped, so it may hold "
                "instructions you wrote; it is copied below exactly as it was (read from {src}). Sort it: lasting "
                "preferences go up into `## My preferences`, facts into the body of this file. Then delete this "
                "block from its heading to its END line -- it will not come back. A byte-exact copy is in "
                "{run}.", verbatim(data))))

    # The paths setup.sh filled in -- carried as data, so a corrected path survives 4.0's fresh detection.
    paths, how = {}, ""
    if "CLAUDE.md" in verdicts:
        paths, how = extract_paths(norm(src.files["CLAUDE.md"].read_bytes()), known, src.env)
    paths_text = paths_file(paths, how, src.label, src.env) if paths else None
    paths_new = paths_text is not None and state.get("carried_paths") != paths

    if not copies and not blocks and not settings_actions and not paths_new:
        out("migrate-3x: nothing new -- every 3.x file here was kept before ({}).".format(
            ", ".join(sorted({v["run"] for v in copied.values() if "run" in v})) or STORE))
        _codex_note(out)
        return 0
    if dry_run:
        for name, _, _ in copies:
            out("  would keep a byte-exact copy of the 3.x " + name)
        for key, _ in blocks:
            out("  would carry over " + key.split(":")[0] + " into " + LOCAL_KB)
        for key, _, target in settings_actions:
            out("  would save {} as {}".format(SETTINGS, target.relative_to(root).as_posix() if target else "(identical backup exists)"))
        for name in sorted(paths):
            out("  would carry {} = {}".format(name, paths[name]))
        for n in notes:
            out("    " + n)
        return 0

    now = _now()
    # Every run writes a NEW folder. A failed run's folder is left exactly as it was (its copies, its FAILED
    # line); this run's record names it.
    failed_before = state.get("failed_run")
    run_dir = _unique_dir(root / STORE, now.strftime("%Y%m%d_%H%M%S"))
    run_rel = run_dir.relative_to(root).as_posix()
    log = ["3.x -> 4.0 migration, {}".format(now.isoformat(timespec="seconds")), "source: " + src.label, ""]
    if failed_before:
        log += ["An earlier run FAILED: {} (its copies and its record are untouched).".format(failed_before), ""]
        out("  (an earlier run failed: {} -- left as it is; this run uses a new folder)".format(failed_before))
    rc, unexpected = 0, None
    try:
        try:
            run_dir.mkdir(parents=True)
        except OSError as e:
            raise _Failed("could not create {}: {}".format(run_rel, e))
        # 1. Backups FIRST: every 3.x file and the current KNOWLEDGEBASE.local.md, each read back.
        for name, data, ckey in copies:
            _write_checked(run_dir / name, data)
            copied[ckey] = {"run": run_rel, "copy": "{}/{}".format(run_rel, name)}
            what = {"pristine": "a shipped 3.x version", "edited": "yours had changes"}[verdicts[name]]
            out("  {}: {} -- byte-exact copy kept at {}/{}".format(name, what, run_rel, name))
            log.append("kept the 3.x {} (byte-exact): {}/{}".format(name, run_rel, name))
        if blocks:
            if local_bytes is not None:
                _write_checked(run_dir / (LOCAL_KB + ".before"), local_bytes)
                log.append("UNDO {}: copy {}/{}.before back over it".format(LOCAL_KB, run_rel, LOCAL_KB))
            else:
                log.append("UNDO {}: it did not exist before; send it to the Recycle Bin".format(LOCAL_KB))
        # 2. The carried paths: one copy for this run, one stable copy setup.sh reads.
        if paths_text is not None:
            _write_checked(run_dir / "carried-paths.env", paths_text.encode("utf-8", "surrogateescape"))
            if paths_new:
                _write_atomic(root / STORE / "carried-paths.env", paths_text.encode("utf-8", "surrogateescape"))
                state["carried_paths"] = paths
                log.append("wrote {}/carried-paths.env (copy of this run's: {}/carried-paths.env); "
                           "an earlier one, if any, is in its own run folder".format(STORE, run_rel))
                out("  your 3.x paths ({}), carried to {}/carried-paths.env for setup.sh:".format(how, STORE))
                for name in sorted(paths):
                    out("    {} = {}".format(name, paths[name]))
        # 3. Settings.
        for key, data, target in settings_actions:
            if target is None:
                carried[key] = {"run": run_rel, "backup": ".claude/settings.json.3x-backup"}
                log.append("{}: an identical .3x-backup already exists".format(SETTINGS))
                continue
            try:
                with open(target, "xb") as fh:      # exclusive: an existing backup is never overwritten
                    fh.write(data)
            except OSError as e:
                raise _Failed("could not save {}: {}".format(target, e))
            rel = target.relative_to(root).as_posix()
            carried[key] = {"run": run_rel, "backup": rel}
            log.append("saved your 3.x {} as {}. UNDO (back to 3.x hooks): copy it over {}".format(SETTINGS, rel, SETTINGS))
            out("  {}: yours had changes -- also saved as {}".format(SETTINGS, rel))
        for n in notes:
            out("    " + n)
            log.append("  " + n)
        # 4. The blocks.
        if blocks:
            prefix = ""
            if local_bytes is None:
                prefix = "# Local knowledgebase\n\n## My preferences\n\n"
            else:
                token = _open_fence(local_text)
                if token:
                    # The file ends inside an unclosed fence: close it, or the blocks' headings would be read as
                    # inside the user's text and their intros joined to the preferences.
                    prefix += token + "\n"
                if not _has_prefs_section(local_text + ("\n" + token if token else "")):
                    # A `## My preferences` heading ahead of the blocks: the session hook reads the FIRST one, so
                    # text carried below can never become the injected preferences, whatever it contains.
                    prefix += "\n## My preferences\n\n"
            old = local_bytes or b""
            nl = "\r\n" if old.count(b"\r\n") * 2 > old.count(b"\n") else "\n"     # a CRLF file stays CRLF
            appended = (prefix.replace("\n", nl) + "".join(
                build_block(n, d, t, intro.format(run=run_rel, n=n, src=src.label), c, nl)
                for _, (n, d, t, intro, c) in blocks)).encode("utf-8", "surrogateescape")
            sep = b"" if not old or old.endswith(b"\n") else (b"\r\n" if b"\r\n" in old else b"\n")
            new = old + sep + appended
            try:
                _write_atomic(local_path, new)
            except OSError as e:
                raise _Failed("could not write {}: {}".format(LOCAL_KB, e))
            if not local_path.read_bytes().startswith(old):
                raise _Failed("{} did not read back starting with its old content".format(LOCAL_KB))
            for key, _ in blocks:
                carried[key] = {"run": run_rel}
                out("  {}: carried into {} (marked block)".format(key.split(":")[0], LOCAL_KB))
                log.append("carried the 3.x {} into {} (marked block)".format(key.split(":")[0], LOCAL_KB))
    except BaseException as e:                # noqa: BLE001 -- ANY end but success is recorded, then re-raised
        rc = 1
        if not isinstance(e, (_Failed, OSError)):
            unexpected = e
        what = "{}: {}".format(type(e).__name__, e) if unexpected else str(e)
        word = "FAILED" if isinstance(e, Exception) else "INTERRUPTED"     # Ctrl+C, SystemExit, ...
        out("migrate-3x: {} -- {}. What was done before that is listed in {}/MIGRATION.txt; nothing was "
            "deleted. Running it again starts a new folder.".format(word, what, run_rel))
        log += ["", "{}: {}".format(word, what), "The steps above this line were done; nothing after it was."]
    finally:
        log += ["", "Nothing was deleted. Every file this run wrote is listed above with its undo."]
        try:
            if run_dir.is_dir():
                with open(run_dir / "MIGRATION.txt", "x", encoding="utf-8", errors="surrogateescape") as fh:
                    fh.write("\n".join(log) + "\n")
            state["carried"], state["copied"] = carried, copied
            if rc and run_dir.is_dir():
                state["failed_run"] = run_rel
            else:
                state.pop("failed_run", None)
            _write_atomic(root / STORE / "state.json",
                          (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        except OSError as e:
            rc = 1
            out("migrate-3x: could not write the record ({}) -- the backups in {} are intact".format(e, run_rel))
    if unexpected is not None:
        raise unexpected                      # recorded above; the traceback is the rest of the report
    if rc == 0:
        out("migrate-3x: done. Backups and the undo steps: {}/MIGRATION.txt".format(run_rel))
        _codex_note(out)
    return rc


def _codex_note(out):
    out("  Codex: 3.x shipped no Codex hook definitions, so there is nothing to re-approve -- approve the 4.0 "
        "hooks once in Codex's /hooks.")


def _say(line: str):
    """Print a line whatever it holds: undecodable bytes (surrogates) and characters the console cannot show
    become replacement marks ON SCREEN only -- the files keep the exact bytes."""
    try:
        data = line.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeEncodeError:                # a surrogate surrogateescape cannot map (not U+DC80..U+DCFF)
        data = line.encode("utf-8", "backslashreplace").decode("utf-8")
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(data.encode(enc, "replace").decode(enc, "replace"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Carry a 3.x user's own edits into a 4.0 install. Deletes nothing.")
    ap.add_argument("--root", type=Path, default=HERE.parent, help="the install (default: this toolkit's folder)")
    ap.add_argument("--from", dest="from_dir", type=Path, default=None,
                    help="read the 3.x files from this folder (laid out like an install) instead")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen; write nothing")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    if not ((root / "CLAUDE.md").is_file() or (root / ".claude").is_dir()):
        print("migrate-3x: {} is not a toolkit install (no CLAUDE.md, no .claude/)".format(root), file=sys.stderr)
        return 2
    if a.from_dir is not None and not a.from_dir.is_dir():
        print("migrate-3x: --from {} is not a folder".format(a.from_dir), file=sys.stderr)
        return 2
    return run(root, a.from_dir.resolve() if a.from_dir else None, a.dry_run, out=_say)


if __name__ == "__main__":
    sys.exit(main())
