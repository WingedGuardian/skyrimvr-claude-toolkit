# Logging registry

`tools/logging-registry.py` builds a searchable inventory of exposed logging and debug controls. It reads selected loose configuration/source files, records file hashes, inventories installed native/game plugins, and lists log-file metadata. It does not change game files, inspect saves, execute DLLs, read log contents or enable logging.

## Run and refresh

Python 3.10+ is required. JSON, INI-style files and PSC need no extra packages. YAML requires PyYAML; TOML requires Python 3.11+ or tomli. Missing parsers produce explicit unsupported-file rows. Nothing is installed automatically.

```text
python tools/logging-registry.py scan --game-root <game> --config-dir <user-config> --plugins-txt <plugins.txt> --output-dir <reports>/current
python tools/logging-registry.py show <reports>/current/registry.json --filter music
python tools/logging-registry.py diff <reports>/previous/registry.json <reports>/current/registry.json
```

Keep a previous registry in another output directory to compare snapshots. The scanner writes only `registry.json` and `registry.md` in the requested output directory, using atomic file replacement. Output beneath Data, the user configuration directory or an extra input root is refused. A game-local `.skyguard/logging-registry/` or `_CLAUDE_OUTPUT/` directory is suitable. Do not publish a real install's reports.

`--data-root` selects an explicit Data view instead of `<game>/Data`. `--extra-root NAME=PATH` adds a named recursive scope. There is no MO2 load-order resolver in this tool: obtain a resolved virtual view through the existing manager/integration first, or retain the gap. A scan of all MO2 mod folders includes disabled and losing files and cannot establish current settings.

Default control scopes are the game/Data roots (top level), Data's `SKSE/Plugins`, `MCM`, `Interface`, `Scripts/Source`, `Source/Scripts`, `Config` and `NetScriptFramework`, plus top-level user configuration files and `JCUser`/`SKSE`. Log metadata comes from the game root, user SKSE/Logs trees and SkyrimNet's installed log directory. Additional locations require explicit extra roots; this is not a whole-disk search.

Default limits: 25,000 candidate files, 2 MiB per file, 128 MiB total reads, 40 nested container levels and 50,000 traversed nodes per structured file. Symlinks/junctions, known save/backup/cache directories and backup-suffixed files are excluded and counted. Named log/capture/recording/screenshot directories are excluded from control parsing, including their JSON files. Unknown state-JSON formats are not always distinguishable from configuration; discovered keys still remain unverified. File limits, parse errors, files changed during reading and unsupported formats remain visible. XML DTDs/entities are refused. Rotated `.log`, JSONL and NDJSON files are metadata only. No archive contents or binary strings are read. A scan is a rolling snapshot with per-file change checks, not a whole-install lock.

## Meaning of a result

Each control includes a stable ID, portable source reference (`data/`, `game/`, `config/` or `extra/`), key, occurrence, line where supported, source SHA256, sanitized value, value origin, candidate classification, component grouping and review state. Arbitrary strings are redacted; booleans, small numbers and known logging-level words are retained. Sensitive-key subtrees are omitted. There are no original source snippets in the report.

`configured-file` means a value appeared in an exposed file. It does not establish precedence or runtime loading. `reference-default`, `commented-example`, `mcm-definition-default` and `source-declaration` are explicitly different evidence. A PSC default may be overridden by a plugin property or save state. Source trace call sites are counted without claiming execution or compiled-script parity. Candidate matching is heuristic; `debug-candidate` may control gameplay, rendering or diagnostics unrelated to file logging.

Component rows describe installed DLLs, installed game plugins, MCM definition directories and file groups. Filename-based grouping is a navigation aid, not mod ownership. A component with no candidate remains unresolved. `--plugins-txt` adds persisted-list evidence for game plugins; implicit masters and loaded runtime state are not inferred. An installed DLL is never reported as loaded merely because its file exists.

`files` accounts for every inspected candidate and its outcome. `accounting` also counts omissions/exclusions; `scopes` records missing or unreadable locations. The report records its scanner's SHA256, and comparisons distinguish scanner changes from input changes. `gaps` is present even on a successful scan. Exit 0 means the report was produced, not that every logger was discovered. Exit 2 means invalid inputs/output or failure to produce the requested report. A report with unsupported/error/capped rows is partial even when the command exits 0.

## Reviewed semantics

Use a separate local JSON file to add verified output locations, restart requirements or meanings. Copy source/key/hash from the registry; document the actual evidence. Example schema (synthetic names):

```json
{
  "schema_version": 1,
  "reviews": [{
    "source": "data/SKSE/Plugins/Example.ini",
    "key": "Logging.LogLevel",
    "value_origin": "configured-file",
    "source_sha256": "<hash from the registry>",
    "reload": "game restart",
    "output": ["user SKSE/Example.log"],
    "meaning": "Level meanings verified in the installed version's documentation.",
    "evidence": "Installed README, Logging section; inspected version recorded locally."
  }]
}
```

Pass `--reviews <local-reviews.json>` on the next scan. Only matching source/key/hash reviews apply. Use `value_origin` and, for repeated keys, `occurrence` to distinguish configured settings from commented examples; ambiguous reviews are refused. Changed source files produce `stale-review`; absent controls count as unmatched. The scanner does not accept a review's runtime-verification claim. Source review and runtime observation are separate tasks.

The registry is a maintained starting point for investigation. Hidden MCM controls, DLL-only switches, archive-only scripts, compiled-out logging and omitted scopes remain unknown until independently inspected. Existing log files can be historical, and enabled logging may still omit the facts needed for a particular diagnosis.
