# v4 reconciliation, 2026-10-22

This is a development-status record, not release acceptance. The user authorized the shared-core
update, reconciliation of the development lines, review-ledger/documentation reconciliation and
open-issue triage. The user explicitly held the four review-fix branches, final integrated
verification, and manual acceptance/publication preparation. No push, issue comment/closure, tag,
release, installed upgrade, game operation, live-save operation or trust/auth change was performed.

## Integrated source

| Item | Result |
|---|---|
| Development-line reconciliation | `20af91d` merges `session/harness-v4` at `b396d5c` into master at `98da143`. Both histories remain. |
| Master's newer changes | The three archive-layout fixes and current resource policy are retained. |
| Instruction conflict | Retained v4's shell Recycle Bin guidance and master's current resource policy; generated both agent instruction files from source. |
| Instruction budget | Codex 32,653/32,768 bytes; Claude 32,212/32,768 bytes at the merge. |
| Instruction reference follow-up | `d4950f1` replaces a source-relative resource-policy hyperlink with a repository path reference; final sizes are Codex 32,636 and Claude 32,195 bytes. |
| Shared preservation core | `e883ae3` pins `d2af1e897daecad4fabc22189ae74fc8a9d91e91`; all 19 vendored files match committed upstream objects and their lock hashes. |
| X4 reconciliation | Read-only verification: X4 master `e20a738` already pins the same core; all 19 lock hashes match. No X4 source edit was necessary. |
| Installer mutation anchor | Re-anchored `mod-install-config-overwritten` to master's expanded config condition; its named mutation is killed. |

The core change holds a permission prompt until save scope, protected-store rules, typed bulk
approval and verified preservation have been checked. A refusal still returns before preservation.
This does not import the separate portable-verdict/rendering changes on `fix/v4-guard`.

## Scoped verification

These checks ran locally through the resource-budget runner and contained test launcher. They are
targeted development evidence, not the held final suite or archive qualification.

| Check | Observed result |
|---|---|
| Merge: archive triage, mod installer, generator and repository instruction budget | 134 passed. |
| Generator and guard manifest checks | 51 generated files in sync; 97 guard files current. |
| New preservation regressions before the pin | Both original local-prompt cases failed: one pair was not preserved, and three pairs were prompted without bulk approval. |
| Updated core: profile tests, new local/profile prompt cases, payload controls, all mutation anchors | 2,725 passed. The new test's Windows drive-case comparison was corrected to use canonical identities; one stale installer anchor from the merge was also corrected before this green run. |
| Selected mutation tests | 3 passed: restoring the local-prompt early return, restoring the profile-prompt early return, and removing installer config protection each fail the named regression. |
| Documentation links and current instruction budget | 4 passed after correcting the inherited source-relative resource-policy link. Generator parity passes. |
| xeditlib diagnostic-channel checks at `06f73ea` | 5 passed; mocked FFI channels, no DLL/game/plugin operation. Historical native qualification is not renewed by this run. |
| Issue 11 synthetic backend-profile construction | Root-only base archive omitted in SE and VR; explicit user-INI archive retained; VR supplemental archive retained; source INIs unchanged in all three cases. No backend or game was launched. |

