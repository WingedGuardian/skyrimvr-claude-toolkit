> Evidence from the toolkit's development. Not instructions for your agent.

# Retention shared-policy text: follow-ups to sync into X4 4.1.0

Recorded 2026-11-01 while merging master's housekeep/retention arc into the v4.0.0 line.

`docs/RETENTION.md` carries a shared-policy block (between `<!-- shared-policy:begin -->` and
`<!-- shared-policy:end -->`) that must be byte-identical in the Skyrim and X4 toolkits. Its sha256 is pinned
in both repos (`tests/test_retention_policy.py`, `084bdd17bfc90466c757cd438b0fd6e05f7b60af7b8c2ed0a81b484f6aa24c4b`).
The merge keeps master's block exactly. The v4 line had edited three rows of the block's class table; those edits
are not dropped. Their Skyrim-specific substance now sits below the block, under "Skyrim checklist" -> "The shared
classes in this toolkit". The wording changes themselves are recorded here verbatim, to be proposed for the shared
text in X4 4.1.0, which would then mean a new pinned hash in both repos.

Nothing here changes the current shared text.

## 1. Forever row

v4 wording (cf67075):

| **Forever** | Saves and every copy of them, save backups included; every other backup store and its manifests (anything under a folder named `*backup*`) except the rolling tool stores below; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |

Shared wording kept (master):

| **Forever** | Saves and every copy of them; backup stores and their manifests (except explicitly configured rolling tool stores below); knowledgebase snapshots; known-good baselines; audit logs; folders a person labelled by hand | Never removed by a tool. A person decides. |

Differences to reconcile: "save backups included"; "anything under a folder named `*backup*`"; knowledgebase
snapshots removed from Forever (in this toolkit they are a rolling tool store, user ruling 2026-10-24, rotated by
`tools/kb-guard.sh`).

## 2. Rolling tool store row

v4 wording (cf67075):

| **Rolling tool store** | A tool's own rotating snapshots of the files it watches (script, config and knowledgebase snapshots), even under a `*backup*` folder. Each toolkit's checklist names its stores and their numbers | Kept by age or count, always with the newest copy; the rest goes to the Recycle Bin. |

Shared wording kept (master):

| **Rolling tool store** | Configured rolling snapshots, including stores under backup folders | Retire only a byte-identical repeated generation after re-proving the retained copy. Newest and distinct generations stay. |

Differences to reconcile: v4 describes the guard's age/count rotation; master describes what `housekeep` itself may
retire. Both are true of this toolkit for different actors.

## 3. Toolkit temp row

v4 wording (cf67075):

| **Toolkit temp** | Directories the toolkit's own code and tests make in the system temp dir, by the toolkit's own name prefixes only (never a folder every project shares, such as pytest's `pytest-of-<user>`), and, where a toolkit keeps them, the batches a rolling tool store retires into `.claude/retired/` | Recycled by the session-start sweep once nothing in them changed for 24 hours (a retired batch: 10 minutes) and no process holds a file open inside. |

Shared wording kept (master):

| **Toolkit temp** | Leftover workspaces explicitly declared disposable by their producer when newly created | Name, ownership and age alone are insufficient. Require a producer disposition receipt or explicit content-bound obsolete decision, then the ordinary protection, inactivity and in-use checks. Legacy unmarked workspaces stay. |

Differences to reconcile: own-prefix-only scope and the explicit pytest exclusion; the `.claude/retired/` batches and
their 10-minute settle time. The v4 rule text ("recycled ... once nothing in them changed for 24 hours") predates
master's disposition-receipt requirement and must not be adopted as written: the merged code requires the receipt or
an obsolete decision for temp dirs (`sweep_temp`), while retired batches are recycled on settle (`sweep_retired`).
