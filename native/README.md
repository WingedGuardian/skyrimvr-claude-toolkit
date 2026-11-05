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
set VCPKG_ROOT=<folder holding vcpkg.exe>
python native/sdk/sdk.py build   --prefix <P> --work <W>
python native/sdk/sdk.py verify  --prefix <P>
python native/sdk/sdk.py qualify --prefix <P> --work <W>
```

Keep both paths short. A prefix is built once and never updated in place; to change anything, change the pins and build a
new one. `build` refuses a game folder and refuses a package set that differs from the pins, before compiling.
`qualify` links `examples/consumer` against the installed files. Finding the package proves nothing.

**The pins are deliberate.** A bump means a new SDK and re-running everything below. CommonLib tags move fast and keep
correcting VR layouts, so choose by test, not by age. The library is VR-only: `EXCLUSIVE_SKYRIM_VR` is defined.

## Use it from a consumer

```cmake
find_package(SkyrimNativeSdk CONFIG REQUIRED)   # -DCMAKE_PREFIX_PATH=<P>
target_link_libraries(MyProbe PRIVATE SkyrimNativeSdk::CommonLib SkyrimNativeSdk::MinHook SkyrimNativeSdk::Observation)
skyrim_native_add_bridges(MyProbe "${CMAKE_CURRENT_SOURCE_DIR}/src/bridges.asm")
```

Your assembly names one bridge per site (see `examples/consumer/src/bridges.asm`). You define `SkyrimNativeObserve` and
`SkyrimNativeXStateMask`, and set the mask from `cpuStateFacts()` after `qualifiedState()` admits it.

## What a consumer still owes, for every site

The tooling checks what you declare; it cannot know your site. **A fixture passing for one descriptor qualifies no other.**

1. **Decode the body.** `EntryDescriptor` holds the expected bytes and unwind codes. `r11IsNotAnInput` is your claim, from
   the decoded body. Also confirm the original does not consume incoming RBP: the bridge uses RBP as its frame pointer.
2. **Prove the site is quiescent when you publish the hook.** An unpublished main loop does not prove worker threads are idle.
3. **Handle lifetime.** Never unload after publishing a callback (`lifetimeStart`). `capture_gate.hpp` bounds a capture and
   reports incomplete ones; use `enter`, `leave` and `drained`.
4. **Keep the hot path clean.** Your observer must not allocate, log or take engine locks, and must not keep the packet's
   state pointer.
5. **Measure overhead.** Each call saves and restores the full XSAVE state, even when your observer does nothing. Do not
   call a dormant bridge free.
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
python native/provenance/native_image.py inspect --store <S> --sha256 <image hash> --rva 0xA25B60 --bytes 16
python native/provenance/native_image.py check   --store <S> --sha256 <image hash> --descriptor sites.json
```

- **One read-only copy per image hash**, so consumers share it and nothing is copied per run. The store has no default
  location (`--store` or `SKYRIM_NATIVE_IMAGE_STORE`) and cannot be inside a game folder. Game bytes stay on your machine.
- **Retention:** kept until its owner removes it; each entry carries `.housekeep-keep`.
- `register` checks layout and that the entry point left `.bind`. It does not prove the code bytes are right. Tool details are
  recorded as declared, and it does no disassembly: check known sites with `check`, and bring your own disassembler.
- `verify --installed` fails if the game changed, which also means your sites need requalifying.

## Tests

```
python -m unittest discover -s native/tests -p "test_*.py"
python native/tests/mutations.py --prefix <P> --work <W>
```

`mutations.py` runs the native tests as its baseline, then breaks each guard in a throwaway copy and requires its named
check to go red. `.github/workflows/native.yml` runs all of this from nothing on Windows.

Licences: `sdk/THIRD-PARTY-NOTICES.md`. The bridge and entry hook were promoted from reviewed sources; see
`observation/PROMOTED-FROM.json`.
