# Incremental pre-release review repairs, 2026-10-23

The five findings from the [October 22 review](2026-10-22-incremental-prerelease-review.md)
are fixed in local source and have targeted verification. This closes those five repair
requests, not the release gate. Repair branches remain unmerged; final integrated checks,
manual acceptance and publication remain held. No push, tag or release was performed.

## Repairs and recovery points

| Finding | Result | Local commit |
|---|---|---|
| R1, shared dispatch ordering and coverage | A failed/partial local restriction refuses before an ask can escape. An unguarded profile with declared save obligations refuses; an empty-plan local ask retains unguarded coverage and warnings. Ordinary evaluated asks still pass preservation and bulk approval. | Core `221292e`; Skyrim master `3cbcf6c`; X4 master `ca39a3a` |
| R2, transcript aliases | The supplied current transcript is compared by physical file identity as well as path. Pre-existing hardlink aliases are refused through shell writes and file tools, including Codex apply_patch. Reads and copy-out remain allowed. | Held guard branch `ced18c8` |
| R3, opaque program policy | An unidentifiable program naming a protected path asks without requiring a delete/write verb in the same command. Removed the unapproved extra condition. Rule metadata, Codex classification, parser-limit notes and intended corpus accompany the repair. | Held guard branch `ced18c8` |
| R4, latency gate false PASS | Nonempty tool-hook output must satisfy the existing production PreToolUse validator. Scalars, arrays, wrong objects, invalid decisions and missing refusal reasons fail. Silence, valid responses and SessionStart text retain their respective contracts. | Held installer branch `9e64a52` |
| R5, customization instructions | Documentation now distinguishes rule metadata from deciding code; changing metadata alone does not change most verdicts. | Held documentation branch `808af7c` |

Both integrated consumers pin `221292e4b92a9f1b1df21f2c5342a4a54e410be6`. All 19
vendored files in each consumer match their lock hashes and the committed core source.
The guard repair branch retains its earlier core baseline; ordinary integration must retain
the newer master pin. No repair branch was merged to obtain these results.

The guard commit includes the already frozen, uncommitted opaque-program prerequisite work
reviewed on October 22. The installer branch's unrelated pre-existing housekeeping edits and
private FIXES notes were preserved outside these commits. The health branch was unchanged.

## Measured verification

All results below are local Windows fixtures, run under the resource-budget runner. Skyrim
checks used the contained test launcher; X4 used its frozen uv environment. These are targeted
checks, not a full suite or native-host acceptance. Counts overlap where a focused group was
rechecked after an additional regression or anchor change.

| Check | Result |
|---|---|
| Core `tests/test_dispatch.py` | 18 passed. Before repair, the three new tests produced 17 failing subcases. |
| Skyrim `tests/test_shared_core_ask_order.py` | 21 passed, including one-save preservation, bulk refusal, seven unknown-plan shapes, incomplete local asks and truthful unknown coverage. |
| Skyrim payload/profile checks plus mutation-anchor inventory | 2,724 passed; most are inexpensive anchor-presence checks, not behavior tests. |
| Guard transcript/opaque/engine checks plus mutation-anchor inventory | 2,812 passed; most are anchor-presence checks. Initial transcript/opaque run reproduced 13 failures before repair. |
| Guard corpus and metadata follow-up | 5 passed; one exercises all 17 changed opaque-program corpus rows with exact verdict/message comparison. |
| Installer latency CLI stand-ins | 13 passed. Five invalid-schema cases first reproduced false PASS, then failed the gate after repair. |
| Documentation current-claim checks | 11 passed. The small prose correction was also reviewed against deciding code. |
| X4 pin and mixed-ask preservation controls | 8 passed, zero skipped; measured after the pin changed. An earlier baseline run is not counted as new-pin evidence. |
| Guard-hash freshness | Skyrim master: 97 entries current; held guard branch: 96 entries current. Different branch inventories remain explicit. |

Six new named mutation twins passed: three dispatch clauses, transcript identity, the opaque
protected-path ask, and latency schema validation. Nine other transcript twins also passed,
including the reanchored lexical-path control for a supplied transcript that does not exist
yet. Every selected twin runs its passing baseline before requiring the mutant regression
to fail; command examples in the guard fixtures are data, never executed attacks.

The installer anchor check exposed two pre-existing stale release-exclusion anchors. These
were narrowed to the current exclusion entries in `e2933a7`; both named mutation twins pass,
and the final installer anchor inventory passes all 2,757 checks. This changed test anchors,
not release contents. A first corpus selector selected no tests and is not counted as a pass;
the explicit changed-row regression above supplies that evidence.

## Remaining scope and limits

An existing hardlink to a different session's transcript, with neither its original path nor
identity supplied by the host, is not discovered by scanning all session trees. Interpreter
code that constructs paths dynamically and races after a precheck retain the documented
coverage limits. The transcript tests establish pre-operation decisions on owned fixtures.

The restored opaque-program rule can ask about legitimate tools held in variables when they
name protected paths. That follows the recorded user policy. Full historical pricing and
integrated acceptance remain part of the held batch; they cannot silently narrow this rule.

The separate House Carl root-INI archive-discovery defect (issue 11) remains a release blocker
in the [reconciliation ledger](2026-10-22-plan8-reconciliation.md). These five fixes do not
close it or the original review's remaining integration/acceptance obligations.

No installed game/configuration, real save, agent trust, public issue or live runtime was
changed. Synthetic save fixtures and their owned preservation transactions were used where
needed. [Source hashes and local commit identities](data/2026-10-23-prerelease-fixes.json)
record the repaired inputs; the October 22 failure report remains historical evidence.
