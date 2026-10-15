"""Version 1 host-neutral requests/results. Policy and evidence are independent."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Action:
    host: str
    tool: str
    surface: str
    cwd: str = ""
    shell: str = ""
    host_version: str = "unknown"
    session_id: str = ""
    tool_call_id: str = ""
    command: str = ""
    paths: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    api_version: int = 1


@dataclass(frozen=True)
class Result:
    decision: str = "allow"
    evaluation: str = "unguarded"
    preservation: str = "not_required"
    validation: str = "not_run"
    reason: str = ""
    warnings: tuple[str, ...] = ()
    api_version: int = 1

    def __post_init__(self):
        if self.decision not in ("allow", "deny", "ask"):
            raise ValueError("invalid decision")
        if self.evaluation not in ("evaluated", "partial", "failed", "unguarded"):
            raise ValueError("invalid evaluation")

    def as_dict(self):
        return asdict(self)


def unknown_tool(action):
    return Result(reason="unknown tool: {}".format(action.tool),
                  warnings=("WARN AND ALLOW: this tool surface was not evaluated",))
