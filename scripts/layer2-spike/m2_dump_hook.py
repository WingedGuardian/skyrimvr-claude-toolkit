"""Plan 6 Task 4: a SCRATCH-ONLY observer hook. Appends what each PreToolUse/SessionStart call
carried (tool, input, and the hook process's SKYGUARD_GUARDS) to M2_DUMP (default
C:/Temp/skyguard-ml2/m2/payloads.jsonl). Prints nothing and exits 0: it never decides anything.
Optional argv[1] = "systemMessage": print a hook JSON carrying a systemMessage (Table V probe)."""

import json
import os
import sys
import time

raw = sys.stdin.buffer.read().decode("utf-8", "replace")
try:
    payload = json.loads(raw)
except ValueError:
    payload = {"unparsed": raw[:500]}
row = {"t": time.time(), "event": payload.get("hook_event_name"), "tool": payload.get("tool_name"),
       "input": json.dumps(payload.get("tool_input"))[:600], "SKYGUARD_GUARDS": os.environ.get("SKYGUARD_GUARDS"),
       "session_id": payload.get("session_id")}
with open(os.environ.get("M2_DUMP", "C:/Temp/skyguard-ml2/m2/payloads.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row) + "\n")
if len(sys.argv) > 1 and sys.argv[1] == "systemMessage":
    print(json.dumps({"systemMessage": "SKYGUARD-SYSMSG-7781 lift visible?",
                      "hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "SessionStart"),
                                             "additionalContext": "SKYGUARD-CTX-7782"}}))
