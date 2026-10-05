# Routes harness (regression harness for the guard-routes review)

The harness the guard-routes red team (2026-10-04) used to find the holes branch rt-fix-b closed. It feeds
hand-written payloads to the REAL engine entry (`tools/skyguard/run.py`), as Claude Code (`protect-bash`,
`protect-files`, `protect-mcp`) or as Codex (`pre-tool`), and prints one verdict per case. It is kept so the
review can be re-run against a later engine and the two outputs diffed; it is not part of the pytest suite
(no file here is named `test_*.py`).

| File | What it is |
|---|---|
| `setup_fake.py` | builds the fake install the cases point at: `C:/Temp/rt-routes/Game` (with `Data/Plugin.esp`, `Data/Skyrim.esm`, `SkyrimVR.exe`) and a settings folder `C:/Temp/rt-routes/cfg` whose `Saves/` holds `Existing.ess`; writes the install's `skyrim-paths.env` |
| `cases1.py` .. `cases9.py` | each writes `C:/Temp/rt-routes/casesN.json`: a list of `{id, agent, tool, ti, cwd, expect}` (`expect` = the decisions that count as caught) |
| `h.py` | the runner: `python h.py casesN.json` prints `id agent/tool input => verdict`, marking `<<<<<< HOLE?` where the verdict is not in `expect` |

Run (verdict-level only: nothing is executed, every case is a hook decision):

```
python setup_fake.py
python cases1.py
RT_REPO=<toolkit checkout> python -B h.py C:/Temp/rt-routes/cases1.json > out1.txt
```

`RT_REPO` is required (no default: the old one named the reviewer's own checkout). A case names the user's home
as `<HOME>`, the token `tools/payload-capture/scrub.py` uses; `h.py` fills it with the fake home it hands the engine
(`C:/Temp/rt-routes/home`). `tests/test_payload_fixtures.py` fails if any file here carries the home of whoever runs
it. The paths are absolute and Windows-shaped because the review ran on Windows; a case's `expect` is the red team's judgement at the time, not a spec. The spec for
each closed hole is its corpus row (`tests/guard_corpus/intended.jsonl`, task `RTB`) or Codex route row
(`tests/test_skyguard_codex_routes.py`), and a `HOLE?` mark here is a lead to check, not a failure.
Known holes still marked at rt-fix-b's end belong to branch rt-fix-a (interpreter and wrapper deletes, path
aliases) or are listed in `docs/guard-coverage.md`.
