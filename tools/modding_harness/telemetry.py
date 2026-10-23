"""Best-effort receipt evidence, never a permission or host-trust authority.

No command, arguments, transcript, environment, or affected paths are recorded.
Session/call identifiers are hashed. A receipt proves this process ran; an absent
receipt cannot distinguish a skipped hook from host/pre-launch termination.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path


def identifier(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def label(value):
    return re.sub(r"[^A-Za-z0-9_.:/-]", "?", str(value))[:160]


class Telemetry:
    def __init__(self, workspace):
        self.path = Path(workspace) / ".modding-agent-harness/coverage.sqlite3"

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.1)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS receipt (utc REAL, session TEXT, call TEXT, "
                               "host TEXT, version TEXT, tool TEXT, surface TEXT, event TEXT, decision TEXT, "
                               "evaluation TEXT, validation TEXT, source TEXT, trust TEXT)")
        except BaseException:
            connection.close()
            raise
        return connection

    def record(self, action, event, result=None, source="", trust="unknown"):
        """Return receipt count for this unknown surface, or None on unavailable logging.

        Every occurrence is recorded. Adapters may announce counts 1,2,4,8,... to
        avoid repeating the same context on every occurrence; reports retain all.
        Trust/source are caller declarations and must be presented that way.
        """
        if event not in ("pre", "post", "session", "failure", "preview"):
            raise ValueError("unknown receipt event")
        connection = None
        try:
            connection = self._connect()
            session = identifier(action.session_id)
            with connection:
                connection.execute("INSERT INTO receipt VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    time.time(), session, identifier(action.tool_call_id), label(action.host),
                    label(action.host_version), label(action.tool), label(action.surface), event,
                    result.decision if result else "", result.evaluation if result else "",
                    result.validation if result else "", label(source), label(trust)))
            if event == "pre" and result and result.evaluation == "unguarded":
                return connection.execute("SELECT count(*) FROM receipt WHERE session=? AND host=? "
                    "AND tool=? AND event='pre' AND evaluation='unguarded'",
                    (session, label(action.host), label(action.tool))).fetchone()[0]
            return 0
        except (OSError, sqlite3.Error):
            return None
        finally:
            if connection is not None:
                connection.close()

    def report(self, session_id):
        if not self.path.exists():
            return {"evidence": "unavailable", "reason": "no receipts; host interception is unproven"}
        connection = None
        try:
            connection = self._connect()
            rows = connection.execute("SELECT call,host,version,tool,surface,event,decision,evaluation,"
                                      "validation,source,trust FROM receipt WHERE session=? ORDER BY utc,rowid",
                                      (identifier(session_id),)).fetchall()
        except (OSError, sqlite3.Error):
            return {"evidence": "unavailable", "reason": "receipt database unreadable"}
        finally:
            if connection is not None:
                connection.close()
        pre, post, unknown, declarations = {}, {}, {}, set()
        uncorrelated = 0
        for call, host, version, tool, surface, event, decision, evaluation, validation, source, trust in rows:
            declarations.add((host, version, source, trust))
            key = (host, call)
            if event in ("pre", "post"):
                if not call:
                    uncorrelated += 1
                elif event == "pre":
                    pre[key] = decision
                else:
                    post[key] = validation
            if event == "pre" and evaluation == "unguarded":
                name = host + ":" + tool
                unknown[name] = unknown.get(name, 0) + 1
        return {"evidence": "process receipts only" if rows else "unavailable", "receipts": len(rows),
                "correlated_calls": len(pre.keys() & post.keys()),
                "allowed_pre_without_post": sum(d == "allow" and k not in post for k, d in pre.items()),
                "post_without_pre": len(post.keys() - pre.keys()), "uncorrelated_receipts": uncorrelated,
                "unknown_occurrences": unknown,
                "caller_declarations": [dict(zip(("host", "version", "source", "trust"), values))
                                        for values in sorted(declarations)],
                "limits": ["absent post may be delayed or unsupported; it is not a validation pass",
                           "receipt and caller trust labels do not prove host enforcement",
                           "Codex stdin continuations have no qualified fresh pre-tool interception"]}


def announce(count):
    return count is None or count > 0 and count & (count - 1) == 0
