r"""The hash of a guard file as GUARD-FILES.sha256 records it (Plan 6 Task 12, R12; fix round 1, C1).

setup.sh writes the interpreter into eight shipped guard files on every install: `PY='{{PYTHON_PATH}}'` in the six
Claude hooks and the Codex .sh entry, `$PY = '{{PYTHON_PATH}}'` in the Codex .ps1 entry, and `{{BASH_PATH}}` in both
Codex entries (setup.sh, "Configure hook scripts"). Hashed as installed, every configured install would banner those
eight files on every session start, and a real edit would hide among them.

So a file is hashed in its REPO form: in the files setup.sh configures (CONFIGURED), the ONE line of each slot is put
back to its token before hashing -- and only when the line has the exact shape setup.sh writes (the whole line is
`PY='<a single-quoted value>'`, with the shell's '\'' escape or PowerShell's '' escape inside). Anything else on that
line (`PY=/tmp/fake`, a trailing command, a second PY line) is left as it is and therefore hashes as a difference.
The configured PowerShell entry may carry a leading UTF-8 BOM so PowerShell 5.1
reads non-ASCII paths correctly; that encoding marker is normalized too.

The value a slot held is returned too. setup.sh writes ONE interpreter (and one bash) into every file, so the caller
compares them across files (consistent_values): a file whose value disagrees with the others was edited after setup
and is reported as differing even though its normalised hash matches. Residue: an edit that sets the same value in all
eight files at once is not seen by the hash (the per-call S-SELF-GUARD note still is).
"""
from __future__ import annotations

import collections
import hashlib
import re
from typing import Dict, Iterable, List, Tuple

_SH_VALUE = r"((?:[^'\r\n]|'\\'')*)"            # PY_Q: ' -> '\''
_PS_VALUE = r"((?:[^'\r\n]|'')*)"               # PY_PS: ' -> ''
SH_PY = ("python", re.compile(r"PY='" + _SH_VALUE + r"'"), b"PY='{{PYTHON_PATH}}'", "'\\''")
SH_BASH = ("bash", re.compile(r"BASH_EXE='" + _SH_VALUE + r"'"), b"BASH_EXE='{{BASH_PATH}}'", "'\\''")
PS_PY = ("python", re.compile(r"\$PY = '" + _PS_VALUE + r"'"), b"$PY = '{{PYTHON_PATH}}'", "''")
PS_BASH = ("bash", re.compile(r"\$BASHEXE = '" + _PS_VALUE + r"'"), b"$BASHEXE = '{{BASH_PATH}}'", "''")

# The files setup.sh's two configure loops rewrite, and the slots each holds (tests/test_skyguard_self_rules.py checks
# this against setup.sh's own loop lists).
CONFIGURED = {
    ".claude/hooks/protect-bash.sh": (SH_PY,),
    ".claude/hooks/protect-files.sh": (SH_PY,),
    ".claude/hooks/protect-mcp.sh": (SH_PY,),
    ".claude/hooks/backup-before-edit.sh": (SH_PY,),
    ".claude/hooks/snapshot-before-tool.sh": (SH_PY,),
    ".claude/hooks/session-kb-guard.sh": (SH_PY,),
    ".codex/hooks/skyguard-entry.ps1": (PS_PY, PS_BASH),
    ".codex/hooks/skyguard-entry.sh": (SH_PY, SH_BASH),
}


def normalised(rel: str, data: bytes) -> Tuple[bytes, Dict[str, str]]:
    """(the repo form of `data`, {slot: the value it held}). Files outside CONFIGURED come back unchanged."""
    slots = CONFIGURED.get(rel.replace("\\", "/"))
    if not slots:
        return data, {}
    if rel.replace("\\", "/").endswith(".ps1") and data.startswith(b"\xef\xbb\xbf"):
        # An encoding marker makes UTF-8 paths readable by PowerShell 5.1; it
        # adds no command or policy. Keep the canonical source hash stable.
        data = data[3:]
    lines = data.split(b"\n")
    done, values = set(), {}
    for i, raw in enumerate(lines):
        cr = raw.endswith(b"\r")
        body = raw[:-1] if cr else raw
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for name, rx, token, esc in slots:
            if name in done:
                continue
            m = rx.fullmatch(text)
            if m:
                done.add(name)
                values[name] = m.group(1).replace(esc, "'")
                lines[i] = token + (b"\r" if cr else b"")
                break
    return b"\n".join(lines), values


def digest(rel: str, data: bytes) -> Tuple[str, Dict[str, str]]:
    body, values = normalised(rel, data)
    return hashlib.sha256(body).hexdigest(), values


def inconsistent(per_file: Iterable[Tuple[str, Dict[str, str]]]) -> List[str]:
    """The files whose slot value disagrees with what the other configured files hold (the most common value per slot;
    a tie names every holder of a minority value, never none)."""
    per_file = list(per_file)
    out = []
    for slot in ("python", "bash"):
        held = [(f, v[slot]) for f, v in per_file if slot in v]
        counts = collections.Counter(v for _, v in held)
        if len(counts) <= 1:
            continue
        top = max(counts.values())
        winners = [v for v, c in counts.items() if c == top]
        keep = winners[0] if len(winners) == 1 else None
        out += [f for f, v in held if v != keep and f not in out]
    return out
