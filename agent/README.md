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

`--check` is for this repository. In an installed copy, `setup.sh` fills placeholders in the
generated files, and `--check` reports that as drift on purpose.
