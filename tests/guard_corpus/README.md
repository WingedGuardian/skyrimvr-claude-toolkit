# Guard corpus

`expected.jsonl` records what the **bash safety hooks** decided, before the skyguard port, for a fixed
set of inputs. One JSON object per line, sorted by `id`:
`{"id","layout","hook","payload","decision","text"}`. `decision` is `allow|advise|ask|deny`; `text` is the
reason or context, or `""`. It is the yardstick for the port: the new hooks must reproduce every
decision and every reason byte for byte. It records today's behaviour, right or wrong; a verdict that
looks wrong is an observation for a later plan, not something to edit here.

## Regenerating

`python scripts/guard-corpus/record.py [--hooks <dir>] [--out <jsonl>]`

Re-record **only** with `--hooks` pointing at the reference hooks (the bash hooks as of the port). Never
re-record against the new engine to make a failing test pass; that would erase the yardstick.

## How rows are sourced

- `testhooks-*`: every literal `cmd(...)` / `fpath(...)` argument in `tests/test_hooks.py`, pulled out by
  AST (calls with non-literal arguments are skipped).
- `dialect-*`: ten destructive command shapes crossed with seven spellings of the game path
  (forward, back, MSYS, doubled, upper-case, mixed), plus a read and an `.esp` write for each.
- `shell-*`: each protect-bash rule with a positive and a negative input, run under the `declared` and
  `config` layouts (`@config` suffix).
- `write-*`: each protect-files rule.
- `payload-*`: empty, invalid, array and tool-less payloads.
- `root-*`: deleting the install's own `Data/` under the `undeclared` and `metachar` layouts.

Layouts (`declared`, `undeclared`, `metachar`, `config`) are built fresh in a temp dir by `make_install`.

## Install-relative rows

`root-*` rows name the install by its real path, which differs on every run. They are stored with an
`<install>` template and `"payload_note": "install-relative"`; both the recorder and the test fill it
in with `install_relative()` at fire time.
