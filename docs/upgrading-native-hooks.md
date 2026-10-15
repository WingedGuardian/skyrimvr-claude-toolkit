# Native hook configuration during a reviewed upgrade

Read [ADAPTING.md](../ADAPTING.md) for staging, explicit choices, quiescence,
rollback and qualification limits. These are native configuration formats for
Claude Code and Codex, independent of the agent performing the upgrade.

An installed `.claude/hooks/protect-save-access.py` is user policy. Its optional
registration at `.modding-agent-harness/local-policies.json` has exactly these
fields; replace the descriptive SHA placeholder with the full source digest:

```json
{
  "v": 1,
  "kind": "legacy-skyrim-save-guard-v1",
  "path": ".claude/hooks/protect-save-access.py",
  "sha256": "FULL_SHA256_OF_THE_RETAINED_SOURCE"
}
```

Registration alone does not qualify retiring its parallel native handler.
Keep it until controlled-load, helper and transfer routes have passed the
replacement qualification described in ADAPTING.md.

The exact reviewed policy's controlled request scopes and canonical declared
helper scopes have fixture qualification. Other policy revisions remain
restrictive until qualified. This does not change host trust, execute the
controller, or establish that removing the native handler is safe. A declaration
for an opaque helper requires full absolute pair paths, operation, logical count,
current session and full command hash from a qualified human reader; it never
comes from tool arguments or agent-authored provenance.

`--owned-pre-groups FILE` reads a versioned object whose `files` maps
`.claude/settings.json` and/or `.codex/hooks.json` to lists of complete verified
previous `PreToolUse` groups. Each group must match in full, including matcher,
handler order, command, Windows command, timeout and asynchronous attributes.
Do not infer ownership from a familiar command name or copy a modified group
into that file and describe it as a verified previous definition.

For an existing native definition, replacing it uses an explicit
`merged_settings` choice. The core reviews both definition files and also
`.claude/settings.local.json`. Unresolved pre-tool handlers in any reviewed
configuration refuse application even when that file is kept. Unknown post-tool
handlers and unrelated settings remain in the merged proposal.

For the exact reviewed Windows policy, `upgrade-stage --integrate-local-policy`
can stage a reproducible proposal containing the source-hash registration and
both native definitions. It validates complete groups, retains every private
source/helper and requires the full candidate guard runtime. It also reviews
local settings; unknown pre-handlers remain a refusal. Each proposed file needs
an explicit `reviewed_settings` choice. See ADAPTING.md for the retained-handler
and native host qualification requirements. This option performs no deployment
or host trust changes.
