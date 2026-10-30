# Optional cold backup storage

Enable this only for explicitly chosen **backup-generation stores**. Each immediate
child directory is one complete historical generation. Older potentially useful
generations go to a separate archive destination; the newest generations stay
local. Disposable build intermediates belong in cleanup, not in this archive.

The feature is disabled by default. Add this object to your private housekeep
configuration, replacing the absolute paths with your own:

```json
"backup_archive": {
  "enabled": true,
  "destination": "/absolute/secondary-storage/backup-vault",
  "sources": [
    {"path": "/absolute/project/backups/snapshots", "reason": "Historical rollback snapshots"}
  ],
  "min_age_days": 30,
  "keep_local": 2,
  "review_days": 30,
  "review_seconds": 15,
  "obsolete": {}
}
```

UNC paths work on Windows. Use a destination your machine policy permits. Keep
this vault separate from ordinary task archives so their records remain distinct.
Both tiers retain unique bytes until proven obsolete; ordinary archive age is a
review threshold. Link/junction destinations and overlapping stores refuse.

Run the following through your normal housekeep entry point (`scripts/x4housekeep.py`
for X4, `tools/housekeep.py` for Skyrim):

```text
backups report
backups plan -o backup-plan.json
backups apply backup-plan.json --approved-plan-sha256 <printed-plan-hash>
backups review
backups restore <local-receipt.json> --to <new-absolute-directory>
```

Review the exact plan before applying it. A generation is read back from the
archive and every member's SHA256 and length checked. Source bytes are rechecked
before retirement. A local restore receipt and a remote manifest survive; only
then does the local generation go through the existing Recycle Bin handler.
Recycling does not immediately reclaim drive space. A failed NAS operation,
changed source, missing local reserve or corrupt receipt leaves the source.
An interrupted `.partial` archive is retained for inspection, never overwritten.
Identical generations share one content-addressed ZIP while retaining their
separate restore receipts and purposes.

The opt-in tier is an explicit preservation exception to the ordinary rule that
backup/known-good directories are left local. Saves and profile data still need
their separate policy and are refused here. Tracked files, linked evidence,
pins, protected globs, repositories and recent/active generations remain protected.

## Periodic review and eventual removal

Session maintenance checks the configured vault at `review_days` intervals,
within `review_seconds`. Failed/deferred checks retry after one day. There is no
new unattended service or scheduled task. Checks report corrupt, unavailable,
deferred and retained records separately. They never delete anything.

**Age, disuse, a new version and an uncertain guess do not establish obsolescence.**
Usefulness can remain unknown indefinitely. Such backups stay indefinitely.
When a person has evidence that a particular historical backup is no longer
needed, add its receipt ID to `obsolete`:

```json
"<receipt-id>": {
  "basis": "operator-confirmed-obsolete",
  "reason": "Concrete explanation of why this restore point has no remaining purpose",
  "evidence": "Reference to the reviewed disposition or replacement proof"
}
```

Then run `backups prune-plan -o obsolete-plan.json`. Review its exact objects and
reasons before `backups prune obsolete-plan.json --approved-plan-sha256 <hash>`.
This is permanent removal, appropriate only with explicit approval for that
plan, especially on a NAS without a Recycle Bin. Changing the declaration,
configuration, archive bytes or references invalidates the approval. An archive
shared by another retained restore point is kept. Small retirement receipts
remain to explain the disposition. No blanket age expiry applies to this vault.

Ordinary cleanup follows the same certainty rule; see the retention policy's explicit obsolete decisions. Archive age never authorizes deletion.
