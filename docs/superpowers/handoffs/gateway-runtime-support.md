# Toolkit-owned runtime support for controlled consumer tests

Status: capability/promotion handoff, not an implemented API, deployment or launch authorization.

## Ownership and problem

Reusable runtime-test infrastructure belongs in the toolkit. Consumer-specific profiles,
budgets, fixtures, source captures and product logic stay in the consumer. Another session's
experiment folder, mutable build or broad diagnostic-project qualification is not a supported
shared interface.

The gateway consumer currently reuses task-local test helpers for file inventories,
non-save configuration preservation, managed sessions, startup readiness and single-owner
shutdown. Its preparation gate also checks an unrelated diagnostic project's full source
receipt. Changes to diagnostic C++ then block consumer preparation even though the consumer
does not build, deploy or run that diagnostic component. The current gate remains closed;
this handoff does not authorize weakening it or transplanting hashes.

## Existing facilities first

- Reuse the toolkit/shared-core streaming digest, durable publication and locking facilities
  where their contracts match. Keep shared-core source changes in its canonical upstream;
  do not edit the vendored `tools/modding_harness` tree directly.
- `modding_harness.preservation.SaveStore` preserves logical saves. It is not automatically
  a configuration/SQLite reference-preservation API; do not repurpose it or change save
  policy as a shortcut.
- The installed consumer controller and toolkit checkout can have different capabilities.
  A CLI that offers on/off/status is not interchangeable with a qualified managed `Lifecycle`
  interface. Inventory and verify the actual target before choosing an integration.
- Continue using the established guarded named-loader for save pairs. No new generic
  load/save interface or hook change is part of this promotion.
- The historical reference class has an unused hard-link capture branch; the consumer's
  override avoids it. Do not promote the entire class unchanged or expose that branch.
  Keep reference-only capture and the independently checked restoration behavior.

## Required supported capabilities

API names/layout are for the toolkit owner to settle; these are behavioral contracts.

| Capability | Required behavior |
|---|---|
| File hashing and metadata inventories | Streaming hashing; deterministic filename/size/mtime inventory with explicit exclusions. Distinguish metadata equivalence from content-hash proof. Read-only access never mutates saves. |
| Non-save reference preservation | Explicit live/reference/evidence roots and immutable reference manifest. Recheck hashes, ownership, scope and file identity; refuse missing references and mutable aliases. No per-run Data staging copy or hard-link fallback. Preserve exclusive restoration, retained test outputs and unknown/foreign-file refusal. |
| Managed host binding | Explicit game/run paths, exact qualified host identity and capability checks. Retain desktop isolation, normal-VR verification, original configuration restoration and Steam preservation. No silent fallback to an older controller. |
| Native readiness and session observation | Verify exact game process/creation identity, real lifecycle and advancing frames. Negotiate actual DevBench schemas. Bound pure reads and retain unknown outcomes. Do not import diagnostic instrumentation or auto-accept unknown modals. |
| Cooperative Windows evidence I/O | Readers share DELETE where required for atomic publication. Scope publication retries to unchanged owned session JSON; no retry of a native mutation or uncertain quit. Avoid active-manifest polling during startup/cleanup. |
| Single-owner exit/restoration | One verified owner requests quit; the existing supervisor owns final cleanup. No competing stop/off, PID-only authorization, uncertain quit replay, user-game termination or helper cleanup without identity proof. Cold recovery requires verified cold state. |
| Qualification/provenance | Exact sources for the consumed package, supported host bindings and relevant dependency closure; separate test evidence and capability limits. No dependency on another project's whole build receipt or unneeded native sources. |

Ownership/approval readers, output roots and installed host bindings must be explicit inputs.
Do not ship private game-root paths, NAS locations, mutable global task roots or a consumer's
lease-ledger path in toolkit defaults. The consumer remains responsible for fresh launch
approval, desktop choice, its profile/fixture declarations and existing money/turn ledgers.

Keep diagnostic phase capture, instrumentation, C++ compiler orchestration, mod-specific
writer probes, report generation and experiment source/build receipts with their owning
workstreams. Promote only the reusable support actually required.

## Promotion and verification sequence

1. Trace actual calls and lazy imports through preparation, preflight, launch, readiness,
   stop, normal restoration and cold recovery. Static imports alone are incomplete proof.
2. Select existing toolkit APIs where compatible. Promote missing reusable functions into
   a supported toolkit package with portable source provenance; leave session-specific
   behavior in consumer bindings. Preserve the original experiment and historical receipts.
3. Review before deployment. Use fixture-local checks for source/import drift, ownership,
   process reuse/creation precision, unknown read/write outcomes, once-only quit, concurrent
   cleanup, restoration failures, hard-link/symlink hazards and source/reference corruption.
   Reject unexpected imports or unsupported hosts rather than bypassing qualification.
4. Update the consumer adapter and qualify that exact source set. Keep the existing
   guarded loader and intervention-off, cancellation/deadline and accounting requirements.
   Do not rebind old native acceptance receipts to new hashes.
5. Perform a separately approved controlled engine run, verify cleanup/restoration, then
   let the consumer complete its actual product acceptance. Fixture tests do not establish
   native gameplay or physical VR acceptance.

No public release, game launch, save operation, installed-controller replacement or guard/hook
deployment is authorized by this document. Any guard/hook change retains its independent
review and exact-commit CI gate. The consumer's production behavior stays unchanged until
its separate acceptance and activation requirements pass.
