"""The agent-neutral request (spec section 5 contract), built by an adapter from its agent's payload.

The spec's fields come first; the rest are what adapters already know and the engine needs (telemetry,
per-session coverage, the patch op per path). `failure` set = the adapter could not translate the payload;
a blocking role must then refuse (spec 5.1) -- it is never read as an empty, harmless request.
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Tuple


class Request(NamedTuple):
    v: int = 1
    agent: str = ""
    event: str = ""                   # pre_tool | session_start | other
    kind: str = ""                    # shell | write | mcp | other | none
    shell: Optional[str] = None       # bash | powershell | None
    command: str = ""
    paths: Tuple[str, ...] = ()
    ops: Tuple[str, ...] = ()         # parallel to paths: add|update|delete|move_from|move_to|write|edit
    mcp_tool: str = ""
    mcp_args: str = ""
    cwd: str = ""
    tool_name: str = ""
    session_id: str = ""
    transcript_path: str = ""
    source: str = ""
    empty: bool = False
    failure: str = ""
