"""Never a permanent delete: what the toolkit prunes from its own stores is RETIRED, then recycled.

User ruling 2026-10-18: every removal goes to the Recycle Bin, with no exceptions; only scratch a run created itself
may be deleted by that same run. Pruned snapshot blobs, recovery blobs and old session logs were written by EARLIER
runs, so they are not that scratch.

A hook cannot afford the Recycle Bin inside its time budget (the shell's delete is one PowerShell process, about a
second, and can raise Windows' "too big for the Recycle Bin, delete permanently?" prompt). So pruning only RENAMES
each item into one batch folder under `<project>/.claude/retired/` -- a same-volume rename, as cheap as the unlink it
replaces, and atomic. housekeep's session sweep (tools/housekeep.py, launched detached at every session start) moves
each settled batch folder to the Recycle Bin whole. Off Windows there is no bin to call: the batches stay in
.claude/retired/ and the session note says how much is waiting there.

Never raises: a store's pruning must not cost a hook its verdict. An item that cannot be moved stays where it is.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

RETIRED = "retired"


def retired_dir(store) -> Path:
    """`<project>/.claude/retired` for a store anywhere under a `.claude` folder; otherwise a `.retired` folder beside
    the store. Always an ancestor's child, so the rename never crosses a volume."""
    store = Path(store)
    for p in (store, *store.parents):
        if p.name == ".claude":
            return p / RETIRED
    return store.parent / ".retired"


class Batch:
    """One prune's worth of retired items: a fresh folder `<stamp>-<tag>-<pid>-<n>` made on the first retire()."""
    _seq = 0

    def __init__(self, store, tag):
        self.root = retired_dir(store)
        self.tag = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(tag))[:40] or "store"
        self.dir = None
        self.count = 0

    def _folder(self):
        if self.dir is None:
            Batch._seq += 1
            d = self.root / "{}-{}-{}-{}".format(time.strftime("%Y%m%d-%H%M%S"), self.tag, os.getpid(), Batch._seq)
            d.mkdir(parents=True)          # a name already taken raises: never mix two batches
            self.dir = d
        return self.dir

    def retire(self, path) -> bool:
        """Move `path` (a file or a folder) into this batch. True when moved; False leaves it in place."""
        try:
            d = self._folder()
            self.count += 1
            os.rename(str(path), str(d / "{}-{}".format(self.count, Path(path).name)))
            return True
        except OSError:
            return False
