# Plan 8 local release readiness — 2026-10-08

Local implementation, release review and automated gates are complete. Current live-agent E2E and the manual batch remain unmeasured; this report does **not** certify publication readiness. No push, tag, upload, remote rename, real-install change or global hook-trust change was performed.

The integration branch and master were locally fast-forwarded to `97116d0ddd001b0ea5271fdb0beba51402a220bb`. This report is a subsequent documentation-only addition. The product rename, archive identity classification, README and dated v4.0.0 CHANGELOG are complete. GitHub repository URLs retain the existing remote name.

## Automated evidence

The full guard gate ran against immutable `51a3c01d32dd8df0132ea285543b0421fbacfb36`. The later `97116d0` repair changes only artifact verification, its tests/mutation twins and two CHANGELOG bullets. Guard sources, generated entries, policy corpus and guard hashes are unchanged; their full gate was not needlessly repeated. The verifier repair received its own complete affected checks and fresh cold artifact proof.

| Check | Observed result |
|---|---|
| Full ordinary suite | 9,178 passed, 61 skipped, zero failed; 896.36 seconds |
| Windows skip ceiling | 61 observed, at most 172 allowed |
| CI output-format selftest | 20 passed; 1.79 seconds |
| Full mutation run | 2,167 passed, two platform skips, zero failures or survivors; 1,630.68 seconds |
| Verifier module plus all final mutation anchors | 2,186 passed; 92.54 seconds |
| Added verifier twins | Both killed; 31.37 seconds |
| Critical CI lint | All 236 tracked Python files passed; subsequently changed test files also passed |
| Configured fake-install wrapper reprobe | All 156 rows passed; 25.7 seconds |
| Historical decision pricing against original master `4e40265` | 80,050 calls; zero engine errors in either version |
| Pricing parse failures | 68 in both versions; zero reason moves |
| Changed decisions | 26 newly advised, one newly asked, three newly refused; all 30 read and judged intended |
| Relaxed historical decisions | No prior refusal, ask or advice removed |
| Cold local-clone build at `97116d0` | Archive bytes and all member hashes identical to the producer build |
| Independent Git-object comparison | Exactly 282 committed shipped files plus BUILD-INFO; five declared PS1/CMD line-ending conversions only |
| Actual payload checker | Wrong-commit control failed; correct commit passed |
| Actual scrubbed artifact verification | Passed; initial payload and installed-file leak scans found no leaks |

The two skipped mutation cases cover POSIX process termination and Linux pidfd handling. Ordinary skips include unavailable host capabilities, intentionally superseded corpus rows, opt-in load tests and platform-specific cases; they are not live coverage. No serial ordinary-suite rerun was needed because it had zero failures.

A daemon restart interrupted an earlier mutation attempt with one failure marker but no diagnostic/footer. That partial attempt is retained as incomplete evidence, with no attributed cause. The complete rerun against the unchanged commit passed. Earlier aborted candidates are not substituted for final results.

All heavy checks used the shared resource manager, below-normal priority, declared CPU/memory/disk budgets and at most eight workers. Mutation fixture cleanup completed before packaging or commits. The real game install was never a test destination.

## Release-review verdict and dispositions

The review covered the complete `v3.9.1..0d83b936` release surface: 459 assigned file bodies and every touching commit, including merge resolutions. Lanes covered guards, machinery/tests/CI/pins, and documentation/instructions/skills. Generated or repeated historical bodies were reused only through exact Git-blob equality or lossless reconstruction from fully read bodies and differences. Every subsequent repair delta through `97116d0` was reviewed. Implementer involvement and delegated execution evidence are disclosed in the retained review reports.

Reviewer verdict for the final implementation: **no unresolved reproduced in-arc defect identified**. This verdict covers the release changes, not a fresh audit of every previously shipped component, and does not replace live E2E.

