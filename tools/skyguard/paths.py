"""Which directories are "the install" and "the config dir" -- ported from protect-bash.sh's
path_to_ere/add_root. Roots are regex alternations matching a path in EITHER dialect (C:/ or /c/)
with ONE OR MORE separators of EITHER kind, case-insensitively (the grep is -i)."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

SEP = r"[/\\]+"
_META = set("[]*+?^$(){}|.\\")
CONFIG_LITERAL = r"Documents[/\\]+My Games[/\\]+Skyrim"
ENV_KEYS = ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR")


def path_to_regex(path: str) -> Optional[str]:
    p = path.replace("\\", "/")
    if re.match(r"^[a-zA-Z]:/", p):
        drive, tail = p[0], p[3:]
    elif re.match(r"^/[a-zA-Z]/", p):
        drive, tail = p[1], p[3:]
    elif p.startswith("/") and len(p) > 1:
        drive, tail = "", p[1:]
    else:
        return None
    if not tail:
        return None
    out = "".join(SEP if c == "/" else ("\\" + c if c in _META else c) for c in tail)
    return "({d}:|{s}{d}){s}{o}".format(d=drive, s=SEP, o=out) if drive else SEP + out


def read_env(path: Path, environ: Dict[str, str]) -> Dict[str, str]:
    """Bash `[ -f env ] && . env`: the environment's values, then the file's assignments over them."""
    env = {k: environ[k] for k in ENV_KEYS if environ.get(k)}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        try:
            parts = shlex.split(value, posix=True)
        except ValueError:
            continue
        env[key] = parts[0] if parts else ""
    return env


class Roots(NamedTuple):
    game: str
    config: str


def _add(acc: List[str], value: str) -> None:
    if not value:
        return
    rx = path_to_regex(value)
    if rx and rx not in acc:
        acc.append(rx)


def roots(root_posix: str, root_win: str, env: Dict[str, str]) -> Roots:
    game: List[str] = []
    _add(game, root_posix)
    _add(game, root_win)
    _add(game, env.get("SKYRIM_GAME_ROOT", ""))
    config = [CONFIG_LITERAL]
    _add(config, env.get("SKYRIM_CONFIG_DIR", ""))
    return Roots("|".join(game), "|".join(config))
