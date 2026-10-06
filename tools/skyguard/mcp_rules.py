r"""The DevBench guard (Plan 5 Task 11): the MCP route and the curl / CLI route to the SAME two tools.

DevBench drives the RUNNING game. Three ways reach it, and every one lands on a tool call `tool(args)`:
  - MCP         `mcp__devbench-vr__console` with tool_input = the args (protect-mcp, decide());
  - curl        `curl ... http://127.0.0.1:8921/api/tool/<tool> -d '<json args>'` (also `--data*`, `--json`, and
                PowerShell Invoke-RestMethod / Invoke-WebRequest `-Body`);
  - the CLI     `bash tools/devbench-cli.sh exec "<console command>"` and `... tool <name> '<json args>'`.
judge() decides a tool call once, so the three routes cannot disagree; scan_shell() finds the calls in a parsed shell
command (protect-bash) and decide() reads the MCP payload (protect-mcp).

S-DEVBENCH-CRASH (deny). The `console` tool is given a command on the deny list, DEVBENCH_DENY (rules.py -- data, not
code). Today that is `smp reset` with NO argument, the only command MEASURED to crash this install (R14, P11): a full
reset of a 700+ plugin order. Compared on whitespace-split lower-case words, so `SMP  RESET` is the same command;
`smp reset <anything>` is a different (unmeasured) command and passes. A console line may hold several commands
(`;` or a newline): each is judged.

S-DEVBENCH-SAVE (ask). The `game` tool is told to save (`action: save`, `name`), or the console is given `save <name>`
(DevBench routes a console save through the same path), and `<config dir>/Saves/<name>.ess` EXISTS: the save would be
overwritten (recovery.py snapshots the old one first, Plan 6 Task 15). Asks only for an existing save; a NEW name passes. This is the only
filesystem read in the rules. When the save folder cannot be found (no declared config dir has a Saves folder), or the
name is missing or is not a plain file name, the answer is not knowable and the rule ASKS (not safe by default).

KNOWN LIMITS (recorded in the pricing ledger, not handled):
  - `curl ... -d @body.json`, `--data-binary @-` and a body held in a variable that is not a same-command literal:
    the body is unreadable offline, so the call is allowed;
  - interpreter code (python requests, node fetch) that posts to the console is not read. A first version denied code
    that held both the endpoint and `smp reset`; priced over history it hit 3 items, all scripts written to TEST this
    rule (strings, not calls), and none a real call, so it was dropped (approval fatigue outweighs an unseen shape);
  - the `papyrus` tool: `Game.SaveGame` and the ExecuteCommand-style functions in _CONSOLE_FUNCTIONS are read
    (rt-fix-b); any other function that saves or runs a console line is not, and console `bat` files can do either
    thing without being named here;
  - `replay` (and any tool that plays back a stored recording or input script): the recording's contents are not in
    the call, so the console lines or saves it replays are not judged; a deny-listed line is caught only when it is
    written into the call itself (a scenario step is, since its steps travel in the call);
  - a URL held in a variable that is not a same-command literal.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
from typing import Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from . import deletes, paths, rules
from .verdict import Verdict

HOOK = "protect-mcp"

# The endpoint any HTTP route uses: `/api/tool/<tool>`, host and port irrelevant (the port moves when 8921 is taken).
_ENDPOINT = re.compile(r"/api/tool/([A-Za-z_]+)/?(?:[?#].*)?$")
_MCP_ENDPOINT = re.compile(r"^https?://[^/\s]+/mcp/?(?:[?#].*)?$", re.IGNORECASE)
_CURL_BODY_FLAGS = ("--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json")
_WEB_PROGRAMS = frozenset(("curl", "wget", "invoke-restmethod", "irm", "invoke-webrequest", "iwr"))
_PS_PROGRAMS = frozenset(("invoke-restmethod", "irm", "invoke-webrequest", "iwr"))
_SPLIT_STATEMENTS = re.compile(r"[;\n\r]")
# The shell-route cheap prefilter: every form names one of these, or both "api" and "tool" (final review B-M4: a dot
# segment or an empty one splits `api/tool`, `/api/./tool/console`).
_SHELL_WORDS = ("api/tool", "devbench", "/mcp")
# A PowerShell .NET web call (shellparse does not model method calls): its body is text only.
_DOTNET_WEB = re.compile(r"\.(?:Upload(?:String|Data|Values)(?:Async)?|(?:Post|Send|Put)Async|GetResponse)\s*\(",
                         re.IGNORECASE)
_TEXT_RESET = re.compile(r"\bsmp\s+reset\b", re.IGNORECASE)
_TEXT_ENDPOINT = re.compile(r"/api/tool/console", re.IGNORECASE)
_KEY_TEXT = r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"'
# `smp reset` with nothing after it, as it reads in a body whose structure the guard cannot parse: it is followed by a
# quote (plain, escaped or doubled), a bracket, a separator or the end.
_TEXT_BARE_RESET = re.compile(r"""\bsmp\s+reset\s*(?:["'`)}\\\];,&|<>]|$)""", re.IGNORECASE)
_BAD_NAME = re.compile(r'[/\\:*?"<>|\x00-\x1f]')

Hit = Tuple[str, dict]            # (rule id, message fields)


# routes red team (rt-fix-b): what the console actually reads. A NUL ends the C string the game parses (`smp reset\0x`
# runs `smp reset`); zero-width and other format characters print as nothing; a statement wrapped whole in quotes is
# the same statement.
_INVISIBLE = re.compile("[\x01-\x08\x0b\x0c\x0e-\x1f\x7f­​-‏⁠-⁤﻿]")


def console_text(command: str) -> str:
    return _INVISIBLE.sub("", command.split("\x00", 1)[0])


def _statements(command: str):
    for stmt in _SPLIT_STATEMENTS.split(console_text(command)):
        s = stmt.strip()
        if len(s) > 1 and s[0] == s[-1] and s[0] in "\"'`":
            s = s[1:-1]
        yield s


def deny_op(command: str) -> str:
    """The deny-listed command this console line runs, in its canonical spelling, else ""."""
    for stmt in _statements(command):
        words = stmt.lower().split()
        for denied in rules.DEVBENCH_DENY:
            if tuple(words) == denied:
                return " ".join(denied)
    return ""


SAVE_COMMANDS = ("save", "savegame")


def _console_save_name(command: str) -> Optional[str]:
    """The name in a console `save <name>` (any statement), "" for a bare `save`, None when there is no save."""
    for stmt in _statements(command):
        words = stmt.split()
        if words and words[0].lower() in SAVE_COMMANDS:
            return " ".join(words[1:])
    return None


def _strings(obj, depth: int = 0):
    """Every string inside a tool's arguments (dict values, list items), depth-capped: a deny-listed console line
    anywhere in a call (a scenario step, a papyrus ExecuteCommand argument, a key the guard does not know) is the
    same command."""
    if depth > 32:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, depth + 1)