| Reproduced finding group | Final disposition |
|---|---|
| Session-start stale state overwrote concurrent persistent Layer 2 enable/disable decisions | Refresh state under the writer lock before rewriting; race controls and twins pass |
| DevBench save aliases skipped recovery | Decision and recovery share the alias predicate; save/co-save controls and twin pass |
| CI builder dirtied its own checkout | Diagnostic output moved to runner temporary storage; actual pipeline regression passes |
| E2E preparation or later collection checks falsely reported success | Failed setup propagates and verdict derives from the final check set; offline failure controls pass |
| Migration fence parser diverged from preferences parsing | Matching delimiter, length and closer rules retained; regression and twins pass |
| Archive identity followed producer rather than selected commit | Slug read from validated committed package metadata; ancestor-build and refusal controls pass |
| CMD recorded-interpreter decoding and missing-value fallback | Apostrophes, bangs, unquoted paths and absent/empty values covered by actual CMD fixture regressions |
| Linux fixture permissions and platform assertions | Source/test repairs complete; native Linux execution remains unmeasured |
| Partial load startup lost child handles | Incremental handle retention; no-process startup-failure controls pass |
| Parallel package-manager tripwire logs contaminated other tests | Per-test logs and restoration; sibling implementation routing and twins pass |
| Generated caches affected instruction scans | Generated-cache exclusions with a bad-owned-instruction control |
| Documentation scope, legacy protection union and identity scrub | Corrected; generated parity and payload identity checks pass |
| Read-only Python inspection caused historical false refusals | Narrow AST/token handling with execution controls; historical false move eliminated by fresh pricing |
| Bash health probes clobbered engine failure state | Probe result fields local in all canonical/generated chains; 64 watched-red regressions and 28 killed twins |
| Verifier created apparent payload leaks and rejected venv paths | Payload import uses bytecode suppression; independently measured runtime directory allowed only in installed-file scan; both watched-red cases and twins pass |

The 30 pricing changes concern guard-file advice, a logged lock lift, in-place recovery confirmation, unlogged raw lifting and direct persistent-state writes. No historical probe command was executed during pricing review.

The local source-checkout hook notices are consistent with an unrendered template path failing before the wrapper starts. That is a reproduced launcher symptom and an inference about the app's exact launch, not observed host telemetry. A configured fake-install pass does not establish live hook approval in this source session.

## Independent review round — 2026-10-10/11

A second, independent review of the whole release range after `601b4df` returned CHANGES. Six commits fix it (`8a89f3d..86f2633`); master was locally fast-forwarded to `86f263385cc7b3cbb3da9bcac79e25a672fc822c`.

| Reproduced finding | Disposition |
|---|---|
| A printed state delete, piped or substituted into an executor, no longer hit the guard-state rule | Restored on every route probed (pipes, `iex`, substitution, `eval`, run-after-redirect, `tee`, `xargs`, `cmd /c`, process substitution), guards on and off, relative and absolute |
| A Recycle-Bin delete of guard state with a computed operand (variable, concatenation, string formatting) lost the guard-state rule | A format-token or empty operand is now unplaceable, so every quoted word is a candidate; an independent probe of ten spellings not in the fix's own tests refused 30 of 30, and 9 of 9 outside controls were allowed |
| Output-inert approval could pass a step the parser dropped | Inert only when every part of the command belongs to a parsed step |
| Leak-scan allowances had no floor; the verifier took the archive root from the checkout | Floor added; root read from the archive's own package metadata |
| CHANGELOG combined issues, overclaimed and filed new-feature fixes as 3.x fixes | One issue per line; self-protection lines moved under the new feature |

Pre-existing gaps found by the same review (a printed path piped into a delete, `cmd /c for /f … do del`, absolute-path read-only inspection refused) are recorded as CHANGELOG Known limits, not fixed in this release.

Full gate on `86f2633`:

