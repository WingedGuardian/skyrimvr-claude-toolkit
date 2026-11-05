# Native SDK, observation and test context: handback

Branch `feat/native-observation`, based on `integ/v4-fixes` at `5d078e4`. Maintainer-only: everything lives under `native/`, which
the release builder excludes and the payload check refuses, each with a mutation twin. New capability, so it belongs to the next
release, not v4.0.0. Usage, limits and evidence are in `native/README.md`; this note is for whoever reviews and merges it.

## What each consumer gets

| Consumer | Interface | Still owed by the consumer |
|---|---|---|
| Delirium | `native/sdk`, `native/observation`, `native/provenance/receipts.py`, `native_image.py` | Its own descriptors, claims and receipts for every site; drive `A25B60` is untested in game |
| AA | `native/sdk` (coherent packages; the `fmt` mismatch cannot recur from these pins), `native/context` | Its own profile, and qualifying its consumed integration |
| Skyrim Souls | `native/provenance/native_image.py` | Its own patch-site and quiescence analysis |
| Gateway | `native/context` is available. Its own support stays gateway-owned under the 2026-11-04 ruling | Its choice whether to adopt it |

### Skyrim Souls: what was asked for

- **Interface:** `native_image.py register|verify|inspect|check`, with an explicit `--store` or `SKYRIM_NATIVE_IMAGE_STORE`. Reads only.
- **Qualified identity:** source `6961efb4f4775a307b0fc9a3d637542c1e090be207d3b09467eab216b7f87971`, image
  `28fbe6abe4c221c6c9131737fbe8b86ea81638e69f62d9fdb9cbaa653186a0ab`.
- **Provenance:** re-derived from an isolated copy with an unmodified Steamless build (commit `cd770bf`), reproducing the historical
  hash. Consumers get one read-only reference, not a copy. Retention: kept until its owner removes it.
- **Measured against it:** 10 of 10 of Souls' function hashes match. The nine requested sites read as recorded.
- **Limits:** the toolkit never runs Steamless and ships no recipe; each maintainer derives their own image locally, because
  Steamless is CC BY-NC-ND. An image's first bytes are not the runtime state of a game with other hooks installed.

## Evidence

| Check | Result |
|---|---|
| SDK from an empty folder, locally and on GitHub | Builds, verifies, link-qualifies |
| Native bridge, hook and gate tests | Pass, locally and on GitHub |
| Test context | 120 tests, 92 mutants, three independent review passes |
| Whole mutation set | See the CI run on the head named in the merge request |
| Real game, one site | 44,102 paired calls at `A27730`, clean exit, 287 files restored and independently re-hashed |

Real-game limits are listed in `native/README.md`: one site, one session, headless, overhead unmeasured.

## Worth a reviewer's attention

- **The bridge's MinHook slot layout is assumed**, so the exact MinHook version is enforced. A bump needs requalifying.
- **Entry claims are the consumer's.** `r11IsNotAnInput`, `noStackArguments` and `incomingRbpUnused` are declared, not proven by the
  tool. Getting one wrong breaks the game in ways no offline test sees.
- **`slots` uses the gateway's existing ownership file protocol**, including its `.gateway.lock` sibling. If that protocol moves, the
  test context must follow. Taking a slot is strict: a supervisor already holding the slots must release them before `prepare`.
- **A small marker file sits in the game folder while a context is active.** That is the one write outside `roots` and `additions`.
- **Known limit:** with no `slots` and a marker deleted by hand, a second context can start.
- **`native.yml` takes about an hour on GitHub**, and only runs on `native/` changes on `master`, `main` and this branch.

## Open questions for the merger

1. Paths and names are proposals. `native/` was chosen so nothing here adds to the full gate.
2. `tests/test_verify_release.py` now drops `native` from its fixture, like every other maintainer-only path.
3. After merge, `native.yml`'s branch filter should lose `feat/native-observation`.

No tag, release or master change was made.
