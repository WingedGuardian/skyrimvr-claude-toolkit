# Ask / analysis-failure audit — 2026-10-02 (input to toolkit Plan 5)

## Asks
- Toolkit has ONE ask rule: ASK_RESAVER (`shell_rules.py:166-170`).
- Hook asks leave no transcript record; outcomes not measurable from history (MEASURED).
- 7 transcript matches: 0 with ReSaver as the executed program; all embedded text (MEASURED). Replay: 3 asks of 49,343, all embedded (MEASURED).
- One genuine `--apply` on a real save (2026-06-29) ran.
- Recommendation: keep ask, narrowed to executed program (done in install `4a429bd`; port to skyguard). On Codex: deny with a verify-gated escape (ask fails open).
- An ask drops advice accumulated before it (`shell_rules.py:166-177`).

## Analysis failures (decision A)
- Already deny: malformed/empty payload, missing tool_input/command, empty file_path, no root, Python missing, engine crash/no JSON, non-zero wrapper rc.
- Cheap extension to deny: whitespace-only command (`shell_rules.py:105` tests `== ""` only); non-string command (stringified today).
- NOT analysis failures (guard answers allow): opaque scripts (`bash x.sh`, `python x.py`, `eval`), variable targets (`rm -rf $G`). Deny would block routine work → advise ("could not determine effect") at most.
- Relative delete misses: `rm -rf Data` (no trailing sep), `Remove-Item Data\x`, `find Data -delete`, `git clean -fdx`; relative mv/cp/redirect/sed -i into Data/ → allow. Fix by cwd resolution (payload `cwd`), not by deny.
- ReSaver via `java -jar ReSaver.jar --apply reset-havok` not matched.
- `ADV_DELETE` likely unreachable (0 replay hits).

## Noise (replay advise hits, 49,343 calls)
ADV_PLUGIN 1,627 · ADV_EXTERNAL 220 · ADV_LOADORDER 160 · ADV_MOVE 132 · PAPYRUS 72 · ADV_REDIRECT 21 · others ≤ 11.
ADV_PLUGIN fires on 3.3% of all calls — a text match on plugin extensions; prime candidate for the executed-program rule.

## Deny → similar allowed call within 3 calls (HEURISTIC, N=71 denies)
DENY_INSTALL 14/29 (its message tells you to split the command, so expected). Replay would deny 421 (DENY_INSTALL 371); false-positive share not measured.