def _steps(args, depth: int = 0):
    """(tool, args) for each step of a scenario: `{tool|name, args|arguments}` and the `{console: "<line>"}` form."""
    if depth > 8 or not isinstance(args, dict):
        return
    for step in args.get("steps") if isinstance(args.get("steps"), list) else ():
        if not isinstance(step, dict):
            continue
        tool = step.get("tool") if isinstance(step.get("tool"), str) else step.get("name")
        sargs = step.get("args") if isinstance(step.get("args"), dict) else step.get("arguments")
        if isinstance(tool, str) and isinstance(sargs, dict):
            yield tool, sargs
            yield from _steps(sargs, depth + 1)
        if isinstance(step.get("console"), str):
            yield "console", {"command": step["console"]}
        if isinstance(step.get("command"), str):
            yield "console", {"command": step["command"]}
        if isinstance(step.get("action"), str):
            yield "game", step


def _clean_name(name) -> Optional[str]:
    """A save's file stem, or None when it is not a plain name (empty, a path, a reserved character)."""
    if not isinstance(name, str):
        return None
    n = name.strip().rstrip(" .")                 # Windows drops trailing dots and spaces: `Existing.` IS Existing
    if n.lower().endswith(".ess"):
        n = n[:-4].rstrip(" .")
    # `.` and `..` need no case of their own: the trailing-dot strip above leaves them empty.
    if not n or _BAD_NAME.search(n) or re.search(r"~[0-9]", n):
        return None                               # an 8.3 alias (EXISTI~1) names a file only the filesystem knows
    return n


