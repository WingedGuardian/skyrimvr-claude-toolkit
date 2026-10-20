# ReSaver analysis overlay — modified third-party source

The `.java` files under `resaver/ess/` in this folder are **modified copies of ReSaver
(FallrimTools) source** by Mark Fairchild, redistributed under their original **Apache License
2.0** (see the per-file headers). Per Apache-2.0 §4(b), each file carries a prominent
`MODIFICATION NOTICE` describing what the Skyrim AI Assistant Toolkit project changed.

## What these are

They extend ReSaver's `.ess` changeform **parse coverage** for a few body / extra-data types that
stock ReSaver doesn't fully decode (extra-data types 16 & 135, QUST QuestInstances, extra REFR
extra-data). They are consumed **only** as an on-the-fly `javac` classpath overlay that the
`tools/resaver-cli.sh` wrapper places *in front of* your own `ReSaver.jar` — **and only for
read-only diagnostic ops** (`recon`, `changeform`, `extradata-scan`, `changeform-diff`, `info`,
`dump`, `find`, …). Every **write-capable** op runs the **stock** jar with no overlay, so an
authored parse fix can never reach `ChangeForm.write()` / `writeESS()` (corruption safety).

## Licensing / distribution

- These modified sources are Apache-2.0 (permissive) — redistributing them in source form here is
  compliant as long as the license header + modification notice are retained (they are).
- **`ReSaver.jar` itself is NOT bundled** — you download it separately (FallrimTools, Nexus mod
  5031). The overlay is compiled against *your* copy at first run.

## ReSaver version compatibility

The overlay is authored against a specific ReSaver release's internal API. If your downloaded
`ReSaver.jar` is a different version and the overlay fails to `javac`-compile against it, the
wrapper **automatically falls back to stock parsing** for that read op (you lose the enhanced
changeform coverage, but `info`/`dump`/`find`/`worries` and all write ops keep working). Swapping
ReSaver versions needs no action: compiled classes live in `tools/resaver-cli/.compiled/<key>/`, keyed by
the digests of `ReSaver.jar`, `lib/*.jar`, these sources and the JDK (`tools/resaver_cli_cache.py`), so a
different jar is a different key and compiles on first use. Nothing is compiled into this folder.
