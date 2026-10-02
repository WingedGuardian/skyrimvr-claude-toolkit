# agent/ — the source of every agent-facing file

Edit here, never in the generated copies. Then run `python scripts/gen_agent_trees.py`, and commit
both the source and the regenerated files. CI runs `--check` and fails on any difference.

| Source | Generated |
|---|---|
| `instructions/core.md` + `instructions/claude.md` | `CLAUDE.md` |
| `instructions/agents-pointer.md` | `AGENTS.md` |
| `skills/<name>/` | `.claude/skills/<name>/` |
| `guards/adapters/claude/*.sh` (thin wrappers; the rules live in `tools/skyguard/`) | `.claude/hooks/*.sh` |
| `targets/claude/*` | `.claude/*` (no header: JSON and `.example` files have no comment syntax) |

Skills (`skills/<name>/SKILL.md`): `create-mod`, `devbench`, `inspect-esp`, `port-to-vr`, `skyrim-audio`,
`skyrim-bsa`, `skyrim-context`, `skyrim-mcm`, `skyrim-nif`, `skyrim-save`, `triage-and-conflicts`, `xelib`.
`devbench`, `triage-and-conflicts` and `xelib` hold what used to be bulk in `core.md`; the core points at
them from its *Where the rest lives* table.

**Budget.** Codex silently truncates `AGENTS.md` past 32,768 bytes, so `--check` also fails when the
worst-case INSTALLED size of header + `core.md` + any addendum exceeds that. "Installed" adds a
2,560-byte reserve for what `setup.sh` expands (paths, the MO2 block; sized against the real `setup.sh`
by `tests/test_instruction_budget.py`) and, until `instructions/codex.md` exists, a 2,048-byte reserve
for it. `python scripts/gen_agent_trees.py --budget` prints every row and its headroom. Over budget?
Move bulk out of `core.md` into a skill (a recipe, read before the work) or `docs/` (background), and
leave a one-line rule plus a pointer behind. Never raise the constants to make it pass.

**Neutrality.** `core.md` is shared by every agent, and so are the `devbench`, `triage-and-conflicts`
and `xelib` skills: no Claude-only tool names, variables, slash commands or config paths in them.
Agent-specific text goes in `instructions/<agent>.md` (today `claude.md`, generated into `CLAUDE.md`
only). `tests/test_instruction_neutrality.py` is the list of banned constructs, each with a positive
example, plus ordinary wording that must never trip them.

`--check` is for this repository. In an installed copy, `setup.sh` fills placeholders in the
generated files, and `--check` reports that as drift on purpose.
