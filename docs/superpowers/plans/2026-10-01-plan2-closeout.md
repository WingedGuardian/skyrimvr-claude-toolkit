# Plan 2 close-out: rulings and deferred minors

Plan 2 (skyguard engine) completed 2026-10-02; full suite on a87ce27: 1052 passed, 27 skipped (harness modules, run separately: 33 passed). Extracted from the execution ledger before its workspace was deleted. Deferred minors are a backlog for Plans 4/5; two are already resolved (noted).

## Rulings

- Setup: Ruling: execute on master, no worktree — the user's global CLAUDE.md says commit directly on master in this repo, never push — cost if wrong: commits need moving to a branch (cheap, local).
- Pre-flight: Ruling: T5 backup-name expectation is `TS__C__x_y.txt` (`:` and `\` both become `_`, as bash's sed does) — plan text typo — cost if wrong: none (the bash reference decides).
- Task 1: review → Needs fixes. Ruling: Important#1 (extractor misses ~60 parametrized test_hooks payloads; plan-mandated) — fix: the spec says the corpus is seeded from test_hooks — cost if wrong: none.
- Task 1: Ruling: re-grade minor#2 (corpus rows deletable/editable undetected) and #3 (recorder writes all-INERT corpus without jq) to Important — both let the yardstick weaken silently during the port (Task 7) — cost if wrong: two small tests.
- Task 1: Ruling: fold #4 (content-hash ids, needed by #2), #5 (undeclared/negative twins) and `*.jsonl text eol=lf` into round 1 — cheap, same re-record — cost if wrong: none.
- Task 3: Ruling: grep._compile uses re.ASCII|re.IGNORECASE — matches GNU grep for whitespace + case folding (88/98 probes) — cost if wrong: none, measured.
- Task 3: Ruling: accept residual \b-adjacent-to-non-ASCII divergence (e.g. `Champollioné …pex`, `cat My.espé`): Python is STRICTER in every measured case and GNU grep itself is inconsistent there — documented, not emulated — cost if wrong: an extra deny/advise on exotic Unicode spellings.
- Task 3: Ruling: corpus text is newline-normalised (\r\n→\n) in record.classify — Windows jq turns embedded newlines into CRLF in the OLD hooks; that platform artifact would make the corpus Windows-only (CI Linux leg fails) and block the port. Decisions are unaffected (only `-delete(\s|$)` sees the \r, and \r is \s) — cost if wrong: echoed command text of multi-line commands differs by \r.
- Task 3: Ruling: fold minors 2 (config-layout rows in the equivalence test) and 3 (all message constants vs bash; ASK audit line asserted) into round 1 — cheap — cost if wrong: none.
- Task 3: Ruling: row unicode-lead-eacute is locale-dependent for the OLD bash (UTF-8 Linux grep allows) — harmless: commits are unpushed until after Task 7, when both platforms run the ASCII Python engine whose verdict equals the recorded one — cost if wrong: one CI row to re-record.
- Task 4: Ruling: Windows Git-Bash grep is the reference for CR semantics (CR before LF = line ending; WSL/Linux grep differs) — grep.grep strips ONE trailing \r per line; corpus rows pin it — Windows is where the hooks run — cost if wrong: Linux-only divergence on a literal trailing CR.
- Task 4: Ruling: NUL bytes in tool_input values are dropped in the Claude adapter's extraction (bash $() silently drops NUL) — carried to Task 6 — cost if wrong: none.
- Task 4: Ruling: fold minors (pin the corpus filter to exactly the payload-* exclusion; reword the garbled comment) into the CR round — cheap — cost if wrong: none.
- Task 4: Ruling: accept rstrip of ALL trailing \r (measured: Git Bash grep matches txt$ with 1-3 trailing CRs) — the round-1 ruling said pin Git Bash's behaviour — cost if wrong: none.
- Task 5: Ruling: fold minors 3 (backup mkdir inside try → bash FAILED text), 4 (session audit line from raw bytes), 5 (child stdin=DEVNULL), 6 (st_mtime_ns for -newer) and 7 (mutant-surviving test gaps) into round 1 — each restores bash behaviour or pins it — cost if wrong: small test churn.
- Task 6: Ruling: decode stdin with errors="replace" (jq emits U+FFFD) — cost if wrong: none.
- Task 6: Ruling: payload_empty = (stdin with NULs removed).rstrip("\r\n") == "" — Windows Git Bash $(cat) strips trailing \n and a final \r\n; parse the NUL-stripped text — cost if wrong: a stray-CR-only payload counted empty (BLIND line) on Linux.
- Task 6: Ruling: fold minors 4 (tool_name "unknown" only for None/False) and 7 (assert the decision in test_run_protect_bash) — cost if wrong: none.
- Task 7: implementer DONE (d3ffd6a, 6969440). Plan-code bugs fixed by implementer: ${PY//\//} mangled paths (now only normalised on the failure branch); engine addressed via ${ROOT_WIN:-$ROOT} (native python can't open MSYS /tmp paths — root-metachar-self row). Rulings applied: jq informational in setup; missing Python → loud WARNING, no exit; ci.yml keeps jq for the frozen bash reference tests.
- Task 7: Ruling: round 1 = Critical#1 fix + #2 settings.json-form invocation tests + mutant; Important#3 setup.sh re-writes a stale/unrunnable PY path on re-run; fold minors #4 (escape & in sed replacement) and #7 (mutation for session.py ignoring kb-guard rc) — cost if wrong: none.
- Task 8: Ruling: MSYS argv conversion of the OLD hooks' echoed text — old bash passes $COMMAND to native jq.exe via `--arg`, and Git Bash rewrites path-shaped argv (`/c/x`→`C:/x`, a line-continuation backslash→`/`, `C:/x` in a VAR=… list→`C;C:\Program Files\Git\x`); the new engine echoes the command verbatim. Decision identical in every measured case (2/11000, both advise=advise) — text-only, new is the faithful one — cost if wrong: none for verdicts; the old reason text was corrupted, not the new.
- Task 8: Ruling: `\b`-next-to-non-ASCII (`cat My.espé` inside a heredoc body) old allow → new advise, 2/11000 — covered by the Task 3 residue ruling; new is STRICTER — cost if wrong: one extra advisory.
- Task 9: latency FAIL (worst p95 2770 ms vs gate 1000). Split MEASURED: Store-alias python +~130 ms/call; wrapper bash forks ~390 ms; engine (py310) 58 ms median. Ruling: fix, don't weaken the gate — wrapper fork removal + setup prefers non-WindowsApps python; dispatched (opus).
- Task 9: latency fix d6b1ca1,7375946,90ccc2d — median ~halved; p95 still >1s under 73-79% load; interleaved old-vs-new A/B dispatched. Ruling: session-kb-guard runs once per SessionStart — gated separately from the per-call PreToolUse gate — cost if wrong: slow session start only.
- Final: Ruling: CHANGELOG 'Zero verdicts change' re-graded Minor→Important (a permanent record overstating a measured result) — fixed in the same pass — cost if wrong: none.
- Task 9: Ruling: latency gate PASSES on the interleaved A/B (d233ee8): NEW p95 329-358 ms per-call hooks at 52-76% load, faster than OLD at median for all 4; session-kb-guard +0.4 s median once per session, accepted — the earlier guard-latency runs (p95 1.0-2.0 s) were at 73-79% load — cost if wrong: an idle re-measure later.

## Deferred minors

- Task 1: minor (deferred): test and recorder share classify()/fire() (#6); corpus test adds ~94 s (#7).
- Task 1: minor (deferred): extractor ev() swallows exceptions — a future pytest.param/helper-built payload would be dropped silently (equality test compares against the same extractor).
- Task 2: minor (deferred): paths.read_env catches only OSError — invalid-UTF-8 env file raises UnicodeDecodeError → engine crash → wrapper fail-closed deny (bash would continue). Catch (OSError, UnicodeError). **[RESOLVED c2affe7: unreadable env file now denies explicitly]**
- Task 2: minor (deferred): read_env scoped to setup.sh's K='…' output (export/unquoted-space/$VAR/CRLF differ from bash sourcing) — add a comment.
- Task 4: minor (deferred): shell-cr-line-end records allow — weak mutation guard (the write-cr rows bite).
- Task 5: minor (deferred): snapshot _age_days stat in copy loops and rl.stat() unguarded (file vanishing between list and stat raises; bash find tolerates).
- Task 5: minor (deferred): empty/garbled .last_snapshot → bash errors (then proceeds), Python treats as 0 (same outcome).
- Task 6: minor (deferred): jq number formatting (1e5/-0/NaN/Infinity), \u007f escaping in objects, BOM/concatenated JSON (fail-closed), lone \ud800 accepted by Python but rejected by jq.
- Task 6: minor (deferred): run.py with --root "" reads .claude/skyrim-paths.env relative to cwd (wrapper always passes a root).
- Task 6: minor (deferred): tool_name 0/0.0 → "unknown" (Python 0 == False); jq gives "0" — use `is None or is False`; audit text only.
- Task 7: minor (deferred): commit d3ffd6a alone ships hooks with {{PYTHON_PATH}} while setup.sh still substitutes {{JQ_PATH}} — bisectability only; pair is consistent.
- Task 7: minor (deferred): PYTHON_PATH may pin a Microsoft Store App Execution Alias (fragile; re-run repairs it). **[RESOLVED 7375946: setup prefers a regular Python]**
- Task 7: minor (deferred): _SELF mutants cover protect wrappers + session-kb-guard; backup/snapshot wrappers' _SELF covered only by launch tests, no dedicated mutant
- Final: minor (deferred): setup.sh accepts any Python 3.x (3.6 → every hook denies 'engine failed' with no setup warning); require >=3.8.
- Final: minor (deferred): setup.sh writes PY in double quotes ($/backtick in a path expand; fails closed).
- Final: minor (deferred): pre-existing hook-timeout class (engine hang >60s → answer dropped).
