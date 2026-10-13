"""Hook health from staged telemetry: invocation -> receipt -> evaluation -> emission.

A 3.x heartbeat proved only that a payload arrived. The failures that matter happen AFTER receipt: an
engine that crashes, a wrapper the agent's timeout killed (on Codex that lets the command RUN), an
output the agent could not parse. So every invocation leaves a record per stage, one line per file,
overwritten, in `<project>/.claude/backups/.hook-heartbeat/<agent>/`:

    <hook>.invoked    wrapper   "<t> <inv>"
    <hook>.received   wrapper   "<t> <inv> bytes=<n>"
    <hook>.evaluated  engine    "<t> <inv> <status> <decision> <code>"
    <hook>.emitted    wrapper   "<t> <inv> ok"            after it validated the engine's output
    <hook>.failed     either    "<t> <inv> <stage> <text>"  stage: receipt | python | engine | emission
    <hook>.latency    wrapper   "<inv> <t_invoked> <t_emitted>", APPENDED; trimmed by the engine

`<t>` is Unix seconds with a fractional part. A failure clears only when a NEWER COMPLETED evaluation
proves recovery; a later payload, a later invocation or an equal timestamp prove nothing.

This module never raises into a hook's decision path: `record` and `trim_latency` swallow OSError.
The 3.x files (`.hook-heartbeat/<hook>`, `<hook>.blind`) are ignored.

Per-session records (Task 13), one file per agent session, `<agent>/sessions/<session_id>.log`:

    <t> start <source> <transcript_path>     SessionStart (the transcript path is the REST of the line)
    <t> <hook> <tool_name>                   every guarded tool call, written before the engine decides
    <t> refused <tool_name>                  Codex only: written after the engine REFUSED that call (a refused
                                             call leaves no transcript item, so it is excluded from "hooked")

`coverage()` compares them with the agent's own transcript (tools/skyguard/transcripts.py): a call that
ran with no record is a call the hook never saw (E24: some Windows surfaces emit no PreToolUse at all).
Only the 20 newest session files per agent are kept.

CLI:  python tools/skyguard/health.py report --root <install> [--stale-min N] [--json] [--codex-config P]
Exit: 1 if any hook is DEGRADED, a Codex hook is untrusted/modified/disabled/never invoked, or a session's
      coverage is DEGRADED; else 2 if any is UNMEASURABLE, the trust reading is unknown, or the directory is
      unreadable; else 0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

STATES = ("HEALTHY", "STALE", "DEGRADED", "NOT EXERCISED", "UNMEASURABLE", "NEVER INVOKED")
COVERAGE_STATES = ("HEALTHY", "DEGRADED", "NOT EXERCISED", "UNMEASURABLE")
STAGES = ("invoked", "received", "evaluated", "emitted", "failed")
STATUS_VALUES = ("evaluated", "partial", "failed")
CLAUDE_HOOKS = ("protect-files", "protect-bash", "protect-mcp", "backup-before-edit", "snapshot-before-tool",
                "session-kb-guard")
CODEX_HOOKS = ("pre-tool", "session-start")
SKEW_S = 300           # a stamp further ahead of "now" than this is a clock fault, not a record
LATENCY_MAX_BYTES = 256 * 1024
LATENCY_KEEP_LINES = 2000
MIN_LATENCY_SAMPLES = 5
HB_REL = Path(".claude") / "backups" / ".hook-heartbeat"
SESSIONS_KEEP = 20
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
# The hook whose record counts as "this call was guarded", per agent. Claude runs several hooks per call
# (protect-*, backup, snapshot), so only the two guards are counted -- once each per matched tool.
COUNTING_HOOKS = {"claude": ("protect-bash", "protect-files", "protect-mcp"), "codex": ("pre-tool",)}
# fix-limits 3: an agent adapted through ADAPTING.md declares itself by its adapter file,
# tools/adapters/<folder>/adapter.py, whose AGENT_NAME slot names its heartbeat folder
# .claude/backups/.hook-heartbeat/adapted/<name>/ (tools/adapting/adapter_template.py, _hb_dir). The template writes
# `pre-tool.<stage>` there and the neutral entry writes the engine's `neutral-pre-tool.evaluated` beside it; those are
# the records read for it. Coverage against a transcript is not measured for it: no reader knows its transcript.
ADAPTED_DIR = "adapted"
ADAPTED_HOOKS = ("pre-tool",)
ADAPTED_EVALUATED = "neutral-pre-tool"          # tools/skyguard/neutral.py TELEMETRY_ROLE (asserted by a test)
ADAPTERS_REL = Path("tools") / "adapters"
_AGENT_NAME = re.compile(r"""^AGENT_NAME\s*=\s*(?:"([^"\\\n]*)"|'([^'\\\n]*)')""", re.MULTILINE)
# Tools the INSTALLED Claude matchers cover (.claude/settings.json: Bash|PowerShell|Monitor -> protect-bash,
# Edit|Write|NotebookEdit -> protect-files; Plan 5), and every MCP tool whose name contains devbench in any case,
# or dev-bench / dev_bench / dev.bench -> protect-mcp (Plan 8 T6: was the prefix mcp__devbench). The pattern is the
# installed matcher's text, character for character (tests/test_skyguard_coverage.py); Claude matchers take no inline
# flags, so case is spelled with character classes.
CLAUDE_COVERED_TOOLS = ("Bash", "PowerShell", "Monitor", "Edit", "Write", "NotebookEdit")
CLAUDE_COVERED_MCP = re.compile(r"mcp__.*[Dd][Ee][Vv][-_. ]?[Bb][Ee][Nn][Cc][Hh].*")


