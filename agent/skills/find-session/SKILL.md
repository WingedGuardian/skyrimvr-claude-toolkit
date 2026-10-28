---
name: find-session
description: Find the past agent session where something was discussed ("do you remember when we...", "what session was that in", "find the conversation where we..."), down to the passage, and resume it. Use whenever the user asks about an earlier conversation rather than the code.
argument-hint: "[what was discussed]"
---

# Find a past session

Semantic search over every agent session transcript on this machine, both harnesses, with the hits grouped by
session. The
search engine is obra/episodic-memory, installed separately; `tools/modding_harness/find_session.py` is the glue: it
points the tool at its index (without that it silently searches an empty one), updates the index first, drops
injected boilerplate, and prints one block per session.

## Run it

Use the user's own words for the query, not keywords you extract from them:

```bash
python tools/modding_harness/find_session.py "going over my balance tweaks with missiles"
```

On Windows use the same interpreter the ledger commands print at session start, or `py -3`.

Give the command up to 5 minutes by setting your command tool's timeout to 300000 ms (the `timeout` or `timeout_ms`
field). Default limits are shorter than a run: one harness stops a command after 10 s.

The tool first updates the index: about 13 s when nothing is new and about 2 minutes after a day of sessions. It
waits up to 4 minutes for that, then searches what is already indexed. Cut it short and the update stops with it;
the next search then reports the update as skipped for about 10 minutes.

The first line of output says how the update went:
- `index synced`: complete.
- `still running`, `skipped` or `failed`: the newest sessions may be missing. Tell the user. If the answer is not
  there, search again in a few minutes.

Add `--no-sync` only when the user wants a quick answer and accepts that the newest sessions may be missing. `--json`
gives the same result as data.

Exit 0 means sessions were found, 1 means none matched, and 2 means the search could not run. On 2, report the reason
the tool printed (not installed, no index, Node missing). Never answer from memory instead.

## Read the result

Each session block shows:
- the match score, the date range, the harness (claude or codex) and the project;
- `began:`, the session's first typed prompt;
- the best passages, each with its transcript line range. A passage marked `reply:` is the agent's answer under an
  injected turn, and `-> reply:` follows a terse user turn such as "ok";
- the transcript path, and the resume command with the directory it must run from (a session is found only from
  the directory it ran in).

## Answer the user

- **One clear match:** name it (date, project, what it began with), quote the matching passage, and give the resume
  command together with the directory to run it from.
- **Several plausible matches:** this is the common case. Do not pick one. Show the two or three that fit, one line
  each: date, project, and what that session was about, taken from its passages. Then ask the user for the detail
  that separates them, for example "the one where we chose the 20% damage bump, or the one about reload times?".
  Re-run with their detail added to the query if that helps.
- **A near miss** (the user said "beta 47" and only beta 28 sessions come up): say so plainly and show the closest.
  The user may be misremembering, or the session may predate the index.
- **No match:** say so, try once with different wording, then ask for another detail. Do not invent a session.

To read more of a session, open its transcript at the line range shown, or resume it with the printed command.

## Not installed

If the tool reports that episodic-memory is not installed or has no index, tell the user. Installing it is their
decision: obra/episodic-memory at commit 7e06519, installed into `~/.local/share/episodic-memory` as its README says
(that commit ships no lockfile, so its dependencies resolve at install time), then one full index build with
`EPISODIC_MEMORY_CONFIG_DIR` set to `~/.local/share/episodic-memory-data`, where the tool looks first. That build took
about an hour and used about 4.5 GB on a workstation with 1,349 conversations. Do not install it unasked.
