# House Carl integration

House Carl is an optional data-layer backend. The toolkit installs an unchanged, separately licensed
2.0.5 server and places one gateway between it and either agent. Both CLI and MCP calls use the same
publication policy. The gateway does not observe gameplay, sort or activate plugins, or touch saves.

## Install and verify

Windows x64, Python 3.10 or newer, .NET Runtime 9 and ASP.NET Core Runtime 9 are required. Both .NET
runtimes must already be installed; setup reports missing prerequisites without installing system
software. Node and the toolkit's separately installed xeditlib are required for independently
validating writes. Read `docs/installing-tools.md` for those tools.

For Wrye Bash with deployed Data:

```text
python tools/housecarl.py setup --edition VR --plugins-txt <absolute-plugins.txt> --config-dir <game-INI-folder>
```

`--data-dir` overrides the toolkit root's Data. `--archive <zip>` uses an already downloaded archive;
otherwise setup downloads the exact pinned GitHub release. The SHA256 is
`c3d3ca7e124d92c65f7a1b935b4a598e8d69f2f1fe900224bab5d1b352f0df19`.
It must match before extraction. Both package version and installed server-file hashes are checked.

For an MO2 profile, pass `--mo2-instance <folder-containing-ModOrganizer.ini>` instead of the two
Wrye paths. The selected profile's order, mod priority and overwrite folder supply the inputs.
Unsupported QSettings encodings must be resolved explicitly; do not guess another profile.
MO2 supplier priority and activation are fixture-tested; a real MO2 installation remains unqualified.

Setup puts the backend and an isolated, hash-locked MCP SDK 2.3.0 environment under
`.skyguard/housecarl/`. It does not run upstream's global setup program. `--agents claude|codex|all|none`
selects project registration; default `all`. Other configured connections are preserved. A conflicting
existing House Carl entry is refused, not silently replaced. Registration configs are backed up first.
Restart the relevant host to discover new tools; project trust is still required where the host requires it.

```text
python tools/housecarl.py doctor
python tools/housecarl.py call toolkit_housecarl_status
python tools/housecarl.py call housecarl_records --arguments-file request.json
```

The command line works without host rediscovery. The toolkit skill is generated for both agents.
Upstream references remain in the separately installed package, accessible through that skill.

## Inputs and capabilities

| Capability | Gateway behavior |
|---|---|
| Record queries, sources, fields, predicates, differences and walks | Upstream tool schema, with plugin-qualified FormKeys |
| SkyPatcher inventory and replay | Static model, retaining unknown-operation and coverage notices |
| Config references | `housecarl_skse findings="config"`; comments are included by upstream |
| Asset/NIF inspection and BSA listing | Reads configured deployed content; archive coverage and ownership can be incomplete (see below) |
| Ordinary plugin authoring | New patches staged; existing files backed up and replaced only after validation |
| Native pairing, VR runtime addresses and facegen provenance | Explicitly unavailable in this release |
| Merging, compaction and external-reference remapping | Not exposed |
| NIF/archive writes, compilation and runtime tests | Existing toolkit workflows |

Wrye Bash is the configured load-order authority; file deployment is a separate role. For this format,
`plugins.txt` carries both ordering and activation. A separate old `loadorder.txt` is not a fallback.
The reader accounts for implicit masters, master flags and VR ESL support, and refuses missing files
or inactive dependencies. Source files are never rewritten. Status prints the whole resolved sequence,
so a fresh Wrye export can independently qualify it; historical exports do not establish current state.

Every call captures its configured plugin/profile inputs. Source/configuration/plugin changes invalidate the result rather
than becoming a quiet partial answer. The backend is owned per call and closed on completion or
cancellation; no background game polling or user-process termination is involved.
Before data tools run, the pinned backend's resolver count and exclusion alarm must confirm the complete
captured plugin set. This does not fingerprint every loose asset; retain upstream coverage and truncation notices.

