# Skyrim OpenCode V1 adapter

Generated from `agent/targets/opencode/SKYRIM-OPENCODE.md`.

This CLI plugin follows the OpenCode V1 1.18.34 source contract. Fixtures exercise
its transport; blocking inside a running host remains unqualified. Desktop and V2
are unsupported. A skipped or failed plugin load provides no enforcement.

The session system prompt contains `Skyrim GUARDS LIVE (opencode plugin v1)` when
the plugin loads. That banner proves loading only. Known mutations use the same
Python game rules and synchronous save preservation as the portable entry.
Unknown tools warn and allow; receipts record every observed occurrence and
warnings appear at counts 1, 2, 4, 8 and so on. Unsupported approval readers cannot
authorize bulk save changes. Post receipts do not imply validation succeeded.

Use Python 3.10 or newer. Optional environment settings: `SKYRIM_PYTHON`,
`SKYRIM_NO_PYTHON_FALLBACK=1`, `SKYRIM_OPENCODE_TIMEOUT_S` (default 60 seconds).
Configure the actual shell in OpenCode itself. The plugin refuses recognized
mutations when its adapter cannot run. It cannot stop the host skipping a plugin.

For another coding agent, use `python tools/agent-harness.py capabilities` and
the Action v1 JSON contract on `check` stdin. The integrating host must honor
the result before running the operation. Installation alone is no proof.
