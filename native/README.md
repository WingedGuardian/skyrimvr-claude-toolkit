# Native SDK and observation tooling

For **maintainers and consumers building SKSEVR test DLLs**. Never shipped: `native/` is in `EXCLUDE` in
`scripts/build_release_zip.py`, and `tools/check-release-payload.py` proves its absence. A plugin built with it needs only
what `sdk/pins.json` lists under `runtime_requirements`, not Python, MSVC, this toolkit or an unpacked executable.

| Need | Use | Provides |
|---|---|---|
| A coherent pinned SDK | `sdk/sdk.py`, `sdk/pins.json` | CommonLib VR-only from source, every transitive package, MinHook, a hash receipt, a real link test |
| Observing a native call | `observation/` | Raw-context bridge, checked entry hook, capture gate |
| Binding results to inputs | `provenance/receipts.py` | Stale-binary and post-run drift rejection |
| Reading the game's native code | `provenance/native_image.py` | Read-only immutable image reference with provenance |
| Starting a consumer | `examples/consumer/` | An offline build using only the exported targets |

## Build the SDK

```
python native/sdk/sdk.py build   --prefix <P> --work <W>
python native/sdk/sdk.py verify  --prefix <P>
python native/sdk/sdk.py qualify --prefix <P> --work <W>
```

Keep both paths short. A prefix is built once and never updated in place; to change anything, change the pins and build a
new one. `build` uses its own vcpkg at the pinned commit, refuses a game folder, and refuses a package set that differs from
the pins before compiling. `verify` checks the receipt against the prefix, the pins and this tree's sources.
`qualify` links `examples/consumer` against the installed files and records that against the receipt; `verify` reports it.
Finding the package proves nothing, and the package config refuses a dependency that resolves outside the prefix.

**The pins are deliberate.** A bump means a new SDK and re-running everything below. CommonLib tags move fast and keep
correcting VR layouts, so choose by test, not by age. The library is VR-only: `EXCLUSIVE_SKYRIM_VR` is defined.

## Use it from a consumer

```cmake
find_package(SkyrimNativeSdk CONFIG REQUIRED)   # -DCMAKE_PREFIX_PATH=<P>
target_link_libraries(MyProbe PRIVATE SkyrimNativeSdk::CommonLib SkyrimNativeSdk::MinHook SkyrimNativeSdk::Observation)
skyrim_native_add_bridges(MyProbe "${CMAKE_CURRENT_SOURCE_DIR}/src/bridges.asm")
```

Your assembly names one bridge per site (see `examples/consumer/src/bridges.asm`). You define `SkyrimNativeObserve` and
`SkyrimNativeXStateMask`. Set the mask from `cpuStateFacts()` after `qualifiedState()` admits it, then call `prepareBridge`:
an unset mask would silently clobber the original's vector registers, so it refuses. Keep each `EntryHook` in static storage.

## What a consumer still owes, for every site

The tooling checks what you declare; it cannot know your site. **A fixture passing for one descriptor qualifies no other.**

1. **Decode the body and make the claims.** `EntryDescriptor` holds the expected bytes and unwind codes, which must match
   what those bytes do. Only fully decoded shapes are accepted: PUSHes filling all 5 bytes, or one 5-byte home-slot store.
   Three claims are yours, from the decoded body, and refused if absent: `r11IsNotAnInput`, `noStackArguments` (the bridge
   does not forward them, so a site with 5+ arguments or varargs gets garbage) and `incomingRbpUnused` (the bridge keeps RBP
   as its frame pointer). Also check nothing branches into the first 5 bytes after the entry: they become a jump.
2. **Prove the site is quiescent when you publish the hook.** An unpublished main loop does not prove worker threads are idle.
3. **Handle lifetime.** Never unload after publishing a callback (`lifetimeStart`). `capture_gate.hpp` bounds a capture and
   reports incomplete ones. Call `enter` and `leave` around each record, then `disarm` and wait for `drained`. `arm` refuses
   while an earlier producer is still inside. `now` must fit 48 bits.
4. **Keep the hot path clean.** Your observer must be `noexcept` and must not fault: an exception before the call unwinds
   through the bridge and the original never runs. Guard any read of a caller-supplied pointer. It must not allocate, log or
   take engine locks, must not keep the packet's state pointer, and must treat the packet as read-only, because it is
   reloaded into the registers afterwards.
5. **Measure overhead and stack.** Each call saves and restores the full XSAVE state, even when your observer does nothing,
   and uses about 17 KB of stack. Do not call a dormant bridge free, and check small-stack threads.
6. **Treat game pointers as observations.** They are not durable identities, and a worker must not dereference them later.
   A missing return on a native exception stays missing evidence.

