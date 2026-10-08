# Plan 8 rename classification — 2026-10-08

Inventory: every literal occurrence in `f14bae1` before T11, including non-shipped tests and dated plans.
The product is Skyrim AI Assistant Toolkit. The archive slug has one source: `package.json` → `name`.
The lockfile mirrors npm metadata; tests assert the required archive name literally.
The GitHub repository and historical fixture paths retain their existing identity. No remote was changed.

47 occurrences classified; no unclassified occurrences.

| Source line (occurrence) | Decision | Reason |
|---|---|---|
| `KNOWLEDGEBASE.md:2045` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `README.md:749` (1) | keep | Repository identity; owner has not renamed the GitHub repository. |
| `docs/superpowers/plans/2026-10-01-plan2-skyguard-engine.md:290` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `docs/superpowers/plans/2026-10-02-plan3-instruction-split.md:199` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `docs/superpowers/plans/2026-10-02-plan4-codex-target.md:252` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `docs/superpowers/plans/2026-10-04-plan8-release-v4-DRAFT.md:287` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `package-lock.json:2` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `package-lock.json:8` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `package.json:2` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `scripts/build-nexus-pack.sh:68` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `scripts/build-nexus-pack.sh:76` (1) | keep | Repository identity; owner has not renamed the GitHub repository. |
| `scripts/build-nexus-pack.sh:132` (1) | keep | Repository identity; owner has not renamed the GitHub repository. |
| `scripts/build_release_zip.py:43` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `scripts/guard-corpus/record.py:490` (1) | keep | Historical checkout path exercising a guard regression. |
| `scripts/layer2-spike/mo2_probe.md:22` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `scripts/thirdparty-audit.py:59` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/fixtures/migrate-3x/v3.9.1/KNOWLEDGEBASE.fixture:1853` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `tests/guard_corpus/expected.jsonl:231` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `tests/guard_corpus/expected.jsonl:275` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `tests/guard_corpus/reference/protect-bash.sh:77` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `tests/guard_corpus/reference/protect-bash.sh:80` (1) | keep | Dated historical evidence or frozen regression fixture; not current product naming. |
| `tests/test_hooks.py:376` (1) | keep | Historical checkout path exercising a guard regression. |
| `tests/test_hooks.py:412` (1) | keep | Historical checkout path exercising a guard regression. |
| `tests/test_hooks.py:413` (1) | keep | Historical checkout path exercising a guard regression. |
| `tests/test_mutations.py:5994` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_nexus_pack.py:30` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:72` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:74` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:75` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:110` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:151` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:152` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:160` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:188` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:245` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:260` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:261` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_release_build.py:295` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_verify_release.py:69` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tests/test_verify_release.py:153` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tools/resaver-cli/analysis-overlay/NOTICE.md:6` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tools/resaver-cli/analysis-overlay/resaver/ess/ChangeFormExtraDataData.java:18` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tools/resaver-cli/analysis-overlay/resaver/ess/ChangeFormQust.java:18` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tools/resaver-cli/analysis-overlay/resaver/ess/ChangeFormRefr.java:18` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tools/verify-release.sh:99` (1) | keep | Repository identity; owner has not renamed the GitHub repository. |
| `tools/verify-release.sh:116` (1) | rename | Current product, archive contract, package metadata or neutral example. |
| `tools/verify-release.sh:122` (1) | rename | Current product, archive contract, package metadata or neutral example. |

## Additional scope

The README title and package description use the new product name; ReSaver attribution remains intact.
ADAPTING, the toy adapter, conformance tools and frozen Codex definitions are checked separately for old naming.
Maintainer identity scrubbing is verified against the built payload, separately from historical-slug classification.
