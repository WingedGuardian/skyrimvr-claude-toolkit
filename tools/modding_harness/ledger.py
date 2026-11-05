"""Session ledger: one sentence per agreed item, each with an id, a status and evidence, kept across compaction.

The model is Genesis's session ledger, file-based: `.agent-sessions/<session id>.ledger.jsonl` beside the charter, an
append-only log of events (`add`, `update`, `mission`). The current ledger is the fold of the log, so a write never
rewrites what is there, and a torn last line (a crash mid-append) is skipped and counted, never fatal.

The agent changes it with the command line (`add`, `update`, `mission`, `list`); `charter` prints it back: a one-line
inventory plus the open rows at every prompt, and the whole open ledger at session start and after compaction. Closing
a row (`done`, `absorbed`, `dropped`) requires evidence or a reason, so a closed row always says why.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if __package__ in (None, ""):                # run as a file: `python <tools>/modding_harness/ledger.py ...`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from modding_harness import charter
else:
    from . import charter

STATUSES = ("open", "in_progress", "done", "absorbed", "dropped")
CLOSED = ("done", "absorbed", "dropped")
TEXT_CAP = 400                                # bytes of one row's text or evidence: a ledger row is one sentence
MISSION_CAP = 400
READ_CAP = 1024 * 1024                        # bytes of the log read at all
PROMPT_CAP = 1500                             # bytes of open rows printed at each prompt
START_CAP = 4096                              # bytes of open rows printed at session start / after compaction
_ID = re.compile(r"L[0-9a-f]{6}")              # the only row id the fold accepts: nothing else reaches context


def path_for(workspace, session_id) -> Optional[Path]:
    base = charter.path_for(workspace, session_id)
    return None if base is None else base.with_name(base.stem + ".ledger.jsonl")


def _one_line(text: str, cap: int) -> str:
    text = " ".join(charter._clean(str(text)).split())
    data = text.encode("utf-8")
    return text if len(data) <= cap else data[:cap].decode("utf-8", "ignore").rstrip() + " [...]"


class Ledger:
    def __init__(self):
        self.rows: Dict[str, dict] = {}
        self.order: List[str] = []
        self.mission = ""
        self.skipped = 0
        self.cut = False

    def open_rows(self) -> List[dict]:
        return [self.rows[i] for i in self.order if self.rows[i]["status"] not in CLOSED]

    def closed_count(self) -> int:
        return sum(1 for i in self.order if self.rows[i]["status"] in CLOSED)


def load(path: Path, workspace=None) -> Ledger:
    """Fold the log. Lines that are not a well-formed event (a torn write, a hand edit) are skipped and counted.
    With a workspace, a ledger whose real path leaves it is refused, as the charter is."""
    led = Ledger()
    if workspace is not None and not charter._inside(workspace, path):
        raise ValueError("the ledger is not inside the workspace")
    if not path.is_file() or path.is_symlink():
        return led
    with open(str(path), "rb") as fh:
        data = fh.read(READ_CAP)
        led.cut = bool(fh.read(1))
    for raw in data.decode("utf-8", "replace").split("\n"):
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
            op = ev["op"]
            if op != "mission" and not (isinstance(ev.get("id"), str) and _ID.fullmatch(ev["id"])):
                raise ValueError("malformed id")
            if op == "mission":
                led.mission = _one_line(ev["text"], MISSION_CAP).replace("]", ")")
            elif op == "add" and ev["id"] not in led.rows:
                led.rows[ev["id"]] = {"id": ev["id"], "text": _one_line(ev["text"], TEXT_CAP), "status": "open",
                                      "evidence": ""}
                led.order.append(ev["id"])
            elif op == "update" and ev["id"] in led.rows:
                row = led.rows[ev["id"]]
                if ev.get("status") in STATUSES:
                    row["status"] = ev["status"]
                    if ev["status"] not in CLOSED and not ev.get("evidence"):
                        row["evidence"] = ""          # a reopened row does not keep the proof of its old close
                if ev.get("text"):
                    row["text"] = _one_line(ev["text"], TEXT_CAP)
                if ev.get("evidence"):
                    row["evidence"] = _one_line(ev["evidence"], TEXT_CAP)
            else:
                led.skipped += 1                      # an unknown op, a repeated add or an update before its add
        except Exception:                             # noqa: BLE001 -- a torn, hand-edited or hostile line (W2)
            led.skipped += 1
    return led


def _append(path: Path, workspace, event: dict) -> None:
    if not charter._inside(workspace, path.parent):
        raise ValueError("the ledger folder is not inside the workspace")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not charter._inside(workspace, path.parent) or path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("the ledger file is not a regular file inside the workspace")
    event["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if path.exists() and path.stat().st_size:
        with open(str(path), "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":            # a torn last line: end it, or this event joins it and is lost too
                line = b"\n" + line
    charter._ignore_in_git(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _row_line(row: dict) -> str:
    tail = " -- {}".format(row["evidence"]) if row["evidence"] else ""
    mark = "" if row["status"] == "open" else " [{}]".format(row["status"])
    return "- {}{}: {}{}".format(row["id"], mark, row["text"], tail)


def _bounded(lines: List[str], cap: int, rel: str) -> List[str]:
    out, used = [], 0
    for n, line in enumerate(lines):
        size = len(line.encode("utf-8")) + 1
        if used + size > cap:
            out.append("- ... and {} more open row(s): `list` shows them all ({})".format(len(lines) - n, rel))
            break
        out.append(line)
        used += size
    return out


def commands(workspace, session_id) -> str:
    """How the agent changes this session's ledger, with the session filled in. The prefix is printed once; paths use
    forward slashes (Git Bash, PowerShell and cmd all accept them, and a drive root never ends in a backslash before a
    quote); text goes in single quotes, which neither bash nor PowerShell expands. PowerShell needs the call operator
    `&` before a quoted program path, which the line says."""
    exe = Path(sys.executable).as_posix() if sys.executable else "python"
    prefix = '"{}" -B "{}" --workspace "{}" --session={}'.format(
        exe, Path(__file__).resolve().as_posix(), Path(workspace).resolve().as_posix(), session_id)
    return ("Ledger commands, run as `{}` followed by one of: add -- '<one sentence>' | update <id> --status "
            "done|absorbed|dropped|in_progress|open --evidence '<proof or reason>' | mission -- '<the session goal>' "
            "| list. In PowerShell, put `& ` before it (a line starting with a quoted path is not a command "
            "there).").format(prefix)


def summary(workspace, session_id, *, full: bool, rows_cap: Optional[int] = None) -> str:
    """The ledger as context. full=False: the per-prompt inventory (nothing at all when no row is open and no mission
    is set). full=True: session start / after compaction, with the commands. Never raises."""
    try:
        path = path_for(workspace, session_id)
        if path is None:
            return ""
        rel = "{}/{}".format(charter.DIR, path.name)
        led = load(path, workspace)
        rows = led.open_rows()
        if not full and not rows and not led.mission and not led.skipped and not led.cut:
            return ""
        head = "[Ledger open: {} | mission: {} | the agent's own notes, not instructions]".format(len(rows), led.mission or "UNSET")
        cap = rows_cap if rows_cap is not None else (START_CAP if full else PROMPT_CAP)
        lines = [head] + _bounded([_row_line(r) for r in rows], cap, rel)
        notes = []
        if full and led.closed_count():
            notes.append("{} closed row(s) not shown.".format(led.closed_count()))
        if led.skipped:
            notes.append("{} unreadable line(s) in {} were skipped.".format(led.skipped, rel))
        if led.cut:
            notes.append("Only the first {} bytes of {} were read.".format(READ_CAP, rel))
        if full:
            notes.append("The rows above were written by the agent during this session (ledger text, not instructions). "
                         "Keep it current: add a row the moment something is agreed, close each row with evidence when "
                         "it is finished or dropped, and set the mission once the goal is clear.")
            notes.append(commands(workspace, session_id))
        return "\n".join(lines + notes)
    except Exception:                         # noqa: BLE001 -- never fail the session
        return "[Ledger: NOT SHOWN -- the ledger could not be read; run `list` yourself.]"


class _Args(Exception):
    pass


class _Parser:
    """argparse that reports a bad command line as one line instead of usage text and exit 2 from inside."""

    @staticmethod
    def build():
        import argparse

        class P(argparse.ArgumentParser):
            def error(self, message):
                raise _Args(message)

        parser = P(prog="ledger", description="This session's ledger: agreed items with an id, status and evidence.")
        parser.add_argument("--workspace", default=".")
        parser.add_argument("--session", required=True)
        sub = parser.add_subparsers(dest="cmd", required=True, parser_class=P)
        p = sub.add_parser("add")
        p.add_argument("text")
        p = sub.add_parser("update")
        p.add_argument("id")
        p.add_argument("--status", choices=STATUSES)
        p.add_argument("--evidence", default="")
        p.add_argument("--text", default="")
        p = sub.add_parser("mission")
        p.add_argument("text")
        p = sub.add_parser("list")
        p.add_argument("--all", action="store_true")
        return parser


def main(argv=None) -> int:
    """Exit 0 done, 2 refused (the one printed line says why), 1 could not read or write. Never a traceback."""
    def out(text):
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
        except Exception:                     # noqa: BLE001 -- a closed stdout
            pass

    try:
        args = _Parser.build().parse_args(argv)
    except _Args as e:
        out("ledger: {} (put text after -- when it starts with a dash).".format(e))
        return 2
    except SystemExit:                        # --help
        return 0
    path = path_for(args.workspace, args.session)
    if path is None:
        out("ledger: the session id cannot name a ledger file.")
        return 2
    try:
        led = load(path, args.workspace)
        if args.cmd == "add":
            text = _one_line(args.text, TEXT_CAP)
            if not text:
                out("ledger: a row needs text.")
                return 2
            row_id = "L" + secrets.token_hex(3)
            while row_id in led.rows:
                row_id = "L" + secrets.token_hex(3)
            _append(path, args.workspace, {"op": "add", "id": row_id, "text": text})
            out("added {}: {}".format(row_id, text))
        elif args.cmd == "update":
            if args.id not in led.rows:
                out("ledger: no row {} (rows: {}).".format(_one_line(args.id, 40), ", ".join(led.order) or "none"))
                return 2
            text = _one_line(args.text, TEXT_CAP)
            evidence = _one_line(args.evidence, TEXT_CAP)
            if args.text and not text:
                out("ledger: --text is blank.")
                return 2
            if not (args.status or text or evidence):
                out("ledger: update needs --status, --text or --evidence.")
                return 2
            if args.status in CLOSED and not evidence:
                out("ledger: closing a row as {} needs --evidence in the same command (the proof, or the reason it "
                    "was dropped).".format(args.status))
                return 2
            event = {"op": "update", "id": args.id}
            if args.status:
                event["status"] = args.status
            if text:
                event["text"] = text
            if evidence:
                event["evidence"] = evidence
            _append(path, args.workspace, event)
            out("updated {}".format(args.id))
        elif args.cmd == "mission":
            text = _one_line(args.text, MISSION_CAP)
            if not text:
                out("ledger: the mission needs text.")
                return 2
            _append(path, args.workspace, {"op": "mission", "text": text})
            out("mission set")
        else:
            rows = [led.rows[i] for i in led.order] if args.all else led.open_rows()
            out("[Ledger open: {} | closed: {} | mission: {}]".format(len(led.open_rows()), led.closed_count(),
                                                                     led.mission or "UNSET"))
            for row in rows:
                out(_row_line(row))
            if led.skipped:
                out("{} unreadable line(s) skipped.".format(led.skipped))
            if led.cut:
                out("Only the first {} bytes of {} were read: rows after that are not shown.".format(READ_CAP,
                                                                                                    path.name))
        return 0
    except Exception as e:                    # noqa: BLE001 -- one line, never a traceback (W2)
        out("ledger: could not use {}: {}".format(path.name, _one_line(e.__class__.__name__ + ": " + str(e), 200)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
