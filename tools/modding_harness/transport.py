"""Version 1 payload normalization. Interpretation is not proof of interception.

Tool names/shapes follow the checked-in Codex 0.160.0 captures and OpenCode V1
1.18.34 source contracts. Callers must qualify their actual host/version. The
original payload is provenance, never approval evidence and never telemetry.
"""
from __future__ import annotations

import json
import ntpath
import posixpath
import re
import sys

from . import patches
from .contracts import Action


class BadPayload(ValueError):
    pass


UNKNOWN_EXECUTION_CWD = (
    "GUARD INERT: Codex supplied a session directory but no absolute execution workdir. "
    "This recognized mutation has an unresolved target. Use absolute mutation paths; "
    "an in-command directory change cannot prove the missing workdir. This call was not allowed.")


def execution_path_absolute(path, *, platform=sys.platform):
    """An execution path independent of an omitted cwd and Windows drive.

    Windows /name and /c/name are not drive-qualified native paths. Do not
    interpret them as MSYS mounts when deciding PowerShell or native patches.
    """
    if not isinstance(path, str) or not path.strip():
        return False
    if platform.startswith("win"):
        return bool(ntpath.splitdrive(path)[0]) and ntpath.isabs(path)
    return posixpath.isabs(path)


def execution_cwd_known(host, payload, *, platform=sys.platform):
    """A native Codex shell's top-level cwd is session context, not tool workdir.

    Measured on 0.162.1: exec_command(workdir=...) becomes Bash {command} in
    PreToolUse, losing workdir. No inferred directory may authorize a mutation.
    Other transports retain their existing contract. This is a restriction,
    never proof that a host intercepted or actually executed a call.
    """
    if host != "codex":
        return True
    ti = payload.get("tool_input", {})
    wd = ti.get("workdir") if isinstance(ti, dict) else None
    return execution_path_absolute(wd, platform=platform)


def _text(data, key, default=""):
    value = data.get(key, default)
    if not isinstance(value, str):
        raise BadPayload(key + " must be text")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BadPayload("duplicate payload field")
        result[key] = value
    return result


def load(raw):
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024 * 1024:
            raise BadPayload("payload must be UTF-8 JSON within 1 MiB")
        payload = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(payload, dict):
            raise BadPayload("payload must be an object")
        return payload
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise BadPayload("payload is not a bounded UTF-8 JSON object") from None


def absolute(path, cwd, platform):
    mod = ntpath if platform.startswith("win") else posixpath
    if mod is ntpath:
        path = re.sub(r"^/([A-Za-z])(?=/|$)", lambda m: m[1].upper() + ":", path)
    if not mod.isabs(path):
        if not cwd or not mod.isabs(cwd):
            raise BadPayload("relative path has no absolute cwd")
        path = mod.join(cwd, path)
    return mod.normpath(path).replace("\\", "/") if mod is ntpath else mod.normpath(path)


def normalize(host, payload, *, platform=sys.platform, default_shell="", shell_patch=None):
    if host not in ("claude", "codex", "opencode"):
        raise BadPayload("use the version 1 generic Action contract for an unspecified host")
    data = load(payload) if isinstance(payload, (str, bytes)) else payload
    if not isinstance(data, dict):
        raise BadPayload("payload must be an object")
    opencode = host == "opencode"
    if opencode and (type(data.get("v")) is not int or data["v"] != 1):
        raise BadPayload("OpenCode transport must be V1")
    tool = _text(data, "tool" if opencode else "tool_name")
    cwd = _text(data, "directory" if opencode else "cwd")
    ti = data.get("args" if opencode else "tool_input", {})
    common = dict(host=host, tool=tool, surface="unknown", cwd=cwd,
                  host_version=_text(data, "host_version", "unknown"),
                  session_id=_text(data, "session_id"),
                  tool_call_id=_text(data, "tool_use_id", _text(data, "call_id")),
                  provenance={"raw_payload": data, "normalization": "source/capture contract; host enforcement unmeasured"})
    if not opencode and data.get("hook_event_name") == "SessionStart":
        common["surface"] = "session"
        return Action(**common)
    if tool.startswith("mcp__"):
        common["surface"] = "mcp"
        return Action(**common)
    shell_tools = {"bash"} if opencode else {"Bash", "PowerShell", "Monitor", "exec_command", "shell_command"}
    file_tools = {"edit", "write"} if opencode else {"Edit", "Write", "NotebookEdit"}
    recognized = tool in shell_tools | file_tools | {"apply_patch", "write_stdin"}
    if not recognized:
        return Action(**common)
    if not isinstance(ti, dict):
        raise BadPayload("known tool arguments must be an object")
    if not cwd or not (ntpath if platform.startswith("win") else posixpath).isabs(cwd):
        raise BadPayload("known tool has no absolute cwd")
    wd = ti.get("workdir")
    if wd is not None:
        if not isinstance(wd, str) or not wd:
            raise BadPayload("workdir must be nonempty text")
        cwd = absolute(wd, cwd, platform)
        common["cwd"] = cwd
    if tool in file_tools:
        key = "filePath" if opencode else "notebook_path" if tool == "NotebookEdit" else "file_path"
        path = _text(ti, key)
        if not path:
            raise BadPayload("file tool has no affected path")
        common.update(surface="files", paths=(absolute(path, cwd, platform),), operations=("write",))
        return Action(**common)
    if tool == "apply_patch":
        keys = [key for key in (("patchText",) if opencode else ("command", "input")) if key in ti]
        if len(keys) != 1:
            raise BadPayload("patch must have one supported text field")
        text = _text(ti, keys[0])
        parser = patches.parse_patch_opencode if opencode else patches.parse_patch
        ops = parser(text)
        common.update(surface="files", command=text,
                      paths=tuple(absolute(path, cwd, platform) for _op, path in ops),
                      operations=tuple("move" if op == "move_from" else "delete" if op == "delete" else "write"
                                       for op, _path in ops))
        return Action(**common)
    if tool == "write_stdin":
        common.update(surface="stdin", command=_text(ti, "chars"))
        common["provenance"]["coverage"] = "no qualified fresh pre-tool interception for stdin continuations"
        return Action(**common)
    keys = [key for key in ("command", "cmd", "script") if key in ti]
    if len(keys) != 1:
        raise BadPayload("shell tool must have exactly one command field")
    command = _text(ti, keys[0])
    if not command.strip():
        raise BadPayload("shell command is empty")
    shell = _text(data, "shell", default_shell) if opencode else _text(ti, "shell", default_shell)
    if tool == "PowerShell":
        shell = "powershell"
    elif host == "claude" and tool == "Bash":
        shell = "bash"
    if shell not in ("bash", "powershell") and tool != "Monitor":
        raise BadPayload("the actual shell must be declared as bash or powershell")
    common.update(surface="shell", shell=shell, command=command)
    common["provenance"]["execution_cwd_known"] = execution_cwd_known(host, data, platform=platform)
    if shell_patch is not None:
        found = shell_patch(command)
        if found is not None:
            body, cd = found
            base = absolute(cd, cwd, platform) if cd else cwd
            ops = patches.parse_patch(body)  # Shell applier is Codex's; native OpenCode uses its own grammar.
            common.update(paths=tuple(absolute(path, base, platform) for _op, path in ops),
                          operations=tuple("move" if op == "move_from" else "delete" if op == "delete" else "write"
                                           for op, _path in ops))
    return Action(**common)
