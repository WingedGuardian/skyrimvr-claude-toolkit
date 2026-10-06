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
overwritten and the old one is not kept. Asks only for an existing save; a NEW name passes. This is the only
filesystem read in the rules. When the save folder cannot be found (no declared config dir has a Saves folder), or the
name is missing or is not a plain file name, the answer is not knowable and the rule ASKS (not safe by default).

KNOWN LIMITS (recorded in the pricing ledger, not handled):
  - `curl ... -d @body.json`, `--data-binary @-` and a body held in a variable that is not a same-command literal:
    the body is unreadable offline, so the call is allowed;
  - interpreter code (python requests, node fetch) that posts to the console is not read. A first version denied code
    that held both the endpoint and `smp reset`; priced over history it hit 3 items, all scripts written to TEST this
    rule (strings, not calls), and none a real call, so it was dropped (approval fatigue outweighs an unseen shape);
  - the `papyrus` tool (`Game.SaveGame`, `Debug.ExecuteConsole`-style calls) and console `bat` files can do either
    thing without being named here;
  - DevBench's own MCP-over-HTTP endpoint (`/mcp`), and a URL held in a variable that is not a same-command literal.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional, Tuple

from . import deletes, paths, rules
from .verdict import Verdict

HOOK = "protect-mcp"

# The endpoint any HTTP route uses: `/api/tool/<tool>`, host and port irrelevant (the port moves when 8921 is taken).
_ENDPOINT = re.compile(r"/api/tool/([A-Za-z_]+)/?(?:[?#].*)?$")
_CURL_BODY_FLAGS = ("--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json")
_WEB_PROGRAMS = frozenset(("curl", "invoke-restmethod", "irm", "invoke-webrequest", "iwr"))
_PS_PROGRAMS = frozenset(("invoke-restmethod", "irm", "invoke-webrequest", "iwr"))
_SPLIT_STATEMENTS = re.compile(r"[;\n\r]")
# The shell-route cheap prefilter: every form names one of these.
_SHELL_WORDS = ("api/tool", "devbench")
_TEXT_RESET = re.compile(r"\bsmp\s+reset\b", re.IGNORECASE)
_TEXT_ENDPOINT = re.compile(r"/api/tool/console", re.IGNORECASE)
_KEY_TEXT = r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"'
# `smp reset` with nothing after it, as it reads in a body whose structure the guard cannot parse: it is followed by a
# quote (plain, escaped or doubled), a bracket, a separator or the end.
_TEXT_BARE_RESET = re.compile(r"""\bsmp\s+reset\s*(?:["'`)}\\\];,&|<>]|$)""", re.IGNORECASE)
_BAD_NAME = re.compile(r'[/\\:*?"<>|\x00-\x1f]')

Hit = Tuple[str, dict]            # (rule id, message fields)


def deny_op(command: str) -> str:
    """The deny-listed command this console line runs, in its canonical spelling, else ""."""
    for stmt in _SPLIT_STATEMENTS.split(command):
        words = stmt.lower().split()
        for denied in rules.DEVBENCH_DENY:
            if tuple(words) == denied:
                return " ".join(denied)
    return ""


def _console_save_name(command: str) -> Optional[str]:
    """The name in a console `save <name>` (any statement), "" for a bare `save`, None when there is no save."""
    for stmt in _SPLIT_STATEMENTS.split(command):
        words = stmt.split()
        if words and words[0].lower() == "save":
            return " ".join(words[1:])
    return None


def _clean_name(name) -> Optional[str]:
    """A save's file stem, or None when it is not a plain name (empty, a path, a reserved character)."""
    if not isinstance(name, str):
        return None
    n = name.strip()
    if n.lower().endswith(".ess"):
        n = n[:-4]
    if not n or n in (".", "..") or _BAD_NAME.search(n):
        return None
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


def judge(tool: str, args: dict, roots, memo: Optional[dict] = None, env=None) -> Optional[Hit]:
    """The decision for one DevBench tool call (args: a dict), as (rule id, fields), or None for allow."""
    tool = tool.lower()
    if tool == "console":
        cmd = args.get("command")
        if not isinstance(cmd, str):
            return None
        op = deny_op(cmd)
        if op:
            return ("S-DEVBENCH-CRASH", {"op": op})
        name = _console_save_name(cmd)
        if name is not None:
            hit = _save_hit(name, roots, env, memo)
            return hit if hit[0] else None
    elif tool == "game":
        action = args.get("action")
        if isinstance(action, str) and action.strip().lower() == "save":
            hit = _save_hit(args.get("name"), roots, env, memo)
            return hit if hit[0] else None
    return None


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
def decide(name: str, args, audit, roots=None, env=None) -> Verdict:
    """protect-mcp: the verdict for one `mcp__<server>__<tool>` call. Only DevBench servers are judged here."""
    if name == "" or name == "unknown":
        audit.rule(HOOK, "S-INERT-NOINPUT", "deny", "no tool call in payload -- the DevBench guard checked nothing",
                   level="REFUSE")
        return Verdict("deny", rules.message("S-INERT-NOINPUT"))
    parts = name.split("__")
    if len(parts) < 3 or parts[0] != "mcp" or not parts[1].startswith("devbench"):
        return Verdict()
    hit = judge(parts[-1], args, roots, None, env) if isinstance(args, dict) else None
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


def _web_call(e, text: str) -> list:
    """(tool, args) pairs for each DevBench REST call this curl / Invoke-RestMethod exec makes; unreadable bodies
    (`@file`, a variable) yield nothing."""
    tool = ""
    for w in e.argv:
        m = _ENDPOINT.search(w)
        if m:
            tool = m.group(1)
            break
    if not tool:
        return []
    if e.program in _PS_PROGRAMS:
        from .shellparse import ps_param
        bodies = list(ps_param(e, "body"))
    else:
        bodies = _curl_bodies(e.argv)
    out = []
    for b in bodies:
        args = _args_from_body(b)                             # `@file` is no JSON, so a body file yields None
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
    low = command.lower()
    if not any(w in low for w in _SHELL_WORDS):
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
    if (not parsed.ok or opaque) and _TEXT_RESET.search(command) and _TEXT_ENDPOINT.search(command):
        return ("S-DEVBENCH-CRASH", {"op": "smp reset"})
    return ask
