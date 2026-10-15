"""Restrictive installed save-policy bridge, evaluated before permanent backup.

Registration binds exact source bytes. Only the legacy guard's pure ``decide``
entry is called; its backup/approval ``main`` never runs. It cannot grant a
permission or replace the portable profile's save scope and approval checks.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modding_harness import contracts, transport

CONFIG = ".modding-agent-harness/local-policies.json"
LEGACY = ".claude/hooks/protect-save-access.py"
KIND = "legacy-skyrim-save-guard-v1"


def registration(project):
    root = Path(project).resolve()
    path = root / CONFIG
    legacy = root / LEGACY
    if not path.exists():
        if legacy.exists():
            raise ValueError("installed save policy needs a hash-bound local-policies.json registration")
        return None
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("local policy registration exceeds 64 KiB")
    data = transport.load(raw)
    if (type(data.get("v")) is not int or data["v"] != 1 or data.get("kind") != KIND or
            data.get("path") != LEGACY or set(data) != {"v", "kind", "path", "sha256"}):
        raise ValueError("unsupported local policy registration")
    if not isinstance(data["sha256"], str) or len(data["sha256"]) != 64:
        raise ValueError("local policy registration has no SHA-256")
    if legacy.is_symlink() or not legacy.resolve().is_relative_to(root) or not legacy.is_file():
        raise ValueError("registered local policy is not an installed regular file")
    with legacy.open("rb") as stream:
        source = stream.read(512 * 1024 + 1)
    if len(source) > 512 * 1024 or hashlib.sha256(source).hexdigest() != data["sha256"]:
        raise ValueError("registered local policy bytes changed or exceed the source budget")
    return legacy, source


def _payload(action, tool, args):
    return {"tool_name": tool, "tool_input": args, "cwd": action.cwd,
            "session_id": action.session_id}


def _runtime_action(tool, args, depth=0):
    from skyguard import mcp_rules
    if not isinstance(args, dict):
        raise ValueError("local DevBench policy requires structured arguments")
    tool = tool.casefold()
    if tool == "game" and str(args.get("action", "")).strip().casefold() in ("save", "load", "loadlast"):
        return True
    lines = []
    if tool == "console":
        lines = list(mcp_rules._strings(args.get("command", args)))
    if tool == "papyrus":
        function = str(args.get("function", "")).rsplit(".", 1)[-1].casefold()
        if function in ("savegame", "loadgame", "requestsave", "requestautosave", "quittomainmenu"):
            return True
        if function in mcp_rules._CONSOLE_FUNCTIONS:
            lines = list(mcp_rules._strings(args.get("args")))
    for line in lines:
        for statement in mcp_rules._statements(line):
            words = statement.split()
            if words and words[0].casefold() in ("save", "load", "savegame", "loadgame", "qsave", "qload"):
                return True
    if depth < 8:
        for nested_tool, nested_args in mcp_rules._steps(args):
            if _runtime_action(nested_tool, nested_args, depth + 1):
                return True
    return False


def evaluate(action, project):
    registered = registration(project)
    if registered is None:
        return contracts.Result(evaluation="evaluated")
    path, source = registered
    namespace = {"__file__": str(path), "__name__": "_registered_skyrim_local_policy", "__package__": None}
    exec(compile(source, str(path), "exec"), namespace)
    decide = namespace.get("decide")
    if not callable(decide):
        raise ValueError("registered local policy has no pure decide entry")
    if action.surface == "files":
        payloads = [_payload(action, "Write", {"file_path": path})
                    for path, operation in zip(action.paths, action.operations) if operation != "read"]
    elif action.surface == "shell":
        payloads = [_payload(action, "PowerShell" if action.shell == "powershell" else "Bash",
                             {"command": action.command})]
    elif action.surface == "mcp":
        raw = action.provenance.get("raw_payload", {})
        args = action.provenance.get("arguments", raw.get("args", raw.get("tool_input", {})))
        from skyguard import mcp_rules
        if mcp_rules.judged_server(action.tool) and _runtime_action(action.tool.rsplit("__", 1)[-1], args):
            return contracts.Result("deny", "evaluated", reason=
                "Installed local policy: generic automated runtime save/load is refused; use only the qualified controlled-load route.")
        payloads = [_payload(action, action.tool, args)]
    else:
        payloads = []
    for payload in payloads:
        result = decide(payload)
        if not isinstance(result, dict) or result.get("kind") not in (
                "allow", "deny", "backup", "needs_files", "needs_set"):
            raise ValueError("registered policy returned an unsupported decision")
        if result["kind"] == "deny":
            return contracts.Result("deny", "evaluated", reason=str(result.get("reason") or "installed local policy refused"))
    # Save authorization and preservation remain the portable dispatcher's job.
    if registration(project)[1] != source:
        raise ValueError("registered policy changed during evaluation")
    return contracts.Result(evaluation="evaluated")


def restriction(project, deadline=None):
    def check(action):
        if action.surface not in ("files", "shell", "mcp") or (
                action.surface == "files" and not any(op != "read" for op in action.operations)):
            return contracts.Result(evaluation="evaluated")
        try:
            registered = registration(project)
            if registered is None:
                return contracts.Result(evaluation="evaluated")
            remaining = min(10, deadline - time.monotonic()) if deadline is not None else 10
            if remaining <= 0:
                raise TimeoutError("local policy budget expired")
            data = json.dumps(asdict(action), ensure_ascii=True).encode("ascii")
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("local policy input exceeds its byte budget")
            completed = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), str(project)],
                                       input=data, capture_output=True, timeout=remaining)
            if completed.returncode or len(completed.stdout) > 65536:
                raise ValueError("local policy worker did not return a bounded verdict")
            result = transport.load(completed.stdout)
            if registration(project) != registered:
                raise ValueError("local policy registration changed during evaluation")
            return contracts.Result(**result)
        except (OSError, ValueError, TypeError, TimeoutError, subprocess.TimeoutExpired) as exc:
            return contracts.Result("deny", "failed", reason="installed local policy failed: " + str(exc)
                                    if isinstance(exc, ValueError) else "installed local policy failed: " + type(exc).__name__)
    return check


if __name__ == "__main__":
    try:
        import contextlib
        class NoPolicyOutput:
            def write(self, text):
                if text:
                    raise ValueError("local decide entry must not print output")
                return 0
            def flush(self):
                pass
        data = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("worker input too large")
        action = contracts.Action(**json.loads(data))
        with contextlib.redirect_stdout(NoPolicyOutput()), contextlib.redirect_stderr(NoPolicyOutput()):
            result = evaluate(action, Path(sys.argv[1]))
        if len(result.reason.encode("utf-8")) > 8192:
            raise ValueError("local policy reason exceeds its byte budget")
        sys.stdout.buffer.write((json.dumps(result.as_dict(), ensure_ascii=True) + "\n").encode("ascii"))
    except Exception:
        raise SystemExit(2)
