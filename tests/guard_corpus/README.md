# Guard corpus

Two files, with two different jobs.

| File | What it is | Who writes it |
|---|---|---|
| `expected.jsonl` | what the **old bash hooks** decided, for a fixed set of inputs. **Frozen.** | `scripts/guard-corpus/record.py`, once, from the reference hooks |
| `intended.jsonl` | the **spec for the new behaviour**: decision, rule ids and exact message text | **by hand**, one row per deliberate change |

## `expected.jsonl` (frozen)

One JSON object per line, sorted by `id`:
`{"id","layout","hook","payload","decision","text"}`. `decision` is `allow|advise|ask|deny`; `text` is the
reason or context, or `""`. It records yesterday's behaviour, right or wrong; a verdict that looks wrong is
not edited here but **superseded** from `intended.jsonl` (below).

Plan 5 rewrites every message, so after Task 1 a legacy row is checked by **decision and rule id**, never by
text. `legacy_rules.json` maps each frozen text template to its rule id; a joined advise text (` | `) maps to
the set of ids. The engine's ids come from its rule trace (`Audit.fired`). A legacy row that a later task
deliberately changes is skipped by that check once an intended row names it in `supersedes`.

Regenerate (do not, normally): `python scripts/guard-corpus/record.py [--hooks <dir>] [--out <jsonl>]`.
Re-record **only** with `--hooks` pointing at the reference hooks. Never re-record against the new engine to
make a failing test pass; that erases the yardstick.

### How legacy rows are sourced

- `testhooks-*`: every literal `cmd(...)` / `fpath(...)` argument in `tests/test_hooks.py`, pulled out by
  AST. New hook tests there must use local helpers, or they would grow this file's input set.
- `dialect-*`: ten destructive command shapes crossed with seven spellings of the game path, plus a read and
  an `.esp` write for each.
- `shell-*`: each protect-bash rule with a positive and a negative input, under the `declared` and `config`
  layouts (`@config` suffix).
- `write-*`: each protect-files rule. `payload-*`: empty, invalid, array and tool-less payloads.
- `root-*`: deleting the install's own `Data/` under the `undeclared` and `metachar` layouts.

Layouts (`declared`, `undeclared`, `metachar`, `config`) are built fresh in a temp dir by `make_install`.
Intended rows may also use `malformed` (Task 6: an unreadable protection key) and `mo2` (Task 7: Mod Organizer
`mods/` and `overwrite/` declared at `D:/MO2/...`) and `saves` (Task 11: a declared config dir `<install>/cfg` whose
`Saves/` holds one save, `Existing.ess`, so the DevBench save-exists check has a folder to read).

## `intended.jsonl` (hand-authored)

```json
{"id": "t07-rel-rm-data", "layout": "declared", "hook": "protect-bash.sh",
 "payload": {"tool_name": "Bash", "cwd": "<install>", "tool_input": {"command": "rm -rf Data/meshes/x"}},
 "payload_note": "install-relative", "decision": "deny", "rules": ["S-DEL-INSTALL"],
 "text": "Blocked: ...", "supersedes": null, "task": "T7", "why": "relative delete into Data/ (P4)"}
```

**Authoring rule.** `decision`, `rules` and `text` are written by hand from the plan's rule and message
table. **Never record an intended row from the engine.** A row copied from engine output asserts only that
the engine agrees with itself; it cannot go red for the reason it exists. If a row fails, fix the engine or
fix the row on the strength of the table, never by pasting what the engine printed.

- `text` is exact, compared byte for byte through the real wrapper (`record.fire`).
- `rules` is the set of rule ids that fire; an `allow` row names none.
- `supersedes` names a legacy row whose verdict this row deliberately changes. The payload, hook and layout
  must be identical, so the row really re-specifies that case. The supersession must be **live**: the engine
  must differ from the legacy verdict (decision or rule set), or `test_supersessions_are_live` fails.
- `why` and `task` are required and non-empty.
- A legacy row may be superseded by **at most one** intended row. A supersession whose decision is WEAKER than
  the legacy row's (deny > ask > advise > allow) is refused unless the intended row carries `"loosens": true`
  and a non-empty `reason`; a loosened rule is then visible in the row and in `intent_diff.py`, never silent.
- `payload_note: "install-relative"` stores `<install>` in place of the temp install's real path; every
  occurrence in the payload, `payload.cwd` included, is filled in at fire time (`record.row_payload`).

**Review rule.** `python scripts/guard-corpus/intent_diff.py <rev-range>` prints every added or changed
intended row as a table (id, payload shape, old decision, new decision, rules, why). The reviewer reads that
table **before** the code diff, and checks each row's decision against the plan's rule table and each
`supersedes` against that task's pricing section.

**Debugging aid.** `python scripts/guard-corpus/record.py --check-intended [--id X]` fires the intended rows
and prints actual against intended. It never writes a file.
