# Machine resource policy

Physical RAM is the primary memory measure. Windows committed memory is a separate allocation limit,
not physical usage, and must not receive a percentage reserve.

- Self-started tests, scans and workers together use at most 40% CPU and 25% of physical RAM,
  at most eight workers, and low priority. Run stress probes at idle priority and time-box them.
- Admission leaves 20% physical RAM available and a fixed 512 MiB of commit after job reservations.
- Stop our job tree when physical RAM available remains below 2 GiB for ten seconds, or drops below
  1 GiB immediately. Commit thresholds are 256 MiB for ten seconds and 128 MiB immediately.
- Games are exempt from resource wrapping and these floors. Explicitly user-directed work is exempt
  from admission shares/reserves, but its emergency floors remain. Validation or installation does
  not by itself make an explicitly directed job self-started.
- System disk free space stays at least min(pagefile size, 50 GiB); other drives min(5%, 50 GiB).
- Raise a resource concern only when the job could feasibly reach a floor. Never terminate another
  session's processes. Heavy self-started work waits or runs at idle while the user plays.

User clarification, 2026-10-21: fixed 512/256/128 MiB commit margins replace the former percentage
reserve and multi-GiB commit stops. The physical RAM, CPU and disk limits are unchanged.
