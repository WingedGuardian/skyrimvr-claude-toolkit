# Machine resource policy

Limits exist to stop the machine slowing to a crawl; barring that, everything runs.

Physical RAM is the primary memory measure. Windows committed memory is a separate allocation limit,
not physical usage, and must not receive a percentage reserve.

- A self-started test, scan or worker waits only if it would push the machine past 90% CPU or leave
  under 3 GiB of physical RAM (20% of RAM while a game is running) or 512 MiB of commit. Use at most
  eight workers and low priority. Run stress probes at idle priority and time-box them.
- Stop our job tree when physical RAM available remains below 2 GiB for ten seconds, or drops below
  1 GiB immediately. Commit thresholds are 256 MiB for ten seconds and 128 MiB immediately.
- Games are exempt from resource wrapping and these floors. Explicitly user-directed work is exempt
  from these admission limits, but its emergency floors remain. Validation or installation does
  not by itself make an explicitly directed job self-started.
- System disk free space stays at least min(pagefile size, 50 GiB); other drives min(5%, 50 GiB).
- Raise a resource concern only when the job could feasibly reach a floor. Never terminate another
  session's processes. Heavy self-started work waits or runs at idle while the user plays.

User clarification, 2026-10-21: fixed 512/256/128 MiB commit margins replace the former percentage
reserve and multi-GiB commit stops.

User ruling, 2026-11-02: shares between jobs (40% CPU, 25% RAM) and the standing 20% RAM reserve are gone.
The stop floors and disk limits are unchanged.
