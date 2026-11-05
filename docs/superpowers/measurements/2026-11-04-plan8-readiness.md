# Plan 8 release readiness, final candidate, 2026-11-04

Replaces the numbers in `2026-10-08-plan8-readiness.md` for the release decision. The table below is on `afdaf16`, merged to
`integ/v4-fixes` as `5d078e4` with no file differences, unless a row says otherwise. Later commits on `integ/v4-release` add this
document, the repository links and the fixes from the final review below. They change the guard's reading of some commands, so T14b
and the cold build are on the earlier code, and the tag needs its own green CI run on the exact final commit.

## Automated evidence

| Check | Result |
|---|---|
| Full CI, run 41940850596 on `afdaf16` | 42 of 42 jobs passed, Linux and Windows, Python 3.10 to 3.13 |
| Pre-push gate on `afdaf16` | Passed |
| Guard pricing, `0a3767e` against `35f8cd0`, 115,073 real calls | 0 newly refused; 114,834 allow to allow; 96 deny to deny; 53 stay refused as unparsed; 14 unparsed now allowed, 62 advise and 14 ask. Parse failures 156 to 142 |
| Guard pricing, final review's fixes against `0a3767e`, 115,964 real calls | No decision moved in either direction. Parse failures 144 both |
| Cold build | Producer and cold clone archives byte-identical, 368 files |
| Payload checker | Wrong-commit control failed; correct commit passed |
| Scrubbed artifact verify | One failure: the CHANGELOG heading still says unreleased, cleared by the release date. Run with `TMPDIR` outside the home folder: from inside it, setup's generated paths read as leaks |
| Hook latency and deadlines | `2026-11-04-plan8-latency-deadline.md`: 135 cells, 905 runs, none failed open or late |
| Third-party audit | `2026-11-04-plan8-thirdparty-audit.md`: 115 rows, no DRIFT, 2 COULD NOT CHECK |

## Review

Every shipped line of `v3.9.1..35f8cd0` was read in seven lanes: 131 findings (8 high, 43 medium, 80 low). A review of the
fixes found 20 more. Independent reviews of `35f8cd0` (6 medium, 5 low) and `9190266` (4 medium, 17 low) followed; all their
medium findings were addressed. A final independent review of `9190266..1ab431b` found no high findings, 3 medium and 7 low:

- Medium: `cp --parents` with `-t`, `--target-directory` or a shortened option could overwrite a save with no backup or approval.
  The fix also ends false approval prompts on named settings files. This completes the earlier fix for the positional form.
- Medium: the Nexus draft's link and its claim that a guard that cannot run always refuses. On Codex a hook that crashes or times
  out fails open. The README said the same, and both are corrected.
- Low, fixed: more spellings of a Python save delete, a dotted `python -W` or `-X` value hiding a piped program, false refusals from
  `-Wignore`, `mod-install`'s plan misreporting a replaced file, a missing test and twins.
- Low, reported instead: a hand-corrected MO2 profile path from 3.x still lasts one setup run; setup now says so.

The commit fixing these has had no independent review of its own. The priced decisions did not move.

## Not run, by the user's ruling of 2026-11-04 ("no need for any of the tests")

The whole manual batch, so none of these has evidence from this candidate:

- Live Codex and Claude Code terminal sessions on the candidate archive, including approving Codex hooks in `/hooks`. The last
  live agent evidence is `2026-11-02-plan8-agent-recheck.md`, on an earlier candidate with `claude -p` and `codex exec`.
- Whether Codex hooks fire on an MCP call.
- Wrye Bash and the xEdit program against the optional master lock. The README says these have not been run, and the lock stays
  off by default.
- An in-game load of a save written by ReSaver Plus at upstream 51. The CHANGELOG says so.

Docker legs and a live OpenCode run were taken off the list by the same day's ruling. The docs say running-host enforcement in
OpenCode is unmeasured. The DevBench live checks were dropped by the user's ruling of 2026-10-29.
