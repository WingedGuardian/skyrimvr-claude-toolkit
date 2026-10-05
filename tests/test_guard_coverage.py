"""The guard-coverage inventory (docs/guard-coverage.md) is checked against the shipped matchers (Plan 5 Task 14).

WHAT FAILS
  - a route marked guarded/partial whose tool name no PreToolUse matcher fires on (the doc claims a hook the settings lack);
  - a route marked uncovered/absent whose tool name a matcher now fires on (the doc is stale: the gap was closed);
  - a PreToolUse matcher with no row in the matcher table, or a table row with no matcher;
  - a literal tool name in a matcher that no route lists (a matcher naming a route the inventory lacks);
  - a summary count that does not equal the rows, or a status outside the key, or a row with no evidence tier.

Matchers are compared with a FULL match (measured 2026-10-04: `Edit|Write` did not fire for `NotebookEdit`; a substring
search would say it did).

EVERY CHECK CAN FAIL: `check()` is a pure function of (settings, doc text), and the mutation tests below feed it a broken
copy for each clause and require that clause's own message.
"""
import json
import re

from conftest import REPO

SETTINGS = REPO / "agent" / "targets" / "claude" / "settings.json"
DOC = REPO / "docs" / "guard-coverage.md"
STATUSES = ("guarded", "partial", "uncovered", "absent", "out-of-scope")
SEEN = ("guarded", "partial")
UNSEEN = ("uncovered", "absent")
TIERS = ("MEASURED", "READ", "INFERRED")


def split_row(line):
    """Cells of a markdown table row; a backslash-pipe is a literal pipe inside a cell."""
    body = line.strip()
    assert body.startswith("|") and body.endswith("|"), line
    inner = body[1:-1].replace(chr(92) + "|", "@@PIPE@@")
    return [c.replace("@@PIPE@@", "|").strip() for c in inner.split("|")]


def tick(cell):
    return re.findall(r"`([^`]+)`", cell)


def tables(text):
    """{heading: [rows as cell lists]} for every table under a '## ' heading (header and rule rows dropped)."""
    out, head, rows = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if head is not None:
                out[head] = rows
            head, rows = line[3:].strip(), []
        elif line.startswith("|") and head is not None:
            if re.match(r"^\|[\s:|-]+\|$", line.strip()):
                continue
            rows.append(split_row(line))
    if head is not None:
        out[head] = rows
    return out


def claude_matchers(settings):
    return [e["matcher"] for e in settings["hooks"]["PreToolUse"]]


def fires(matcher, tool):
    return re.fullmatch(matcher, tool) is not None


def check(settings, text):
    """Return a list of problems; empty means the inventory and the settings agree."""
    problems = []
    tabs = tables(text)
    matchers = claude_matchers(settings)
    mrows = next((r for h, r in tabs.items() if h.startswith("Claude Code PreToolUse matchers")), None)
    rrows = tabs.get("Routes")
    if mrows is None or rrows is None:
        return ["MISSING-TABLE: the doc has no matcher table or no Routes table"]
    mrows = mrows[1:]
    rrows = rrows[1:]
    documented = [tick(r[0])[0] for r in mrows if tick(r[0])]
    for m in matchers:
        if m not in documented:
            problems.append("MATCHER-NOT-DOCUMENTED: settings matcher %r has no row in the matcher table" % m)
    for m in documented:
        if m not in matchers:
            problems.append("MATCHER-NOT-IN-SETTINGS: the doc lists matcher %r the settings do not carry" % m)

    counts = dict.fromkeys(STATUSES, 0)
    all_tools = set()
    ids = set()
    for r in rrows:
        if len(r) != 7:
            problems.append("BAD-ROW: %d cells in %r" % (len(r), r[:1]))
            continue
        rid, agent, route, tools, status, guard, notes = r
        if rid in ids:
            problems.append("DUPLICATE-ID: %s" % rid)
        ids.add(rid)
        if status not in STATUSES:
            problems.append("BAD-STATUS: %s has status %r" % (rid, status))
            continue
        counts[status] += 1
        if not any(t in notes for t in TIERS):
            problems.append("NO-EVIDENCE-TIER: %s names none of %s" % (rid, "/".join(TIERS)))
        if status in SEEN and guard.strip().lower() in ("", "none", "n/a"):
            problems.append("GUARD-MISSING: %s is %s but names no guard" % (rid, status))
        names = [t for t in tick(tools) if t != "-"]
        all_tools.update(names)
        if agent != "claude":
            continue        # Codex rows: the Codex target is not in this branch; its matcher is `.*` (master hooks.json)
        for n in names:
            hit = [m for m in matchers if fires(m, n)]
            if status in SEEN and not hit:
                problems.append("GUARDED-BUT-UNMATCHED: %s %s is %s but no matcher fires on it" % (rid, n, status))
            if status in UNSEEN and hit:
                problems.append("UNCOVERED-BUT-MATCHED: %s %s is %s but matcher %r fires on it" % (rid, n, status, hit[0]))
    for m in matchers:
        for part in m.split("|"):
            if re.fullmatch(r"[A-Za-z_]+", part):
                if part not in all_tools:
                    problems.append("MATCHER-NAMES-UNLISTED-ROUTE: matcher %r names tool %r that no route lists" % (m, part))
            elif not any(fires(part, t) for t in all_tools):
                problems.append("MATCHER-NAMES-UNLISTED-ROUTE: pattern %r matches no listed tool" % part)

    summary = {}
    for s in STATUSES:
        mm = re.search(r"^- %s: (\d+)\s*$" % re.escape(s), text, re.M)
        summary[s] = int(mm.group(1)) if mm else None
    for s in STATUSES:
        if summary[s] != counts[s]:
            problems.append("SUMMARY-COUNT: %s says %s, the rows give %d" % (s, summary[s], counts[s]))
    return problems


