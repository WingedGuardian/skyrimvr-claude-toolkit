# Incremental pre-release review, 2026-10-22

**Verdict: CHANGES REQUIRED.** Five confirmed findings: four medium, one low. One affects
the integrated shared core; the other four concern held repair work. This is an incremental
review of previously unreviewed changes, not final release sign-off.

The review used the software `release-review` scope rules and was performed directly by one
reviewer, without delegation. The last published version remains `v3.9.1`. The prior eight-lane
review covers `v3.9.1..b396d5c`; current master is `c4b6eb5` (662 commits after the last tag,
nine beyond that reviewed baseline). The four repair branches contribute 58 further commits.
Their uncommitted guard/installer work was frozen and inspected too, without merging it.

## Source and coverage

| Tree | Frozen HEAD | Changed paths versus b396d5c |
|---|---|---:|
| master | `c4b6eb5` | 18 |
| guard repair | `ee53ea2` plus recorded dirty bytes | 30, including the untracked opaque-command test |
| health repair | `b28c57e` | 35 |
| installer repair | `c37d959` plus recorded dirty bytes | 42 |
| documentation repair | `795ba23` | 41 |

The [input manifest](data/2026-10-22-prerelease-review-inputs.json) binds these 166 path versions
to their raw SHA256 values. Full private source snapshots, original diffs, probe programs and
JSON results are retained in the local `prerelease-review-20261022` evidence directory.
All five original trees' tracked bytes, heads and dirty status matched their initial captures
after the probes. Only this report and its sanitized input manifest were added afterwards.

The review focused on new runtime/control-path deltas and surrounding implementations: preservation
ordering and rendering, transcript and opaque-command rules, archive classification/install,
cleanup/retirement, restore behavior, diagnostics, test barriers, release exclusions and user
documentation. Previous review coverage is reused for unchanged code. This does **not** renew
a full-file review of every large test/corpus/generated variant, including the roughly 9,500-line
mutation inventories. No fresh whole-release coverage certificate is claimed. The held final
integrated review and verification still need their final source binding.

## R1 — MEDIUM — shared-core exceptional paths still skip preservation or failure checks

**Integrated source:** `tools/modding_harness/dispatch.py:70-78`, pinned in `e883ae3`.
**Blame:** IN-ARC. The shared dispatcher was added in `622b6cfd`; the latest pin added the
local-ask handling at lines 70-72 and 77-78. This is remaining contract coverage, not a claim
that ordinary evaluated-profile cases regressed.

The normal evaluated-profile prompt path now preserves correctly. However, when the profile's
result is `unguarded`, a local ask returns at line 78 before the nonempty save plan is checked.
It also replaces the unguarded result with the local `evaluated` result and drops its warning.

**MEASURED:** a synthetic valid Action/Evaluation with three existing toy save files, an explicit
three-mutation SavePlan, an unguarded profile result and an evaluated local ask returned:

```json
{"decision":"ask","evaluation":"evaluated","preservation":"not_required","transactions":[]}
```

This was a real dispatcher check, not preview mode. No typed grant was supplied. The files were
ordinary owned `.save` fixtures, not game saves; no submitted mutation executed. The outcome
permits a confirmation without either bulk approval or preservation despite the declared plan.
No currently qualified native-host route to this combination was established by the probe.

A second contrast shows `ask/failed` and `ask/partial` local restrictions bypass the failure check
at lines 73-74: they return ask rather than deny. The evaluated local-ask preview control correctly
reports preservation required. These are separate status branches missing from the current tests.

**Repair:** do not return an unguarded fallback over an explicit preservation obligation. Preserve
or refuse it, retain the incomplete-evaluation notice, and reject failed/partial restrictions
before treating their ask as authorizable. Add unknown-profile/nonempty-plan and local-status
contrast cases. Make the change in the shared core and reconcile both consumer pins.

## R2 — MEDIUM — transcript protection recognizes names, not an existing hard-link alias

**Held guard source:** `tools/skyguard/self_rules.py:831-851`, reached through shell and file
guards. **Blame:** IN-ARC, `ee53ea2` and its recorded working-tree follow-up.

The new transcript rule compares normalized path strings and transcript-tree membership.
It does not compare the requested file's identity with the supplied current transcript.

**MEASURED through the real in-process Claude guard entry:** a scratch transcript and an ordinary
path outside its transcript tree were made hard links to the same owned file. `os.path.samefile`
confirmed identity. An append to the literal transcript was denied with S-SELF-TRANSCRIPT; an
append to the alias and a Write-tool request for that alias were both allowed with no rule.
No append or Write request was executed; the original fixture bytes remained unchanged.

