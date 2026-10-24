---
name: logging-registry
description: Inventory, search and refresh logging switches across a Skyrim install, with configured values, source evidence and explicit coverage gaps. Use to locate debug/log controls or assess observability before troubleshooting. Does not enable logging or analyze log contents.
---

# Logging registry

Use `tools/logging-registry.py` for repeatable read-only discovery. Read [the tool guide](../../../docs/logging-registry.md) for formats, review annotations and limits.

Choose the actual game/Data and user-config roots. Do not silently select a different Skyrim edition or interpret an MO2 mods directory as the winning virtual Data view. For MO2, obtain an externally resolved view or describe the missing coverage; individual mod folders are not authoritative winners.

```text
python tools/logging-registry.py scan --game-root <game> --config-dir <user-config> --plugins-txt <authority-plugins.txt> --output-dir <local-report-dir>
python tools/logging-registry.py show <local-report-dir>/registry.json --filter <mod-or-setting>
python tools/logging-registry.py diff <previous-registry.json> <new-registry.json>
```

Keep generated inventories and local review files out of a public repository. Reports redact arbitrary string values and omit log contents, but still reveal installed component/file names. Use synthetic fixtures for public tests and examples.

Report both discoveries and the denominator: scanned scopes, file outcomes, limits, unresolved components, missing parsers and uninspected archives/compiled code. Distinguish logging candidates from general debug candidates, configured values from defaults/commented examples, and source declarations from deployed PEX behavior. A failed or empty search does not prove that a mod lacks logging.

Before describing how a switch works, inspect its installed documentation/source or verified runtime behavior. Record reviewed semantics with `--reviews`; reviews are bound to source hashes and become stale when the file changes. No review annotation establishes runtime verification automatically. Use existing log-triage tools to analyze contents separately when requested.

The scanner never toggles settings, launches the game, decompiles scripts or installs dependencies. A request to inventory logging does not authorize enabling debug modes. Some debug switches change gameplay or render overlays rather than merely write logs.