The last full CI result remains [38 green jobs on b396d5c](https://github.com/WingedGuardian/skyrimvr-claude-toolkit/actions/runs/39374521991).
That result does not certify the merged master or its successor commits. Final CI, decision pricing,
artifact/native-host qualification, audit refresh and final readiness numbers remain held.

## Review-fix branches remain held

| Branch | Recorded tip | State at reconciliation |
|---|---|---|
| `fix/v4-guard` | `ee53ea2` | 7 committed repairs; opaque-command work and related changes remain uncommitted. |
| `fix/v4-health` | `b28c57e` | 19 committed repairs; private FIXES report is untracked. |
| `fix/v4-install` | `c37d959` | 20 committed repairs; recycler/test changes remain uncommitted. |
| `fix/v4-docs` | `795ba23` | 12 committed repairs; documented ask-count assertions depend on the held guard changes. |

None of these branches was merged, rebased, cleaned or otherwise edited. Their 58 commits remain
available for the deferred batch. Reconcile overlapping `housekeep.py` test-safety guards,
`tests/test_mutations.py`, guard hashes, generated instructions, and release-builder exclusion sets
when that batch resumes. Reconcile the duplicate toolkit/core prompt-order repairs rather than
blindly applying both implementations.

## Finding dispositions

The original eight-lane review covered `v3.9.1..b396d5c`: 2 HIGH, 18 MEDIUM and 44 LOW findings.
The tables below account for all 64, plus two informational notes. A commit named on a held branch
means implementation exists there, not that it is integrated or release-qualified. Branch reports
and original evidence remain separate; their old results are not relabeled.

| ID | Finding | Disposition |
|---|---|---|
| L1-1 M | Variable-held program plus flags | Held guard work; no closure claimed. |
| L1-2 M | Variable-held command passed to an evaluator | Held guard work; no closure claimed. |
| L1-3 L | Array, indirect and transformed program words | Held guard work; no closure claimed. |
| L1-4 L | Evaluated command substitution | Held guard work; no closure claimed. |
| L1-5 L | Inline awk execution naming a protected root | Held guard work; no closure claimed. |
| L1-6 L | PowerShell expression used as call target | Held guard work; no closure claimed. |
| L1-7 L | Interpreter receives target through argument data | Held guard work; no closure claimed. |
| L1-8 INFO | Other opaque programs naming protected paths | User's ask-first ruling is recorded; implementation remains uncommitted on the guard branch. |
| L2-1 H | Prompt bypasses save approval and preservation | Shared-core portion integrated in `e883ae3`; toolkit verdict/rendering portion remains held in `bf813a5`. |
| L2-2 M | Save tests exercise the old enforcement path | Held `21b2232`; new core integration tests added independently in `e883ae3`. |
| L2-3 M | Agent can write its approval transcript | Held `ee53ea2`; do not claim this protection on current master. |
| L2-4 L | Skipped lock target causes repeated relock | Held `8e3e8c0`. |
| L2-5 L | Documented approval format disagrees with live reader | Held docs `e3d32e9`; obsolete reader docstring still needs reconciliation. |
| L2-6 L | Private adapter identifiers in public source | Still pending; moving prose does not remove identifiers from runtime code. Preserve legacy compatibility when generalizing. |
| L2-7 L | Snapshot/recovery pruning deletes permanently | Held health `e250df9`; documentation follow-up remains required. |
| L3-M1 | Doctor disagrees with Claude coverage canary | Held `aeb36ea`. |
| L3-L1 | Malformed inline code reports engine failure | Held `3547162`. |
| L3-L2 | Crashed Codex call counted as hooked | Held `4337137`. |
| L3-L3 | Session-log pruning deletes permanently | Held `4bb7df6`; retention prose remains to reconcile. |
| L4a-H1 | Failed mod update can partially delete live folder | Held `f77db9a` plus notice/test-safety follow-ups. |
| L4a-M1 | Migration selects shortest shipped KB prefix | Held `c679a65`. |
| L4a-M2 | Folder cleanup ignores protected contents | Held `850c899`. |
| L4a-M3 | KB rotation permanently deletes snapshots | Held `12a3784` and notice follow-up; reconcile Forever policy versus this behavior before acceptance. |
| L4a-M4 | Release notes omit populated Unreleased section | Held docs `e3d32e9`; workflow refusal for a populated Unreleased section remains an unimplemented recommendation. |
| L4a-L1 | Cleanup fails off Windows | Held `850c899`. |
| L4a-L2 | Relative scratch path uses caller cwd | Held `850c899`. |
| L4a-L3 | Artifact verifier accepts degraded canary | Held `cd2cad6`. |
| L4a-L4 | Setup readback test cannot fail | Held `219ee27`. |
| L4a-L5 | POSIX fixture writes outside its sandbox on Windows | Held `08ef6b4`. |
| L4a-L6 | Example pins stale xeditlib revision | Held `1609cd3`; actual package pin already uses repaired `06f73ea`. |
| L4a-L7 | DevBench liveness test accepts every result | Held `023f7d3`. |
| L4b-M1 | Dependency audit misses README install command | Held `dcd76b6`. |
| L4b-M2 | Latency tool passes missing/crashed hooks | Held `67dfd71`; historical load measurements are separate evidence. |
| L4b-L1 | Contributor-only scripts crash in distributed ZIP | Held `0379406`. |
| L4b-L2 | E2E collector only checks tail of output | Held `d806349`. |
| L4b-L3 | Offline audit test ignores setup drift | Held `b4afcc6`. |
| L4b-L4 | Interrupted audit response aborts the run | Held `58e8caa`. |
| L4b-L5 | Adapter assertions have vacuous first clauses | Held `8e19af5`. |
| L4b-L6 | Layer-2 spike passes for wrong reasons/deletes old output | Held `c37d959`; native C3/C10 measurements were not executed. |
| L5-F1 M | Headless restore loses files changed after startup | Held `7771c24`. |
| L5-F2 M | Reference verifier misses punctuation in master names | Held `b2d1630`. |
| L5-F3 L | Staged patch name interpreted as glob | Held `8ba56fd`. |
| L5-F4 L | Headless state file deleted | Held `7771c24`. |
| L5-F5 L | Benchmark restoration/location claims | Held `dba2f73`; player-location limitation needs documentation. |
| L5-F6 L | Partial House Carl download blocks retry | Held `a7bec5c`. |
| L5-F7 L | ReSaver notice describes retired cache behavior | Held `575de1a`. |
| L5-F8 L | Crash classification uses offset without module | Held `46de5e4` plus anchor `2245aa2`. |
| L5-F9 L | BOM drops first active plugin | Held `b5bcb25`. |
| L5-F10 L | Nexus key exposed in curl argv | Held `9fbbbbc`. |
| L6-F1 M | Contributor plans shipped as executable instructions | Held `e6daa81`; cited evidence moves to `docs/evidence`. |
| L6-F2 M | Release notes incomplete or stale | Held `e3d32e9`; append the later installer/health/core changes after integration. |
| L6-F3 M | Save documentation says confirmation instead of preservation | Held `96a47fb`. |
| L6-F4 M | Upgrade directions contradict/cannot be followed | Held `19c2375`; end-to-end upgrade acceptance remains held. |
| L6-F5 L | sed verdict and ask-count documentation | Held `9bc448e`; five-ask claims depend on held guard work. |
| L6-F6 L | Contradictory Codex subfolder statements | Held `fb97915` and `e3d32e9`; source addendum retains the qualified version-dependent wording. |
| L6-F7 L | ADAPTING starts with private workflow | Held `19c2375`. |
| L6-F8 L | Toolkit-wide single .NET requirement overclaim | Held `3e37ab4` and `268dc0c`. |
| L6-F9 L | Neutral skill and inventory lists stale | Held `23332f8`. |
| L6-F10 L | Save skill has dangling approval sentence | Held `46aeae9`. |
| L6-F11 L | DevBench skill version sentence contradictory | Held `46aeae9`; installed runtime checks remain separate. |
| L6-F12 L | Internal plan language in user guidance | Held `1103974`. |
| L6-F13 L | Pre-arc documentation carry-overs | Held `795ba23`; pre-arc status retained. |
| L7-1 M | Python write barrier misses copy/truncate routes | Held `66e7437`. |
| L7-2 L | Mutation-only skip is treated as a platform skip | Held `10cf86f`. |
| L7-3 L | Package-manager tripwire spelling gaps | Held `66e7437`; isolated Python in script files remains a declared gap. |
| L7-INFO | Duplicate mutation twin | Held `bd982c5`. |

Additional follow-ups are not silently accepted as post-release deferrals:

- Corpus hermeticity: the health branch's synthetic-root trial passed 33 in-process corpus checks
  but broke 144 tests that retain the old literal root. The trial was reverted. A coordinated
  fixture migration remains pending with the held batch; it is not proof of a runtime protection bug.
- Test barriers: mode/time/ownership writes and isolated Python in shell script files remain outside
  the expanded barrier. No general OS-sandbox or network-barrier claim is made.
- Test-only recycler guards overlap across installer/health branches. Merge them with fakes before
  running their tests; do not invoke an OS-prompt path for verification.
- The health follow-up's remaining weak triage assertions and untested FormID normalization remain
  recorded; the zero-remainder test itself has a committed repair (`b28c57e`).
- Pending prose: retired stores and non-Windows behavior, session-sweep scope and switch, backup/Forever
  consistency, expanded test barriers and their limits, benchmark location, headless restore copies,
  module-qualified crash rulings, and one changelog line per integrated repair. Describe the code
  actually integrated; do not advertise held branch behavior as current.

## Open-issue reconciliation

| Issue | Ownership and disposition |
|---|---|
| [9](https://github.com/WingedGuardian/skyrimvr-claude-toolkit/issues/9) | Dependency-wrapper defect. Published xeditlib commit `06f73eac215970c427ede049a9198c8002c43c65` selects the dedicated diagnostic readers and allocates UTF-16 terminator space. Toolkit package and lock already pin it. Five focused channel tests pass. No additional dependency bump is required. Actual installed wrapper renewal, final artifact evidence and public closure are separate; issue remains open. |
| [10](https://github.com/WingedGuardian/skyrimvr-claude-toolkit/issues/10) | Installed personal-policy classification defect, including source/prose, report output, and helper-name suffix cases. The prepared candidate and its source/shell qualification are already recognized by the integrated toolkit adapter. Hash readback confirms the installed policy is still the original and the separate candidate is unchanged. Installation is not part of this work. Exact original reporter payloads remain unavailable, so do not close the issue from synthetic evidence alone. |
| [11](https://github.com/WingedGuardian/skyrimvr-claude-toolkit/issues/11) | Active asset-coverage defect, not reconciled away by a backend-version difference. The report uses installed 2.1.0; public toolkit pins 2.0.5. An independent three-case profile probe on current toolkit source shows root-INI base archive lists are omitted before either backend receives the profile. Treat this as a release-blocking correctness item until the integration supplies qualified archive inputs or reliably marks incomplete coverage. This triage does not claim a backend repair or native asset-result qualification. |

Issue 11 repair requirements: qualify the engine's applicable defaults and INI precedence; account for
both Wrye/deployed and MO2 inputs without editing authority files; fingerprint every newly consumed
input; preserve explicit overrides; mark uncertainty in machine-readable responses; use the supported
backend discovery facilities rather than a parallel asset index. Verify absent/present contrasts and
changed-input rejection. The [House Carl documentation](../../housecarl.md) now states the current
negative-result limit. No source fix is smuggled into the held review-fix batch.

## Historical checklist corrections and remaining holds

Completed historical prerequisites: Git update and pin move, isolated Codex login, recorded T14b
latency/deadline matrix, and the DevBench 1.28 pin change. The pin change is not live acceptance.
Layer 2 stays off by the user's ruling; real Steam verification was explicitly declined and remains
untested. Older readiness tables and draft checklist entries are historical, not a new todo list.

Held by the 2026-10-22 instruction: finish/merge the four review-fix branches, final integrated
review and gates, decision-history pricing, final archive/host/upgrade qualification, third-party
refresh within 24 hours of tagging, final readiness numbers, manual acceptance, and publication
preparation. Existing missing-secret/manual/rename/Nexus items remain recorded without action.
No new approval request is needed to preserve those holds. Installed toolkit/personal-policy
upgrades and public issue actions have not been performed.