Local qualification used Windows Python 3.10 and Skyrim VR: 684 enabled plugins, an independently
matched Iron Sword winner, real MCP calls, and isolated create/apply/forward/copy/create-plugin/remove/restore
with a manager hardlink stand-in. Creating a record inside an ESL-flagged plugin and owned-process cancellation
also passed. This proves those fixtures, not every record type or mod's gameplay behavior.
The current whole sequence still needs a fresh Wrye UI/export cross-check; a historical export is insufficient.
Status supports a plugin-basename filter on this captured membership; alternate-profile and mod-folder
lookups are unavailable through the gateway. Creation does not compile scripts or supply voice/SEQ assets;
retain upstream notices about those requirements before using a plugin in game.

## Archive discovery limit

[Issue 11](https://github.com/WingedGuardian/skyrimvr-claude-toolkit/issues/11) reports asset-absence
results without a coverage warning when applicable base archives were omitted. The gateway builds
its backend INI from the configured user/profile INIs and adds the VR archive; it currently does
not import archive lists from the game-root INI. A synthetic SE/VR profile check reproduced that
omission. The public toolkit pins 2.0.5; the original report used an installed 2.1.0 backend.

Treat a missing asset result as inconclusive until the relevant base archives are independently
accounted for, even when the backend says `read_incomplete: false`. Plugin-index completeness does
not establish archive completeness. This is a file-discovery limit, not evidence of the runtime
asset winner. Do not change the user's INIs or activation/order files to make a query agree.

## Existing-file publication and recovery

The gateway first copies and independently hash-verifies the original into its permanent backup
store. Upstream writes only a private target copy. xEdit then parses the candidate and checks its
references; unchanged records are compared with the original outside the requested edit set.
Identical-to-master/previous-override findings are retained as warnings, since forwarding can intentionally
produce them; structural, reference and unknown error groups block publication. Nothing is auto-cleaned.
These group definitions follow upstream [xeErrors.pas](https://github.com/matortheeternal/xedit-lib/blob/DelphiTokyo/src/xeErrors.pas).
Upstream preflight and readback remain additional checks. A failed or incomplete check prevents
publication. Changes to the original or captured inputs also prevent publication.

Publication replaces the directory entry using a verified sibling file. It never writes through a
mod manager's hardlink into its source copy. The receipt records hashes, backup, requested operation,
validation and publication state. If publication or receipt persistence fails after the commit, inspect
the retained target and evidence; do not assume the original is still installed.

Existing edits affect the active deployed file only; redeployment may replace them. New plugins remain
under the integration's patches directory until the user installs and activates them. No save or
activation changes are part of either operation.
With MO2, the active file is its winning mod/overwrite supplier. No other copy is edited.
Closure copy supports new patches only. Existing targets with pre-existing xEdit reference errors are
currently refused; nested edits that change an unrequested containing record can also be refused.

```text
python tools/housecarl.py restore <transaction-id>
```

Restore verifies the retained backup and requires that the destination still match the published
result. It backs up that result before restoring the original. All backups and transaction evidence
are retained. A later user edit requires explicit conflict resolution, not a forced restore.

The gateway is an application boundary, not an OS sandbox. Directly launching a different server or
writing files through another tool does not acquire gateway protection. `skydoctor` reports the optional
installation separately from the agent-hook coverage.

## Updates and removal

Updates are explicit and version-pinned. Setup stages a new backend installation and preserves the
previous configuration and backend. It never changes pins to follow an upstream release automatically.
Project MCP registration can be removed without deleting backups, patches, transactions or upstream
files. Keep evidence until the user chooses its retention; never delete a whole state tree as cleanup.
Use `python tools/housecarl.py unregister --agents all` to disconnect this gateway and `register`
to reconnect it. These commands preserve unrelated server entries and retain registration backups.

The toolkit remains MIT. House Carl and its references remain GPL-3.0-only in their separate package;
they are downloaded at installation, not included in the toolkit release archive. The MCP SDK is MIT.