def load():
    return json.loads(SETTINGS.read_text(encoding="utf-8")), DOC.read_text(encoding="utf-8")


def test_inventory_matches_the_shipped_matchers():
    settings, text = load()
    assert check(settings, text) == []


def test_inventory_has_the_routes_the_brief_names():
    _, text = load()
    rows = tables(text)["Routes"][1:]
    tools = {t for r in rows for t in tick(r[3])}
    for need in ("Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit", "mcp__devbench-vr__console"):
        assert need in tools, need
    agents = {r[1] for r in rows}
    assert {"claude", "codex", "any"} <= agents
    assert any("code-execution" in r[2] for r in rows), "an MCP code-execution route must be listed (Plan 6 Phase A)"


def test_measured_gaps_are_closed_by_a_matcher_and_the_doc_says_so():
    """NotebookEdit and Monitor were MEASURED outside every matcher (2026-10-04); Plan 5 Task 13b added both. Each must now
    be fired on by a matcher AND marked guarded, so the doc cannot drift back to calling them open."""
    settings, text = load()
    rows = {r[0]: r for r in tables(text)["Routes"][1:]}
    for rid, tool in (("C7", "NotebookEdit"), ("C8", "Monitor")):
        assert rows[rid][4] == "guarded", rid
        assert any(fires(m, tool) for m in claude_matchers(settings)), tool


# ---- mutations: one twin per clause, each must produce its own named problem ----

def _has(problems, code):
    return any(p.startswith(code) for p in problems)


def test_mutation_matcher_removed_makes_a_guarded_route_unmatched():
    settings, text = load()
    s = json.loads(json.dumps(settings))
    s["hooks"]["PreToolUse"] = [e for e in s["hooks"]["PreToolUse"] if e["matcher"] != "Edit|Write|NotebookEdit"]
    p = check(s, text)
    assert _has(p, "GUARDED-BUT-UNMATCHED"), p
    assert _has(p, "MATCHER-NOT-IN-SETTINGS"), p


def test_mutation_matcher_added_is_flagged_both_ways():
    settings, text = load()
    s = json.loads(json.dumps(settings))
    s["hooks"]["PreToolUse"].append({"matcher": "EnterWorktree", "hooks": []})
    p = check(s, text)
    assert _has(p, "UNCOVERED-BUT-MATCHED"), p      # the doc still calls EnterWorktree uncovered
    assert _has(p, "MATCHER-NOT-DOCUMENTED"), p


def test_mutation_unlisted_tool_in_a_matcher():
    settings, text = load()
    s = json.loads(json.dumps(settings))
    s["hooks"]["PreToolUse"][1]["matcher"] = "Edit|Write|NotebookEdit|MadeUpTool"
    p = check(s, text)
    assert _has(p, "MATCHER-NAMES-UNLISTED-ROUTE"), p


def test_mutation_uncovered_route_relabelled_guarded():
    settings, text = load()
    mutated = text.replace("| `EnterWorktree`, `ExitWorktree` | uncovered |", "| `EnterWorktree`, `ExitWorktree` | guarded |")
    assert mutated != text
    p = check(settings, mutated)
    assert _has(p, "GUARDED-BUT-UNMATCHED"), p
    assert _has(p, "SUMMARY-COUNT"), p


def test_mutation_summary_count_wrong():
    settings, text = load()
    mutated = text.replace("- guarded: 10", "- guarded: 11")
    assert mutated != text
    assert _has(check(settings, mutated), "SUMMARY-COUNT")


def test_mutation_bad_status_and_missing_evidence():
    settings, text = load()
    assert _has(check(settings, text.replace("| C5 | claude | Write tool | `Write` | guarded |",
                                             "| C5 | claude | Write tool | `Write` | covered |")), "BAD-STATUS")
    no_tier = re.sub(r"(\| C15 \|[^\n]*?)READ: no local mutation\.", r"\1no local mutation.", text)
    assert no_tier != text
    assert _has(check(settings, no_tier), "NO-EVIDENCE-TIER")


def test_substring_search_would_have_been_wrong():
    """Why `fires` is a full match: the pattern `Edit|Write` is a substring of nothing here, but `Edit` is inside
    `NotebookEdit`, and the measured hook did not fire for it."""
    assert re.search("Edit|Write", "NotebookEdit") is not None
    assert not fires("Edit|Write", "NotebookEdit")     # why NotebookEdit needs its own entry in the matcher