def _documents(env) -> list:
    """Where `Documents` may be: under USERPROFILE / HOME, and a OneDrive-redirected Documents (OneDrive,
    OneDriveConsumer, OneDriveCommercial). Environment only: nothing here asks the OS."""
    out = []
    for key in ("USERPROFILE", "HOME", "OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        base = env.get(key, "")
        if base:                                              # an empty value would make `Documents` cwd-relative
            d = os.path.join(base, "Documents")
            if d not in out:
                out.append(d)
    return out


def saves_folder(roots, env) -> Optional[str]:
    """The first existing Saves folder: each declared config dir, then <Documents>/My Games/<game>/ for every game
    folder name (VR, SE, base). None when there is none."""
    cands = [os.path.join(d, "Saves") for d in roots.config_dirs]
    for docs in _documents(env):
        cands += [os.path.join(docs, "My Games", g, "Saves") for g in paths.GAME_FOLDERS]
    return next((c for c in cands if os.path.isdir(c)), None)


def save_exists(name: str, roots, env=None, memo: Optional[dict] = None) -> Optional[bool]:
    """True when the first existing Saves folder holds `<name>.ess` (case-insensitive, as Windows); False when it
    does not; None when there is no Saves folder to read. `memo` keeps the folder and its listing for one scan (a
    Saves folder holds thousands of files)."""
    memo = {} if memo is None else memo
    folder = saves_folder(roots, os.environ if env is None else env)
    if folder is None:
        return None
    if folder not in memo:
        try:
            memo[folder] = frozenset(n.casefold() for n in os.listdir(folder))
        except OSError:
            memo[folder] = None
    if memo[folder] is None:
        return None
    return (name + ".ess").casefold() in memo[folder]


def _save_hit(name, roots, env=None, memo=None) -> Hit:
    """The ask for a save named `name` (raw, may be unusable), or an empty hit when it is a new save."""
    clean = _clean_name(name)
    if clean is None:
        return ("S-DEVBENCH-SAVE", {"op": "Cannot tell which save this overwrites."})
    exists = save_exists(clean, roots, env, memo)
    if exists is False:
        return ("", {})
    if exists is None:
        return ("S-DEVBENCH-SAVE", {"op": f"Cannot tell if save {clean} exists."})
    return ("S-DEVBENCH-SAVE", {"op": f"Overwrites save {clean}."})


_CONSOLE_FUNCTIONS = ("executecommand", "executeconsole", "executeconsolecommand", "runconsolecommand")


def judge(tool: str, args, roots, memo: Optional[dict] = None, env=None, depth: int = 0) -> Optional[Hit]:
    """The decision for one DevBench tool call, as (rule id, fields), or None for allow. The first deny wins over
    any ask. Any string anywhere in the call that the console would run as a deny-listed command denies (rt-fix-b:
    a list or an unknown key, a scenario step, a papyrus ExecuteCommand argument)."""
    tool = tool.lower()
    for s in _strings(args):
        op = deny_op(s)
        if op:
            return ("S-DEVBENCH-CRASH", {"op": op})
    if not isinstance(args, dict):
        return None
    ask: Optional[Hit] = None
    lines = []                                    # console lines this call runs
    if tool == "console":
        cmd = args.get("command")
        lines = list(_strings(cmd)) if cmd is not None else list(_strings(args))
    elif tool == "papyrus" and isinstance(args.get("function"), str):
        fn = args["function"].lower()
        if fn in _CONSOLE_FUNCTIONS:
            lines = list(_strings(args.get("args")))
        elif fn == "savegame":                    # Game.SaveGame(name) writes the same file a console save does
            a = args.get("args")
            hit = _save_hit(a[0] if isinstance(a, list) and a else None, roots, env, memo)
            ask = hit if hit[0] else None
    elif tool == "game":
        action = args.get("action")
        if isinstance(action, str) and action.strip().lower() == "save":
            hit = _save_hit(args.get("name"), roots, env, memo)
            ask = hit if hit[0] else None
    for line in lines:
        name = _console_save_name(line)
        if name is not None and ask is None:
            hit = _save_hit(name, roots, env, memo)
            ask = hit if hit[0] else None
    if depth < 8:
        for stool, sargs in _steps(args):
            hit = judge(stool, sargs, roots, memo, env, depth + 1)
            if hit and hit[0] == "S-DEVBENCH-CRASH":
                return hit
            ask = ask or hit
    return ask


def save_name(tool: str, args) -> Optional[str]:
    """The raw name a DevBench call saves under ("" when it names none), or None when the call saves nothing -- read as
    judge() reads it. recovery.py snapshots that save before the call (Plan 6 Task 15); tests hold the two in step."""
    if not isinstance(args, dict):
        return None
    t = tool.lower()
    if t == "console":
        c = args.get("command")
        return _console_save_name(c) if isinstance(c, str) else None
    if t == "game":
        act = args.get("action")
        if isinstance(act, str) and act.strip().lower() == "save":
            n = args.get("name")
            return n if isinstance(n, str) else ""
    return None


def shell_save_names(command: str, parsed) -> list:
    """The raw save names of the DevBench calls a shell command makes (curl, Invoke-RestMethod, devbench-cli), found as
    scan_shell finds the calls. Opaque or unparsed commands yield nothing (no name can be read from them)."""
    if not any(w in command.lower() for w in _SHELL_WORDS):
        return []
    names = []
    for raw in parsed.execs:
        if raw.program == "?":
            continue
        ex = deletes.substituted(raw)
        route = _web_call if ex.program in _WEB_PROGRAMS else (_cli_call if ex.program == "devbench-cli" else None)
        for tool, args in (route(ex, command) if route else []):
            n = save_name(tool, args)
            if n is not None:
                names.append(n)
    return names


# ------------------------------------------------------------------------------------------------------ MCP route
def is_devbench_server(server: str) -> bool:
    """A DevBench MCP server by any spelling of its configured name: `devbench-vr`, `DevBench-vr`, `dev-bench`,
    `skyrim-devbench`, a plugin-installed `plugin_devbench_x` (rt-fix-b, routes s106-s111). Case- and
    separator-insensitive."""
    return "devbench" in re.sub(r"[-_.\s]", "", server.lower())


def is_devbench(name: str) -> bool:
    """True for an `mcp__<server>__<tool>` call whose server is a DevBench one (is_devbench_server). Plan 5 Task 17:
    the Codex engine uses it to keep a judged DevBench call out of the unhooked-tools log."""
    parts = name.split("__") if isinstance(name, str) else []
    return len(parts) >= 3 and parts[0] == "mcp" and is_devbench_server(parts[1])


def judged_server(name: str) -> bool:
    """True when `name` is an `mcp__<server>__<tool>` call the MCP rules judge (a DevBench server, by Plan 5's
    is_devbench). Plan 7: the neutral entry labels every other MCP call NOT JUDGED rather than evaluated."""
    return is_devbench(name)


def decide(name: str, args, audit, roots=None, env=None) -> Verdict:
    """protect-mcp: the verdict for one `mcp__<server>__<tool>` call. Only DevBench servers are judged here."""
    if name == "" or name == "unknown":
        audit.rule(HOOK, "S-INERT-NOINPUT", "deny", "no tool call in payload -- the DevBench guard checked nothing",
                   level="REFUSE")
        return Verdict("deny", rules.message("S-INERT-NOINPUT"))
    parts = name.split("__")
    if len(parts) < 3 or parts[0] != "mcp" or not is_devbench_server(parts[1]):
        return Verdict()
    hit = judge(parts[-1], args, roots, None, env)
    if hit is None:
        return Verdict()
    rid, fields = hit
    reason = rules.message(rid, **fields)
    audit.rule(HOOK, rid, rules.RULES[rid]["decision"], reason)
    return Verdict(rules.RULES[rid]["decision"], reason)


# ----------------------------------------------------------------------------------------------- curl / CLI route
def _args_from_body(body: str) -> Optional[dict]:
    """The tool's args from a request body: JSON, else the few keys that matter pulled out of damaged JSON."""
    try:
        d = json.loads(body)
        return d if isinstance(d, dict) else None
    except ValueError:
        pass
    out = {}
    for key in ("action", "command", "name"):
        m = re.search(_KEY_TEXT % key, body)
        if m:
            try:
                out[key] = json.loads('"' + m.group(1) + '"')
            except ValueError:
                out[key] = m.group(1)
    return out or None


def _curl_bodies(argv) -> list:
    out, k = [], 0
    while k < len(argv):
        w = argv[k]
        if w in _CURL_BODY_FLAGS or re.fullmatch(r"-[A-Za-z]*d", w):
            if k + 1 < len(argv):
                out.append(argv[k + 1])
            k += 2
            continue
        if w.startswith("-d") and len(w) > 2 and not w.startswith("--"):
            out.append(w[2:])
        k += 1
    return out


def _norm_url(word: str) -> str:
    """The word as the server sees the URL (final review B-M4): percent-decoded (rt-fix-b: /api/tool/%63onsole), with
    the path's dot segments and empty segments squashed, as curl squashes them before sending (`/api/./tool/console`,
    `/api/x/../tool/console`, `/api/%2e/tool/console`, `/api//tool/console`). Linear: posixpath.normpath."""
    u = unquote(word)
    if "/" not in u:
        return u
    try:
        parts = urlsplit(u)
    except ValueError:
        return u
    if not parts.path:
        return u
    path = posixpath.normpath(parts.path)
    if path.startswith("//"):
        path = path[1:]                                    # normpath keeps a POSIX leading `//`
    if parts.path.endswith("/") and not path.endswith("/"):
        path += "/"
    return urlunsplit(parts._replace(path=path))


def _norm_text(text: str) -> str:
    """Every URL-like token of the text normalised by _norm_url (the text fallback's endpoint test)."""
    return " ".join(_norm_url(t) for t in re.split(r"[\s'\"`]+", text) if t)


def _web_call(e, text: str) -> list:
    """(tool, args) pairs for each DevBench REST call this curl / Invoke-RestMethod exec makes; unreadable bodies
    (`@file`, a variable) yield nothing."""
    tool, query, rpc = "", {}, False
    for w in e.argv:
        u = _norm_url(w)                                      # rt-fix-b %63onsole; B-M4 dot / empty segments
        m = _ENDPOINT.search(u)
        if m:
            tool = m.group(1)
            q = urlsplit(u).query
            query = {k: v[-1] for k, v in parse_qs(q).items()} if q else {}
            break
        if _MCP_ENDPOINT.search(u):
            tool, rpc = "mcp", True                           # DevBench's MCP-over-HTTP: JSON-RPC tools/call
            break
    if not tool:
        return []
    if e.program in _PS_PROGRAMS:
        from .shellparse import ps_param
        bodies = list(ps_param(e, "body"))
    elif e.program == "wget":
        bodies = [w.split("=", 1)[1] for w in e.argv if w.startswith(("--post-data=", "--body-data="))]
        bodies += [e.argv[i + 1] for i, w in enumerate(e.argv[:-1]) if w in ("--post-data", "--body-data")]
    else:
        bodies = _curl_bodies(e.argv)
    out = [(tool, query)] if query else []
    for b in bodies:
        args = _args_from_body(b)                             # `@file` is no JSON, so a body file yields None
        if rpc and isinstance(args, dict):
            params = args.get("params") if isinstance(args.get("params"), dict) else {}
            name = params.get("name") if isinstance(params.get("name"), str) else "mcp"
            inner = params.get("arguments")
            out.append((name, inner if isinstance(inner, dict) else args))
            continue
        if args is not None and (tool.lower() != "console" or isinstance(args.get("command"), str)):
            out.append((tool, args))
        elif tool.lower() == "console" and _unreadable_names_reset(text):
            out.append(("console", {"command": "smp reset"}))
    return out


def _unreadable_names_reset(text) -> bool:
    """A console call whose body gave no definite command (`-d command=smp reset`, `-Body (@{...} | ConvertTo-Json)`,
    `-Body $b`, a PowerShell-escaped `\\"` body) but whose command text (the WHOLE command: the body is often built in an earlier statement) names `smp reset` bare: the same as the opaque
    branch, it denies. A body file or a variable that does not name it stays allowed (the ledgered gap)."""
    return bool(_TEXT_BARE_RESET.search(text))


def _cli_call(e, text: str) -> list:
    argv = e.argv
    if not argv:
        return []
    if argv[0] == "exec" and len(argv) > 1:
        return [("console", {"action": "exec", "command": " ".join(argv[1:])})]
    if argv[0] == "call" and len(argv) > 2:                  # call <Script> <Function> [argsJson]: the papyrus tool
        try:
            a = json.loads(argv[3]) if len(argv) > 3 else []
        except ValueError:
            a = [argv[3]]
        return [("papyrus", {"action": "call", "script": argv[1], "function": argv[2], "args": a})]
    if argv[0] == "tool" and len(argv) > 2:
        args = _args_from_body(argv[2])
        if args is not None and (argv[1].lower() != "console" or isinstance(args.get("command"), str)):
            return [(argv[1], args)]
        if argv[1].lower() == "console" and _unreadable_names_reset(text):
            return [("console", {"command": "smp reset"})]
        return []
    return []


def scan_shell(command: str, parsed, roots, env=None) -> Optional[Hit]:
    """The first deny, else the first ask, among the DevBench calls a shell command makes. Not safe by default
    (R4): where the parse failed or a program is opaque, a deny-listed command next to the console endpoint in the
    TEXT denies. Interpreter code is NOT read (see KNOWN LIMITS)."""
    low = unquote(command).lower()
    if not any(w in low for w in _SHELL_WORDS) and not ("api" in low and "tool" in low):
        return None
    ask: Optional[Hit] = None
    opaque = False
    memo: dict = {}
    for e in parsed.execs:
        if e.program == "?":
            opaque = True
            continue
        e = deletes.substituted(e)
        calls = []
        if e.program in _WEB_PROGRAMS:
            calls = _web_call(e, command)
        elif e.program == "devbench-cli":
            calls = _cli_call(e, command)
        for tool, args in calls:
            hit = judge(tool, args, roots, memo, env)
            if hit is None:
                continue
            if hit[0] == "S-DEVBENCH-CRASH":
                return hit
            ask = ask or hit
    textual = not parsed.ok or opaque or bool(_DOTNET_WEB.search(command))
    if textual and _TEXT_RESET.search(command) and _TEXT_ENDPOINT.search(_norm_text(command)):
        return ("S-DEVBENCH-CRASH", {"op": "smp reset"})
    return ask