Native tests and mutants are synthetic. They prove nothing about engine, VR or gameplay behaviour.

## Native image reference

The installed `SkyrimVR.exe` is SteamStub-wrapped, so its disk bytes are not the runtime code. **This tool never derives an
image and never runs a derivation tool.** You supply one you made yourself:

```
python native/provenance/native_image.py register --store <S> --source <exe> --image <derived> --owner <you> \
    --tool-name <name> --tool-commit <commit> --tool-sha256 <hash>
python native/provenance/native_image.py verify  --store <S> --sha256 <image hash> --installed <exe>
python native/provenance/native_image.py inspect --store <S> --sha256 <image hash> --rva <hex RVA> --bytes 16
python native/provenance/native_image.py check   --store <S> --sha256 <image hash> --descriptor sites.json
```

- **One read-only copy per image hash**, so consumers share it and nothing is copied per run. The store has no default
  location (`--store` or `SKYRIM_NATIVE_IMAGE_STORE`) and cannot be inside a game folder or a git checkout. Game bytes stay on
  your machine.
- **Retention:** kept until its owner removes it; each entry carries `.housekeep-keep`.
- **Immutability is against accidents.** Read-only attributes and a manifest hash catch drift; they do not stop a process
  determined to rewrite the store.
- `register` checks layout and that the entry point left `.bind`. It does not prove the code bytes are right. Tool details are
  recorded as declared, and it does no disassembly: check known sites with `check`, and bring your own disassembler.
  `inspect` reports `.pdata` facts and follows chained entries to `primary_begin`, because a range can be a fragment.
- `verify --installed` fails if the game changed, which also means your sites need requalifying.

## Test context: put configuration and databases back

`context/context_store.py` makes one live test leave the install as it found it. It covers selected configuration and mutable
SkyrimNet state, plus files a test adds for its run. It never starts a game, and a prepared context carries no launch consent.

```
python native/context/context_store.py check   --game G --state S --store B --owner O --run R --profile P   # read-only
python native/context/context_store.py prepare --game G --state S --store B --owner O --run R --profile P
# ... the caller's own approved launch and exit ...
python native/context/context_store.py verify  --game G --state S                                           # read-only
python native/context/context_store.py restore --game G --state S --store B --owner O                       # repeatable
```

The profile is JSON and everything in it is explicit; the toolkit ships no paths:

| Key | Meaning |
|---|---|
| `roots` | Folders under the game, captured whole and put back whole |
| `cold_processes` | Exact image names that must not be running. Required: without it a running game cannot be seen |
| `edits` | `{path, old, new}` inside a root. `old` must match exactly once. Bytes are replaced, the file is not reformatted |
| `additions` | `{source, sha256, target}`. Placed for the run and removed after; refuses to replace a file |
| `slots` | Optional `{file, lock, keys}`: a shared ownership file. Never stolen; other fields keep their exact bytes |

What it guarantees, each with a test and a mutant:

- **Refuses before changing anything** on another owner, a named process running, a file some process holds open, a link in
  scope, an edit that does not match, or an incomplete earlier run.
- **Stores each distinct content once**, by hash, read-only, in `--store`, whatever the number of runs. No per-run staging copy,
  and no hard links. `--store` and `--state` must be outside the game.
- **Writes the manifest first.** Any later failure or crash, including one inside `restore`, is undone by running `restore`.
- **Keeps evidence.** Changed files are copied to `<state>/<run>/evidence/changed`, files created in scope go to `created`.
- **Leaves everything outside `roots` alone**, so an unrelated change survives.
- **Holds ownership until a full re-hash passes.** A partial restore reports `verified: false`, exits 1 and stays active.

Limits, stated plainly:

- A file held open with full sharing by a process not in `cold_processes` that writes after the check is not seen until restore.
- `check` passing says only that `prepare` would have started at that moment.
- `slots` are cooperative: a writer ignoring them is not stopped.
- Saves are out of scope. Pair protection stays with the save guard and the guarded named-load route.
- Not yet qualified against a real session: see the status in the handback.

## Tests

```
python -m unittest discover -s native/tests -p "test_*.py"
python native/tests/mutations.py --prefix <P> --work <W>
```

`mutations.py` runs the native tests as its baseline, then breaks each guard in a throwaway copy and requires its named
check to go red. `.github/workflows/native.yml` runs all of this from nothing on Windows.

Licences: `sdk/THIRD-PARTY-NOTICES.md`. The bridge was promoted from reviewed sources, listed in
`observation/PROMOTED-FROM.json`; `entry_hook.hpp` and `capture_gate.hpp` were generalised from such sources and rewritten here.