def _claude_covered(tool):
    return tool in CLAUDE_COVERED_TOOLS or (isinstance(tool, str) and CLAUDE_COVERED_MCP.fullmatch(tool) is not None)
# Codex: every tool call is matched (matcher ".*"). Known gaps, listed rather than counted:
CODEX_KNOWN_GAPS = ("write_stdin (input to a running program; not measured on 0.160.0, never counted)",
                    "commands typed with ! in the TUI (measured: they fire no PreToolUse)")


if __package__ in (None, ""):      # run as a script: `python tools/skyguard/health.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class Unmeasurable(Exception):
    """A record exists but cannot be read as one of the formats above."""


# --------------------------------------------------------------------------------------------------
# writing (engine side) -- must never raise into the decision path
# --------------------------------------------------------------------------------------------------
def record(dirpath, hook, stage, inv, detail=""):
    """Overwrite `<hook>.<stage>` with "<now> <inv> <detail>". Returns True when written."""
    try:
        d = Path(dirpath)
        d.mkdir(parents=True, exist_ok=True)
        line = "{:.6f} {}".format(time.time(), inv)
        if detail:
            line += " " + " ".join(str(detail).split())
        with open(str(d / "{}.{}".format(hook, stage)), "w", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        return True
    except (OSError, ValueError):
        return False


def trim_latency(dirpath, hook):
    """Keep the last LATENCY_KEEP_LINES lines once the file passes LATENCY_MAX_BYTES."""
    try:
        p = Path(dirpath) / "{}.latency".format(hook)
        if p.stat().st_size <= LATENCY_MAX_BYTES:
            return
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-LATENCY_KEEP_LINES:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def session_record(dirpath, session_id, hook, what, transcript=None):
    """Append one session line (see the module docstring). `transcript` is not None for the start line, where
    `hook` is "start" and `what` is the source. Never raises; returns True when written."""
    try:
        if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
            return False
        d = Path(dirpath) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        f = d / (session_id + ".log")
        fresh = not f.exists()
        what = "-".join(str(what or "-").split()) or "-"
        line = "{:.6f} {} {}".format(time.time(), hook, what)
        if transcript is not None:
            line += " " + str(transcript).replace("\r", " ").replace("\n", " ")
        with open(str(f), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        if fresh:
            prune_sessions(dirpath)
        return True
    except (OSError, ValueError):
        return False


def prune_sessions(dirpath, keep=SESSIONS_KEEP):
    """Keep the `keep` newest session files."""
    try:
        files = sorted((p for p in (Path(dirpath) / "sessions").glob("*.log") if p.is_file()),
                       key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        for p in files[keep:]:
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


# --------------------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------------------
_TIME = re.compile(r"[0-9]+(\.[0-9]+)?\Z")
_SEQ = re.compile(r"([0-9]{1,15})\.")
_MIN_FIELDS = {"invoked": 2, "received": 3, "evaluated": 5, "emitted": 3, "failed": 3}


def read(path, stage="invoked"):
    """Parse one record -> (t, inv, rest) or None when the file does not exist. Raises Unmeasurable."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Unmeasurable("{}: unreadable ({})".format(path.name, e.__class__.__name__))
    fields = (text.splitlines() or [""])[0].split()
    if len(fields) < _MIN_FIELDS[stage]:
        raise Unmeasurable("{}: too few fields {!r}".format(path.name, text[:60]))
    try:
        # float() alone accepts "20260909_15" (underscores) as 2026090915.0: a 3.x stamp must not parse
        if not _TIME.match(fields[0]):
            raise ValueError(fields[0])
        t = float(fields[0])
    except ValueError:
        raise Unmeasurable("{}: timestamp {!r} is not a number".format(path.name, fields[0][:30]))
    if not math.isfinite(t):
        raise Unmeasurable("{}: timestamp is not finite".format(path.name))
    rest = fields[2:]
    if stage == "evaluated" and rest[0] not in STATUS_VALUES:
        raise Unmeasurable("{}: unknown status {!r}".format(path.name, rest[0][:30]))
    return t, fields[1], rest


def _completed(recs):
    """Time of the newest COMPLETED evaluation: status 'evaluated' AND emitted for the same invocation."""
    ev, em = recs.get("evaluated"), recs.get("emitted")
    if ev and em and ev[2][0] == "evaluated" and em[1] == ev[1]:
        return ev[0]
    return None


def _never_finished(recs, now, kill_s):
    """Invoked, never emitted, and older than any hook timeout: the agent killed it (Codex then RAN the call)."""
    inv, em = recs.get("invoked"), recs.get("emitted")
    return bool(inv) and (not em or (em[1] != inv[1] and em[0] < inv[0])) and now - inv[0] > kill_s


def _seq(inv):
    """The invocation sequence number the wrapper embeds in the id (`<seq>.<pid>-<rand>`), else None."""
    m = _SEQ.match(inv)
    return int(m.group(1)) if m else None


def _stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def adapted_agents(root):
    """-> [(name, heartbeat folder name or None)], one per tools/adapters/<folder>/adapter.py, by folder name.

    The name is the adapter's AGENT_NAME slot: the first `AGENT_NAME = "<literal>"` line before `# SLOTS-END` (the
    file is read, never imported or run). Its folder is that name with every character but [A-Za-z0-9._-] made `_`,
    exactly as the template's _hb_dir does. With no literal there, the records cannot be located: (folder text, None)."""
    try:
        dirs = sorted(p for p in (Path(root) / ADAPTERS_REL).iterdir() if (p / "adapter.py").is_file())
    except OSError:
        return []
    out = []
    for d in dirs:
        try:
            text = (d / "adapter.py").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = _AGENT_NAME.search(text.split("# SLOTS-END", 1)[0])
        if m:
            name = m.group(1) if m.group(1) is not None else m.group(2)
            out.append((name, re.sub(r"[^A-Za-z0-9._-]", "_", name)))
        else:
            out.append((d.name + " (no AGENT_NAME literal)", None))
    return out


def _files(agent):
    """The record-name overrides an agent's hooks need (hook_state `files`): an adapted agent's evaluation is the
    neutral entry's own record."""
    return {"evaluated": ADAPTED_EVALUATED} if agent.startswith(ADAPTED_DIR + "/") else None


def hook_state(d, hook, now, stale_s=7200, kill_s=150, files=None):
    """-> (state, detail). The rules run in the order they are numbered. `files` maps a stage to the record name
    that holds it when that is not `hook` (an adapted agent's `evaluated`)."""
    d = Path(d)
    recs = {}
    try:
        for stage in STAGES:
            r = read(d / "{}.{}".format((files or {}).get(stage, hook), stage), stage)
            if r is not None:
                recs[stage] = r
    except Unmeasurable as e:
        # 2. a record that does not parse is never green (a truncated stamp once sorted low and cleared a failure)
        return "UNMEASURABLE", str(e)

    # 2b. a stamp from the future is a clock fault (or a hand-written record): it would otherwise outrank every
    #     real record and keep a hook "fresh" forever, or hide a failure
    for stage, rec in recs.items():
        if rec[0] > now + SKEW_S:
            return "UNMEASURABLE", "clock skew: {} is stamped {} (now is {})".format(stage, _stamp(rec[0]), _stamp(now))

    # 1. nothing at all: the hook has proven nothing either way
    if not recs:
        return "NOT EXERCISED", "no telemetry yet -- proves nothing either way"

    done = _completed(recs)
    failed = recs.get("failed")

    # 3. a failure is current unless a completed evaluation is STRICTLY newer (equal stamps prove no order).
    #    "Newer" is the wrapper's per-hook invocation sequence when both ids carry one (`<seq>.<pid>-<rand>`):
    #    a clock stepped BACK cannot hide a failure that way. Ids without a sequence fall back to the stamps.
    #    fix-limits 8: the wrappers take <seq> from the previous record with no lock, so two calls at the same moment
    #    can share one. Equal sequences on DIFFERENT ids are concurrent calls, ordered only by their stamps; the SAME
    #    id never clears its own failure (a killed-run or empty-payload record is written under the current call).
    cleared = False
    if failed and done is not None:
        ds, fs = _seq(recs["evaluated"][1]), _seq(failed[1])
        if recs["evaluated"][1] == failed[1]:
            cleared = False
        elif ds is not None and fs is not None and ds != fs:
            cleared = ds > fs
        else:
            cleared = done > failed[0]
    if failed and not cleared:
        stage = failed[2][0]
        text = " ".join(failed[2][1:])
        return "DEGRADED", "{} failed at {}{}".format(stage, _stamp(failed[0]), (": " + text) if text else "")

    # 4. invoked and never finished
    if _never_finished(recs, now, kill_s):
        t = recs["invoked"][0]
        return "DEGRADED", ("invoked at {} and never finished -- killed by the agent's timeout? "
                            "On Codex that lets the command RUN".format(_stamp(t)))

    # 5. the newest evaluation did not finish its job
    ev = recs.get("evaluated")
    if ev and ev[2][0] != "evaluated":
        return "DEGRADED", "{}: {}".format(ev[2][0], ev[2][2] if len(ev[2]) > 2 else "?")

    if done is None:
        return "NOT EXERCISED", "no completed evaluation yet (in flight, or no verdict was ever emitted)"

    # 6. the last completed evaluation is old
    if now - done > stale_s:
        return "STALE", "last completed evaluation {} min ago".format(int((now - done) // 60))

    # 7. healthy; an older failure stays visible so intermittent trouble has somewhere to show up
    detail = "last completed evaluation {} s ago".format(int(now - done))
    if failed:
        detail = "recovered; was DEGRADED at {} by {} failure ({})".format(_stamp(failed[0]), failed[2][0], detail)
    return "HEALTHY", detail


def latency(d, hook, last=200):
    """-> (n, p50_ms, p95_ms) over the newest `last` valid samples; (0, None, None) when there are none."""
    ms = []
    try:
        lines = (Path(d) / "{}.latency".format(hook)).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0, None, None
    for line in lines[-last:]:
        f = line.split()
        try:
            a, b = float(f[1]), float(f[2])
        except (IndexError, ValueError):
            continue
        if math.isfinite(a) and math.isfinite(b) and b >= a:
            ms.append((b - a) * 1000.0)
    if not ms:
        return 0, None, None
    ms.sort()
    return len(ms), ms[len(ms) // 2], ms[min(len(ms) - 1, int(0.95 * len(ms)))]


def _read_session(path):
    """-> (transcript_path or None, {hook: {tool: n}}). Raises Unmeasurable on a line that does not parse."""
    transcript, hooked = None, {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise Unmeasurable("{}: unreadable ({})".format(Path(path).name, e.__class__.__name__))
    for line in text.splitlines():
        if not line.strip():
            continue
        f = line.split(None, 3)
        if len(f) < 3 or not _TIME.match(f[0]):
            raise Unmeasurable("{}: bad session line {!r}".format(Path(path).name, line[:60]))
        if f[1] == "start":
            if len(f) == 4:
                transcript = f[3]
        else:
            per = hooked.setdefault(f[1], {})
            per[f[2]] = per.get(f[2], 0) + 1
    return transcript, hooked


def _coverage_rows(project, agent, last=5):
    """-> [(session, surface, hooked, called, state, why)], newest session first, at most `last`.

    `called` counts only the tools the installed matchers cover (Claude: CLAUDE_COVERED_TOOLS; Codex: every
    call). `hooked` is how many of those the guard recorded. HEALTHY hooked >= called, DEGRADED hooked <
    called (calls ran with no hook: the E24 shape; for Claude judged per tool), NOT EXERCISED no calls,
    UNMEASURABLE the transcript is missing, has no start line, or is not a shape the parser knows."""
    from skyguard import transcripts
    d = Path(project) / HB_REL / agent / "sessions"
    try:
        files = sorted((p for p in d.glob("*.log") if p.is_file()),
                       key=lambda p: (p.stat().st_mtime, p.name), reverse=True)[:last]
    except OSError:
        return []
    rows = []
    for p in files:
        sid = p.stem
        try:
            transcript, per_hook = _read_session(p)
            if not transcript:
                raise Unmeasurable("no start line: the transcript path was never recorded")
            surface, counts = transcripts.count_calls(agent, transcript)
        except (Unmeasurable, transcripts.Unmeasurable) as e:
            rows.append((sid, "?", 0, 0, "UNMEASURABLE", str(e)))
            continue
        hooked_by = {}
        for h in COUNTING_HOOKS[agent]:
            for tool, n in per_hook.get(h, {}).items():
                hooked_by[tool] = hooked_by.get(tool, 0) + n
        if agent == "claude":
            tools = [t for t in counts if _claude_covered(t)]
            called = sum(counts.get(t, 0) for t in tools)
            # per tool: a guard line for one tool cannot make up for an unseen call of another
            hooked = sum(min(hooked_by.get(t, 0), counts.get(t, 0)) for t in tools)
        else:
            called = sum(counts.values())
            # A refused call is recorded by the hook but leaves no transcript item: exclude it from both sides.
            refused = per_hook.get("refused", {})
            hooked = sum(max(0, n - refused.get(tool, 0)) for tool, n in hooked_by.items())
        short = hooked < called
        state = "NOT EXERCISED" if called == 0 else "DEGRADED" if short else "HEALTHY"
        rows.append((sid, surface, hooked, called, state, ""))
    return rows


def coverage(project, agent, last=5):
    """-> [(session, surface, hooked, called, state)] -- see _coverage_rows for the rules."""
    return [r[:5] for r in _coverage_rows(project, agent, last)]


# --------------------------------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------------------------------
def _installed(root):
    """-> {agent: [hook, ...]} for every agent whose hook set is installed under `root`."""
    out = {}
    hooks = root / ".claude" / "hooks"
    if hooks.is_dir():
        have = [h for h in CLAUDE_HOOKS if (hooks / (h + ".sh")).is_file()]
        if have:
            out["claude"] = have
    if (root / ".codex" / "hooks.json").is_file():
        out["codex"] = list(CODEX_HOOKS)
    for name, folder in adapted_agents(root):                  # fix-limits 3: what each adapter declares
        out["{}/{}".format(ADAPTED_DIR, folder if folder is not None else name)] = list(ADAPTED_HOOKS)
    return out


def _writable(d):
    """Can a hook record here? Probed by writing, because os.access lies on Windows."""
    probe = Path(d) / ".canary-probe"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _has_records(d, hook):
    """Is there ANY stage record for this hook? (A bad one still counts: something ran.)"""
    return any((Path(d) / "{}.{}".format(hook, st)).exists() for st in STAGES)


TRUST_BAD = ("untrusted", "modified", "disabled")


def _codex_trust(root, codex_config):
    from skyguard import codex_trust
    cfg = Path(codex_config) if codex_config else codex_trust.codex_home() / "config.toml"
    return codex_trust.trust_report(root / ".codex" / "hooks.json", cfg, root)


def report(root, stale_min=120, as_json=False, now=None, out=None, codex_config=None, coverage_last=5):
    """Print the report; return the exit code."""
    out = out or sys.stdout
    now = time.time() if now is None else now
    root = Path(root)
    installed = _installed(root)
    if not installed:
        print("no installed hooks under {} (.claude/hooks, .codex/hooks.json) -- cannot check".format(root),
              file=sys.stderr)
        return 2
    hb = root / HB_REL
    rows, unreadable = [], False
    trust = _codex_trust(root, codex_config) if "codex" in installed else None
    if hb.exists() and not os.access(str(hb), os.R_OK | os.X_OK):
        unreadable = True
    for agent, hooks in installed.items():
        d = hb / agent
        unwritable = d.is_dir() and not _writable(d)
        for h in hooks:
            if unreadable:
                state, detail = "UNMEASURABLE", "telemetry directory is not readable"
            elif agent.endswith(" (no AGENT_NAME literal)"):
                state, detail = "UNMEASURABLE", ("its adapter.py has no AGENT_NAME literal in the slots, so the "
                                                 "folder its heartbeats go to is unknown")
            elif unwritable:
                # the wrappers swallow a failed write, so a frozen directory would read HEALTHY/STALE forever
                state, detail = "UNMEASURABLE", "telemetry directory is not writable -- hooks cannot record here"
            else:
                state, detail = hook_state(d, h, now, stale_s=stale_min * 60, files=_files(agent))
                if (agent == "codex" and state == "NOT EXERCISED" and trust["overall"] == "trusted"
                        and not any(_has_records(d, x) for x in hooks)):
                    # Approved, yet NO Codex hook has left a single stage record: the entry never started (one
                    # hook silent while the other ran just means no tool call yet). Two unsupported setups do
                    # exactly this and FAIL OPEN -- a group policy forcing AllSigned, and WSL-hosted Codex.
                    state = "NEVER INVOKED"
                    detail = ("Codex says this hook is approved, but it has never run (no record at all). If a "
                              "Codex session has run since approval, the hook is not starting -- see README, "
                              "'Using with Codex' (AllSigned policy, WSL): every call runs unchecked")
            n, p50, p95 = latency(d, h)
            rows.append({"agent": agent, "hook": h, "state": state, "detail": detail,
                         "latency_n": n, "p50_ms": p50, "p95_ms": p95})
    coverage_rows = {}
    for agent in installed:
        if not unreadable and agent in COUNTING_HOOKS:   # an adapted agent has no transcript reader: not measured
            coverage_rows[agent] = [dict(zip(("session", "surface", "hooked", "called", "state", "why"), r))
                                    for r in _coverage_rows(root, agent, coverage_last)]
    legacy = []
    if hb.is_dir():
        try:
            legacy = sorted(p.name for p in hb.iterdir()
                            if p.is_file() and (p.name in CLAUDE_HOOKS or p.name.endswith(".blind")))
        except OSError:
            unreadable = True
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    cov_counts = {s: sum(1 for rs in coverage_rows.values() for r in rs if r["state"] == s)
                  for s in COVERAGE_STATES}
    trust_bad = bool(trust) and (trust["overall"] in TRUST_BAD
                                 or (trust["overall"] == "unrunnable" and not trust.get("unrendered")))
    trust_unknown = bool(trust) and trust["overall"] == "unknown"
    if counts["DEGRADED"] or counts["NEVER INVOKED"] or cov_counts["DEGRADED"] or trust_bad:
        code = 1
    elif counts["UNMEASURABLE"] or cov_counts["UNMEASURABLE"] or unreadable or trust_unknown:
        code = 2
    else:
        code = 0
    if as_json:
        print(json.dumps({"rows": rows, "counts": counts, "legacy_ignored": legacy, "trust": trust,
                          "coverage": coverage_rows, "coverage_counts": cov_counts, "exit": code}, indent=1),
              file=out)
        return code
    print("=" * 62, file=out)
    print("HOOK CANARY -- did each guard see its payload, reach a verdict, and deliver it?", file=out)
    print("=" * 62, file=out)
    for r in rows:
        lat = ""
        if r["latency_n"] >= MIN_LATENCY_SAMPLES:
            lat = "  [latency p50 {:.0f} / p95 {:.0f} ms, n={}]".format(r["p50_ms"], r["p95_ms"], r["latency_n"])
        print("  {:<8} {:<22} {:<13} {}{}".format(r["agent"], r["hook"], r["state"], r["detail"], lat), file=out)
    if legacy:
        print("  (ignored {} legacy 3.x heartbeat file(s): {})".format(len(legacy), ", ".join(legacy)), file=out)
    if trust is not None:
        _print_trust(trust, rows, out)
    for agent, rs in coverage_rows.items():
        _print_coverage(agent, rs, out)
    adapted = [a for a in installed if a.startswith(ADAPTED_DIR + "/")]
    if adapted:
        print("-" * 62, file=out)
        print("ADAPTED AGENTS ({}): self-verified (ADAPTING.md section 7). Their rows prove the adapter reached and "
              "delivered verdicts; coverage is not measured, so only the live canaries prove the hook runs for "
              "every tool.".format(", ".join(a.split("/", 1)[1] for a in adapted)), file=out)
    print("-" * 62, file=out)
    print("healthy: {HEALTHY}   stale: {STALE}   degraded: {DEGRADED}   unmeasurable: {UNMEASURABLE}   "
          "not-exercised: {NOT EXERCISED}   never-invoked: {NEVER INVOKED}".format(**counts), file=out)
    print(file=out)
    if counts["DEGRADED"]:
        print("RESULT: DEGRADED HOOK(S) -- a guard ran and did NOT complete a delivered verdict. Common causes:", file=out)
        print("        Python missing or its path unconfigured (re-run setup.sh), an engine crash, or the agent", file=out)
        print("        killing a slow hook. An unfinished hook can let the command RUN.", file=out)
    elif counts["NEVER INVOKED"]:
        print("RESULT: CODEX HOOKS APPROVED BUT NEVER INVOKED -- if Codex has run a session, nothing is guarded.",
              file=out)
    elif trust_bad:
        print("RESULT: CODEX HOOKS NOT RUNNING -- see the trust table above.", file=out)
    elif cov_counts["DEGRADED"]:
        print("RESULT: DEGRADED COVERAGE -- a session ran tool calls that no hook recorded (a surface that "
              "emits no PreToolUse).", file=out)
    elif code == 2:
        print("RESULT: UNMEASURABLE -- nothing can be proven for: {}".format("; ".join(_caveats(
            rows, coverage_rows, trust, unreadable, trust_unknown, unmeasurable_only=True)) or "see above"),
            file=out)
    else:
        cav = _caveats(rows, coverage_rows, trust, unreadable, trust_unknown)
        if cav:
            print("RESULT: UNPROVEN -- not proven healthy: {}".format("; ".join(cav)), file=out)
        else:
            print("RESULT: OK -- {} hook(s) proven to receive, evaluate and deliver".format(counts["HEALTHY"]),
                  file=out)
    return code


def _caveats(rows, coverage_rows, trust, unreadable, trust_unknown, unmeasurable_only=False):
    """Every reason the report cannot say plain OK, by name. Empty only when nothing is left unproven."""
    out = []
    um = ["{} {}".format(r["agent"], r["hook"]) for r in rows if r["state"] == "UNMEASURABLE"]
    um += ["{} session {}".format(a, r["session"][:13]) for a, rs in coverage_rows.items() for r in rs
           if r["state"] == "UNMEASURABLE"]
    if unreadable:
        um.append("the telemetry directory")
    if trust_unknown:
        um.append("Codex trust")
    if um:
        out.append("UNMEASURABLE: " + ", ".join(um))
    if unmeasurable_only:
        return out
    stale = ["{} {}".format(r["agent"], r["hook"]) for r in rows if r["state"] == "STALE"]
    if stale:
        out.append("STALE (no recent completed evaluation): " + ", ".join(stale))
    codex_on = trust is not None and not trust.get("unrendered")
    if trust is not None and trust.get("unrendered"):
        out.append("Codex not configured (hooks.json is not rendered; Codex support is Windows-only)")
    if codex_on and not any(r["agent"] == "codex" and r["hook"] == "pre-tool" and r["state"] == "HEALTHY"
                            for r in rows):
        out.append("Codex pre-tool has not completed an evaluation, so nothing proves the Codex guard runs "
                   "(session-start alone does not): run one Codex tool call and re-run")
    if not out and not any(r["state"] == "HEALTHY" for r in rows):
        out.append("no hook has completed an evaluation yet: trigger one shell command and one file edit "
                   "and re-run")
    return out


def _print_trust(trust, rows, out):
    print("-" * 62, file=out)
    print("CODEX TRUST (read from Codex's config.toml; Codex skips an unapproved hook silently)", file=out)
    overall = trust["overall"]
    if trust.get("unrendered"):
        print("  Codex is NOT CONFIGURED here: {}".format(trust["root_problem"]), file=out)
        return
    ran = any(r["agent"] == "codex" and r["state"] not in ("NOT EXERCISED", "NEVER INVOKED") for r in rows)
    if overall == "unrunnable":
        print("  Codex hooks CANNOT RUN from this folder: {}".format(trust["root_problem"]), file=out)
    elif overall in TRUST_BAD and not ran:
        print("  Codex hooks NOT RUNNING (untrusted or unapproved): approve them with /hooks in Codex.", file=out)
    elif overall in TRUST_BAD:
        print("  Codex hooks are {} per config.toml, yet they have run: the trust reading may be out of "
              "date.".format(overall), file=out)
    elif overall == "unknown":
        print("  Codex trust is UNKNOWN: {}".format(trust.get("error") or "see the rows"), file=out)
    else:
        print("  all Codex hooks trusted", file=out)
    for h in trust.get("hooks", []):
        print("    {:<10} {}".format(h["status"], ":".join(h["key"].rsplit(":", 3)[-3:])), file=out)


def _print_coverage(agent, rs, out):
    print("-" * 62, file=out)
    print("COVERAGE, {} (last {} session(s): tool calls in the transcript vs calls a hook recorded)".format(
        agent, len(rs)), file=out)
    if not rs:
        print("  no sessions recorded yet", file=out)
    for r in rs:
        extra = "  ({})".format(r["why"]) if r["why"] else ""
        print("  {:<13} {:<28} hooked {:>3} / called {:>3}  {}{}".format(
            r["session"][:13], r["surface"], r["hooked"], r["called"], r["state"], extra), file=out)
    if agent == "codex":
        print("  claimed surfaces: exec, tui (a surface is claimed only after a live probe; the desktop app and "
              "IDE extensions are not). Coverage lists only sessions the hook SAW: a surface whose hooks never "
              "fire leaves no record here.", file=out)
        print("  not counted: " + "; ".join(CODEX_KNOWN_GAPS), file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--root", required=True)
    rp.add_argument("--stale-min", type=int, default=int(os.environ.get("HOOK_CANARY_STALE_MIN", "120") or 120))
    rp.add_argument("--json", action="store_true")
    rp.add_argument("--codex-config", default=None, help="default: $CODEX_HOME/config.toml or ~/.codex/config.toml")
    a = ap.parse_args(argv)
    return report(a.root, a.stale_min, a.json, codex_config=a.codex_config)


if __name__ == "__main__":
    sys.exit(main())