| Check | Observed result |
|---|---|
| Full ordinary suite | 9,361 passed, 61 skipped, zero failed; 1,274.32 seconds |
| CI output-format selftest | 20 passed |
| Full mutation run | 2,218 passed, two platform skips, one failure: the `messages-no-echo` mutant hit the 600-second per-mutant timeout under eight-way load. Serial rerun killed it (404 seconds). No survivor |
| Historical decision pricing against `601b4df` | 82,112 calls; zero engine errors in either version; 68 unchanged parse failures; zero decisions moved in any direction |
| Cold local-clone build at `86f2633` | Archive bytes and all 284 member hashes identical to the producer build |
| Actual payload checker | Wrong-commit control failed; correct commit passed |
| Actual scrubbed artifact verification | Passed; no leaks |
| Independent leak scan | One archive root, no forbidden members, no user name, email, account or key-shaped value; every user-folder path is a placeholder |

Four earlier full-gate attempts on this commit were stopped by the resource watchdog when the game started and exhausted commit memory, and one by a low-memory process reaper during mutations. None counts as a result. On the user's explicit instruction, the gate that produced the table above ran outside the shared resource manager, at below-normal priority with eight workers. The `messages-no-echo` mutant needs about 400 seconds when run alone, which leaves too little margin under its 600-second cap.

## Remaining user and host work

1. **Current Codex E2E:** complete the prepared isolated device-login flow in a fresh owned non-temporary home. No existing credentials are copied and no global trust is edited. Then recapture scratch-install E2E on the current installed CLI; historical captures used a different CLI version. Interactive approval, context/lifts and subfolder behavior need their own observations. Claude's quota-consuming E2E half remains deferred unless authorized.
2. **Manual batch:** the refreshed T17 draft remains preparation, not an executable certified test session. User-owned TUI, manager/GUI and headset checks are unrun. Missing scoped Steam stand-in/collectors and actual runtime/profile binding must be prepared and validated before presenting those sections for execution; do not improvise the historical draft.
3. **Game prerequisites:** record actual runtime/SKSEVR identities, active manager profile and winning files before live P6/P7 checks. Public archive hashes/layouts were verified without executing or installing their contents. Third-party mods are installed by the user through their manager, with rollback; archive inspection does not establish runtime compatibility.
4. **T14b latency:** each normal-priority worst-case load run requires explicit user approval. None was performed here.
5. **Host decisions:** Git for Windows update/pin requires a quiet user-authorized moment. The global Spriggit 0.40.0 update decision remains the user's. Docker legs need Docker Desktop already running; it was unavailable and was not started.
6. **Publication:** push/tag/release/upload/repository rename and CI-secret changes remain unauthorized. The user must supply the required LEAK_TOKENS secret when publication is separately authorized. Real Linux CI needs that later push; its 305 skip ceiling remains an estimate until measured.

## Known limits

Hooks must be configured, approved and actually invoked on their supported surfaces. Layer 2 is opt-in, off by default, and unsupported on Linux/POSIX in this release. Parser/adaptor coverage gaps and supported-host boundaries remain as documented in [guard coverage](../../guard-coverage.md) and [safety philosophy](../../safety-philosophy.md); the guards are not a general sandbox. Python 3.8/3.9 syntax compatibility is untested, distinct from the tested 3.10–3.13 range.

Pre-arc limits remain separate: encoded Bash program names and generic fixture copy exclusions can leave coverage/private-source-copy gaps. The tests were run in isolated clean worktrees, not against a populated checkout containing local paths, credentials or backups. Existing identity-prefix matching is literal and does not claim arbitrary filesystem-boundary enforcement. Optional-tool versions and public metadata sightings do not prove installed game runtime compatibility.

The prepared user-run authentication helper and detailed progress/review/manual drafts stay in the ignored contributor ledger. This documentation-only report commit requires its own final archive rebuild and verification; the artifact results above apply to `97116d0`. Final archive digests and exact final commit identity are retained in the ledger rather than embedded in this self-containing report.