**Limit on the finding:** the supported `ln` creation request naming the protected source was
itself denied by the guard. The reproduced write gap requires an alias that already exists;
this is not evidence that the ordinary guarded `ln` route creates one successfully. Existing
script-file/interpreter coverage limits remain separate. This does not establish a live
approval forgery or a filesystem sandbox boundary.

**Repair:** compare physical identity against the current transcript where available, retain
source-side alias-creation protection, and state unsupported alias cases explicitly. Add a
pre-existing-alias contrast, alongside literal-path denial and read/copy-out controls. This
extends the existing L2-3 evidence; it is not a second count of the original baseline finding.

## R3 — MEDIUM — opaque-command repair narrows the recorded ask-first requirement

**Uncommitted guard source:** `tools/skyguard/shell_rules.py:366-370`.
**Blame:** IN-ARC, working-tree implementation after `ee53ea2`; not merged into master.

The new `_opaque_protected` rule requires `_hidden_action(command, parsed)` in addition to an
unidentifiable program and a protected target. A dynamic program whose destructive behavior
is not spelled in this same command therefore remains allowed.

**MEASURED:** an unnamed program given either the configured Data directory or a plugin inside
it returned allow with no rule. The positive control with a literal delete action assigned in
the command returned the expected ask; a named read stayed allowed. Commands were evaluated
as data only.

The branch already contains a matching expected-ask case. Its existing
`test_an_unnamed_program_on_a_protected_path_asks[opaque-on-a-plugin]` failed with allow;
`[var-holds-command-line]` passed in the same contained targeted run: **one failed, one passed**.
This is an observed mismatch with the branch's own requirement, not an inferred threat.

**Repair:** restore the recorded ask-first behavior or obtain an explicit revised policy before
adopting the extra action-word requirement. Pricing legitimate tool invocations is useful
evidence, but does not silently revise the ruling. Keep the test and documentation consistent
with the selected boundary. Existing historical limits are not automatically accepted deferrals.

## R4 — MEDIUM — repaired latency gate still accepts invalid hook protocol output

**Held installer source:** `scripts/guard-latency.py:88-99`.
**Blame:** IN-ARC, `67dfd71`. The previous unconditional-result failure is repaired, but its
replacement checks only JSON syntax, not the toolkit's actual output contract.

**MEASURED:** six owned stand-in wrappers read stdin and print the JSON scalar `1`. Running the
actual repaired CLI with one invocation per wrapper returned exit 0, reported zero failed runs,
and printed PASS (worst observed p95 63 ms). No real engine wrapper was called. In contrast,
the existing production `skyguard.claude_output.valid(b'1')` returned False for those bytes.

This is a false protocol-success claim by a maintainer diagnostic. It is not evidence of a
production hook failure or a renewed latency/load qualification.

**Repair:** use the existing production validator for nonempty PreToolUse output. Preserve
legitimate silent/empty-object allows and the separate SessionStart text contract. Add valid
allow/deny controls and JSON scalar/array/wrong-schema failures; syntactically valid JSON is
not sufficient to establish a hook answer.

## R5 — LOW — new rule-customization advice does not work for ordinary rules

**Held docs source:** `docs/safety-philosophy.md:88-89`.
**Blame:** IN-ARC, `795ba23`; this replaces the earlier nonexistent-threshold advice.

The new instruction says changing a rule's `decision` metadata in `tools/skyguard/rules.py`
can turn a refusal into a note. Many deciding paths return a hardcoded deny/ask and do not
consult that field. The special self-guard decision paths are not representative of all rules.

**MEASURED:** the S-SED-CRLF control denied as expected. Changing only its in-memory registry
decision from deny to advise left the real guard result as deny with the same rule and reason.
No source/config file was edited and no sed command executed. Restoring the dictionary value
left the captured source unchanged.

**Repair:** limit the advice to rule families that actually read the metadata, or explain that
changing other decisions requires editing and testing the deciding implementation too. Do not
generalize one configurable rule family into a toolkit-wide customization guarantee.

## Review limits and unchanged holds

These findings do not close or replace the original 64 findings, the House Carl archive-discovery
issue, or the held integration work. R2-R5 concern proposed repairs and must not be described as
newly deployed behavior. R1 is the only finding above in current master runtime source.

All probes ran against frozen copies under the resource-budget runner. Only named targeted
tests and harmless stand-in wrappers ran; no full suite, mutation campaign, load test, final
archive build, live engine operation, installed update, native-host trust change, public issue
action or publication occurred. The empty-backup-directory cleanup observation is retained in
private probe evidence but is not promoted to a release blocker: no saved content was at risk
in that fixture. No speculative findings are included in the five above.

The review made no production fixes or branch integration. The existing holds on integration,
final verification, manual acceptance and publication remain effective.
