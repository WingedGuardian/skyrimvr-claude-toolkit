# agent/ — the source of every agent-facing file

Edit here, never in the generated copies. Then run `python scripts/gen_agent_trees.py`, and commit
both the source and the regenerated files. CI runs `--check` and fails on any difference.

| Source | Generated |
|---|---|
| `instructions/core.md` + `instructions/claude.md` | `CLAUDE.md` |
| `instructions/core.md` + `instructions/codex.md` | `AGENTS.md` (what Codex reads) |
| `skills/<name>/` | `.claude/skills/<name>/` |
| `guards/adapters/claude/*.sh` (thin wrappers; the rules live in `tools/skyguard/`) | `.claude/hooks/*.sh` |
| `targets/claude/*` | `.claude/*` (no header: JSON and `.example` files have no comment syntax) |
| `skills/<name>/` | `.agents/skills/<name>/` (Codex reads the same skills) |
| `guards/adapters/codex/*` (`skyguard-entry.ps1`, `.sh`; the rules live in `tools/skyguard/`) | `.codex/hooks/*` |
| `targets/codex/hooks.json`, `config.toml` | `.codex/hooks.json` (no header, never edited by hand: see below), `.codex/config.toml` |
| `tools/skyguard/codex_rules.py` (`render()`) | `.codex/rules/skyrim.rules` |

`targets/codex/FROZEN.sha256` is the freeze ledger for the two Codex hook definitions, not a product: nothing is
generated from it. `hooks.json` is rendered ONLY by `tools/skyguard/codex_definitions.py render`; the generator
copies the template and never edits it. `.codex/**` is pinned LF by a path rule in `.gitattributes` (the
generator writes LF and `*.ps1` is otherwise CRLF). `--target all` (the default; what `--check` and CI use) is
the union of both targets and refuses two targets emitting one path; `--target claude|codex` renders one.

Skills (`skills/<name>/SKILL.md`): `create-mod`, `devbench`, `inspect-esp`, `port-to-vr`, `skyrim-audio`,
`skyrim-bsa`, `skyrim-context`, `skyrim-mcm`, `skyrim-nif`, `skyrim-save`, `triage-and-conflicts`, `xelib`.
`devbench`, `triage-and-conflicts` and `xelib` hold what used to be bulk in `core.md`; the core points at
them from its *Where the rest lives* table.

**Budget.** Codex silently truncates `AGENTS.md` past 32,768 bytes, so `--check` also fails when the
worst-case INSTALLED size of header + `core.md` + any addendum exceeds that. "Installed" adds a
2,560-byte reserve for what `setup.sh` expands (paths, the MO2 block; sized against the real `setup.sh`
by `tests/test_instruction_budget.py`); `instructions/codex.md` is counted at its real size. `python scripts/gen_agent_trees.py --budget` prints every row and its headroom. Over budget?
Move bulk out of `core.md` into a skill (a recipe, read before the work) or `docs/` (background), and
leave a one-line rule plus a pointer behind. Never raise the constants to make it pass.

**Neutrality.** `core.md` is shared by every agent, and so are the `devbench`, `triage-and-conflicts`
and `xelib` skills: no Claude-only tool names, variables, slash commands or config paths in them.
Agent-specific text goes in `instructions/<agent>.md` (`claude.md` into `CLAUDE.md` only, `codex.md` into
`AGENTS.md` only). Every skill body is judged too, since Codex reads them all; the one sanctioned Claude-only
spelling is ``the mod the user named (`$ARGUMENTS` in Claude Code)``. `tests/test_instruction_neutrality.py` is the list of banned constructs, each with a positive
example, plus ordinary wording that must never trip them.

`--check` is for this repository. In an installed copy, `setup.sh` fills placeholders in the
generated files, and `--check` reports that as drift on purpose.
