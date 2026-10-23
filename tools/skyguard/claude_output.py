"""Validate this toolkit's complete Claude PreToolUse output without loading the policy engine.

The shell transport runs this under its existing outer deadline. Its nonce response acknowledges
validation only; the original engine bytes are emitted unchanged after that acknowledgement.
"""
from __future__ import annotations

import json
import sys


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite JSON number")


def _scalar_text(value):
    return isinstance(value, str) and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def valid(data: bytes) -> bool:
    try:
        output = json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
        if not isinstance(output, dict):
            return False
        if not output:  # The renderer's ordinary allow is exactly an empty object.
            return True
        if set(output) != {"hookSpecificOutput"}:
            return False
        hook = output["hookSpecificOutput"]
        if (not isinstance(hook, dict) or hook.get("hookEventName") != "PreToolUse" or
                set(hook) - {"hookEventName", "permissionDecision", "permissionDecisionReason", "additionalContext"}):
            return False
        if not ("permissionDecision" in hook or "additionalContext" in hook):
            return False
        if "permissionDecision" in hook and hook["permissionDecision"] not in ("allow", "ask", "deny"):
            return False
        if "permissionDecisionReason" in hook and ("permissionDecision" not in hook or
                                                    not _scalar_text(hook["permissionDecisionReason"])):
            return False
        if "permissionDecision" in hook and hook["permissionDecision"] in ("ask", "deny") and "permissionDecisionReason" not in hook:
            return False
        if "additionalContext" in hook and not _scalar_text(hook["additionalContext"]):
            return False
        return True
    except (UnicodeError, ValueError, RecursionError):
        return False


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isascii() or not sys.argv[1].isdigit():
        return 2
    if not valid(sys.stdin.buffer.read()):
        return 2
    sys.stdout.buffer.write(("skyguard-claude-output:" + sys.argv[1]).encode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
