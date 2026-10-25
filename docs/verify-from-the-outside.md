# Verify from the outside, and prove the check could fail

A working principle for the moments when being wrong is expensive: reviewing a release, hunting a bug,
writing or changing a safety guard, and trusting any tool that prints a number you will act on. It is
not meant for routine one-line edits. Each rule below describes a failure that is easy to make and
hard to see.

## 1. A correct fix in the wrong wiring is still a bug

The commonest expensive failure is a fix that is right and **inert**: the change is correct, but the
thing that actually runs never reads it. Verify through the real entry point, from the outside. Do not
verify by re-reading what you just edited, and do not verify through the path you just repaired.

| The fix is correct in... | ...but the game or user reads | Verify with |
|---|---|---|
| the `.psc` source | a stale `.pex` (scripts load at game startup only) | a per-build marker line in the Papyrus log |
| your plugin's record | whichever plugin WINS the override | `bash tools/skyrim-winner.sh winner <formid>` |
| an asset inside a BSA | a loose file that overrides it | look for a loose copy before assuming the BSA wins |
| an auto property | a non-auto property, still blank after a load | read it at runtime (DevBench), not in xEdit |
| one of three call sites | the two you did not change | fix at the choke point (section 2) |
| the repository, and the tests that pass on it | the published archive users extract | check a fresh extract of the archive, not the working tree |
| your local copy | the public copy you never updated | compare the shipped file set, not the files you had in mind |

When a check passes, ask whether it passed for the right reason. A control that succeeds for a reason
unrelated to the fix is worse than no control, because it is believed.

## 2. Fix at the choke point, and read the ordering

If the same defect can come back through a caller that does not exist yet, the fix is in the wrong
place. Put the guard where the action happens, so a future caller inherits it.

A backup, prune, snapshot or validation placed AFTER the operation it exists to make safe protects
nothing, and it still looks completely correct. Check the order, not only that the step is present.

## 3. Every check must be able to fail

For every verification, say out loud what, concretely, would have made it go red. If the answer is
"nothing that could actually have happened", the check is decoration.

- A hook can be dead while its tests stay green: a test pipes the hook its input, while the real host
  may deliver it differently. A green result fixed before the check ran is the normal failure mode, not
  an exotic one. `bash tools/hook-canary.sh` reads evidence from real invocations for this reason.
- A check whose pattern matches its own documentation is worse than none. It goes red on correct code
  the moment someone writes a comment explaining the defect, and then gets "fixed" by weakening it.
  Match the construct, skip comments, and run the check against the repaired state too, not only the
  broken one.
- For anything that guards real work (a hook, a gate, a refusal path), prove it by mutation: break the
  guarded thing on purpose and require the named check to go red. "Some test failed" is not enough. The
  baseline must be green first, or no mutation result means anything.
- For a compound condition, write one mutation per clause. Each clause shadows the ones behind it, so a
  single mutation only ever tests the first clause it trips.
- A "no X in any item" check must first prove there are items. A loop over an empty set asserts nothing.

## 4. Reproduce before you fix, up the cheapest ladder that can still falsify

A finding you cannot reproduce is withdrawn, not "probably real". Reproduction does not have to mean a
game session per finding. Climb only as far as you need to actually falsify the claim:

1. read the bytes, the record or the log (free)
2. an independent parser, or `skyrim-winner` (seconds). Never the tool that wrote the file: a mesh
   that passes its own writer's readback can still crash the engine.
3. DevBench against the running game, driven by the agent (skill `devbench`)
4. the user's own play session or headset: the last resort, with tests batched

Someone else's measurement is evidence, not truth. That includes other agents, a mod page's bug
report, and your own earlier turns. Measure again before acting on it.

## 5. A search that finds nothing is a lead, never a fact

Write "my search for X found nothing", never "X does not exist". Before any negative claim, run a
second search shaped differently from the first. Modded Skyrim has many hiding places: the thing may be
inside a BSA, shadowed by a loose file, distributed at runtime (SPID, KID, SkyPatcher), built by a
dialogue or quest fragment, spelled in the other case (`WARNING:` and `warning:`), or in a plugin
outside the subset you loaded. A who-wins answer computed against a guessed subset of plugins is only
as complete as the guess.

## 6. Housekeeping that keeps a result meaningful

- Do not edit the tree while a review or a mutating test pass is running over it. A mutating pass
  rewrites files in place; a copy taken at that moment carries the mutation and looks right.
- Restore anything you mutate, and confirm it is byte-identical (compare hashes).
- Never claim a range or a count you have not summed from the actual data, and give its denominator.
- Re-read a shared file before quoting it; what you remember of it is a snapshot.
- Write down your own checking mistakes (in `KNOWLEDGEBASE.local.md`). A mistake in the checking step
  is the most repeated defect there is, and it stays invisible unless someone records it.
