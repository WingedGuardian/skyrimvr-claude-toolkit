#!/usr/bin/env python3
"""Record what the CURRENT hooks decide for a fixed set of inputs (spec §7.1).

The output, tests/guard_corpus/expected.jsonl, is the yardstick for the skyguard port: the new hooks
must reproduce every decision and every reason byte for byte. Rows are generated from:
  * every cmd()/fpath() payload in tests/test_hooks.py (literal calls AND parametrize rows), by AST;
  * every rule in the bash hooks, each with a positive and a negative input;
  * path dialects, multi-line, CRLF and non-ASCII variants (Plan 2 Review Focus);
  * the empty, invalid and non-object payloads.
Re-record only with --hooks pointing at the hooks whose behaviour is the reference.

tests/guard_corpus/intended.jsonl is the OTHER file: hand-authored expectations for the new behaviour. This
script never writes it; `--check-intended [--id X]` only fires its rows and prints actual vs intended.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BS = chr(92)
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
GAMEW = GAME.replace("/", BS)
LAYOUTS = {
    # name: (install dir name, skyrim-paths.env text)
    "declared": ("Skyrim VR", f'SKYRIM_GAME_ROOT="{GAME}"\n'),
    "undeclared": ("Skyrim VR", ""),
    "metachar": ("Skyrim (VR) [x]$", ""),
    "config": ("Skyrim VR", f'SKYRIM_GAME_ROOT="{GAME}"\nSKYRIM_CONFIG_DIR="D:/Profiles/MO2/profiles/Default"\n'),
}


def bash() -> str:
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(c).is_file():
            return c
    found = shutil.which("bash")
    if found and "System32" not in found:
        return found
    raise SystemExit("no non-WSL bash")


def configure(text: str) -> str:
    """Fill whichever install placeholder the hooks carry, the way setup.sh does."""
    jq = shutil.which("jq") or ""
    return (text.replace("{{JQ_PATH}}", jq.replace(BS, "/"))
                .replace("{{PYTHON_PATH}}", sys.executable.replace(BS, "/")))


def make_install(base: Path, hooks_src: Path, layout: str) -> Path:
    name, env = LAYOUTS[layout]
    root = base / layout / name
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "Data" / "Scripts" / "Source").mkdir(parents=True)
    for f in sorted(hooks_src.glob("*.sh")):
        (root / ".claude" / "hooks" / f.name).write_text(configure(f.read_text(encoding="utf-8")),
                                                         encoding="utf-8", newline="\n")
    if (REPO / "tools" / "skyguard").is_dir():
        shutil.copytree(REPO / "tools" / "skyguard", root / "tools" / "skyguard",
                        ignore=shutil.ignore_patterns("__pycache__"))
    if env:
        (root / ".claude" / "skyrim-paths.env").write_text(env, encoding="utf-8", newline="\n")
    return root


def install_relative(payload: dict, install: Path) -> dict:
    """Fill the `<install>` template of an install-relative row with this run's real install path."""
    return json.loads(json.dumps(payload).replace("<install>", install.as_posix()))


def needs_missing_jq(hooks_src: Path) -> bool:
    """The reference bash hooks need jq; a CI leg without it must skip, not record GUARD INERT rows."""
    uses_jq = any("{{JQ_PATH}}" in f.read_text(encoding="utf-8") for f in hooks_src.glob("*.sh"))
    return uses_jq and shutil.which("jq") is None


def classify(out: str) -> dict:
    # Windows jq renders an embedded newline of $COMMAND as CRLF in its output; Linux jq does not. The
    # recorded text must not depend on the recording OS, or the corpus fails on CI's Linux leg
    # (Task 3 fix-round ruling). Decisions are unaffected.
    out = out.strip()
    if not out:
        return {"decision": "allow", "text": ""}
    h = json.loads(out).get("hookSpecificOutput", {})
    if "permissionDecision" in h:
        return {"decision": h["permissionDecision"],
                "text": h.get("permissionDecisionReason", "").replace("\r\n", "\n")}
    if "additionalContext" in h:
        return {"decision": "advise", "text": h["additionalContext"].replace("\r\n", "\n")}
    return {"decision": "allow", "text": ""}


def fire(install: Path, hook: str, payload) -> dict:
    stdin = "" if payload is None else (payload if isinstance(payload, str) else json.dumps(payload))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(install))
    for k in ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR"):
        env.pop(k, None)
    # as_posix(): production runs `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/X.sh"`, i.e. a forward-slash
    # (or mixed) path; a backslash-only path hides defects in how the wrapper derives its own location.
    r = subprocess.run([bash(), (install / ".claude" / "hooks" / hook).as_posix()], input=stdin.encode("utf-8"),
                       capture_output=True, env=env, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"{hook} exited {r.returncode}: {r.stderr[:300]!r}")
    return classify(r.stdout.decode("utf-8"))


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def row_payload(row: dict, install: Path):
    """The payload to fire: install-relative rows (any `<install>` in the payload, `cwd` included) are filled in."""
    p = row["payload"]
    return install_relative(p, install) if row.get("payload_note") == "install-relative" else p


# ---- rule ids -------------------------------------------------------------------------------------------
# The wrapper prints only a message, and Plan 5 rewrites every message, so a verdict is compared by decision
# and RULE ID. A legacy row's rule ids come from its frozen text through legacy_rules.json; the engine's come
# from the rule trace (Audit.fired), read in-process because the wrapper does not print it.

def legacy_rule_ids(text: str, templates: dict) -> set:
    """Rule ids whose legacy template occurs in `text`: every literal chunk of the template, in order."""
    found = set()
    for template, rid in templates.items():
        chunks = [c for c in re.split(r"\{[a-z]+\}", template) if c]
        pos = 0
        for c in chunks:
            at = text.find(c, pos)
            if at < 0:
                break
            pos = at + len(c)
        else:
            found.add(rid)
    return found


def _engine_eval():
    spec = importlib.util.spec_from_file_location("engine_eval", REPO / "scripts" / "guard-corpus" / "engine_eval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def engine_verdict(install: Path, hook: str, payload) -> dict:
    """What the engine decides, with the rule ids it fired. Mirrors tools/skyguard/run.py without the wrapper."""
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from skyguard import paths, shell_rules, write_rules
    from skyguard.adapters import claude
    from skyguard.audit import Audit
    from skyguard.verdict import Verdict
    ee = _engine_eval()
    raw = "" if payload is None else (payload if isinstance(payload, str) else json.dumps(payload))
    p = claude.extract(raw)
    audit = ee.make_audit(Audit)
    root = install.as_posix()
    root_win = root if os.name == "nt" else ""
    cwd = payload.get("cwd", "") if isinstance(payload, dict) and isinstance(payload.get("cwd"), str) else ""
    if hook == "protect-bash.sh":
        if not p["command"]:
            v = ee.call(shell_rules.decide, ("", paths.Roots("", ""), audit), cwd=cwd)
        else:
            environ = {k: val for k, val in os.environ.items()
                       if k not in ("SKYRIM_GAME_ROOT", "SKYRIM_CONFIG_DIR", "SKYRIM_LOADORDER_DIR")}
            env = paths.read_env(Path(root_win or root) / ".claude" / "skyrim-paths.env", environ)
            v = ee.call(shell_rules.decide, (p["command"], paths.roots(root, root_win, env), audit), cwd=cwd)
    else:
        v = ee.call(write_rules.decide, (p["file_path"], audit), cwd=cwd)
    if v.decision in ("deny", "ask"):
        decision, text = v.decision, v.reason
    elif v.context:
        decision, text = "advise", v.context
    else:
        decision, text = "allow", ""
    return {"decision": decision, "text": text, "rules": {rid for rid, _ in audit.trace}}


def fire_full(install: Path, row: dict) -> dict:
    """The real wrapper's decision and text, plus the engine's rule ids (which must agree on the decision)."""
    payload = row_payload(row, install)
    got = fire(install, row["hook"], payload)
    eng = engine_verdict(install, row["hook"], payload)
    if eng["decision"] != got["decision"]:
        raise RuntimeError(f"{row['id']}: wrapper says {got['decision']} but the in-process engine says "
                           f"{eng['decision']}; the rule ids would describe a different verdict")
    return {**got, "rules": eng["rules"]}


def intended_problems(row: dict, got: dict) -> list[str]:
    """How an intended row differs from what the engine did. Decision, rule ids AND exact text."""
    out = []
    if got["decision"] != row["decision"]:
        out.append(f"decision: intended {row['decision']}, got {got['decision']}")
    if set(got["rules"]) != set(row["rules"]):
        out.append(f"rules: intended {sorted(row['rules'])}, got {sorted(got['rules'])}")
    if got["text"] != row["text"]:
        out.append(f"text: intended {row['text']!r}, got {got['text']!r}")
    return out


def intended_row_problems(row: dict) -> list[str]:
    """Static checks on one hand-authored row: required fields, and a reason it exists."""
    out = [f"missing {k}" for k in ("id", "layout", "hook", "payload", "decision", "rules", "text")
           if k not in row]
    if row.get("layout") not in LAYOUTS:
        out.append(f"unknown layout {row.get('layout')!r}")
    if row.get("decision") not in ("allow", "advise", "ask", "deny"):
        out.append(f"bad decision {row.get('decision')!r}")
    if not str(row.get("why") or "").strip():
        out.append("empty why")
    if not str(row.get("task") or "").strip():
        out.append("empty task")
    if (row.get("decision") == "allow") == bool(row.get("rules")):
        out.append("an allow row must name no rules, and any other row must name at least one")
    return out


def supersession_problems(intended: list[dict], legacy: list[dict], templates: dict, current) -> list[str]:
    """Every `supersedes` must name a real legacy row with the identical case and a verdict the new engine
    really changes. `current(row)` -> {"decision", "rules"} for the intended row as the engine runs it now."""
    by_id = {r["id"]: r for r in legacy}
    out = []
    for row in intended:
        target = row.get("supersedes")
        if not target:
            continue
        old = by_id.get(target)
        if old is None:
            out.append(f"{row['id']}: supersedes {target}, which is not a legacy row")
            continue
        if (old["payload"], old["hook"], old["layout"], old.get("payload_note")) != \
                (row["payload"], row["hook"], row["layout"], row.get("payload_note")):
            out.append(f"{row['id']}: payload, hook or layout differs from the legacy row {target}")
            continue
        now = current(row)
        was = (old["decision"], legacy_rule_ids(old["text"], templates))
        if (now["decision"], set(now["rules"])) == was:
            out.append(f"{row['id']}: supersedes {target}, but the engine still gives the legacy verdict {was[0]} "
                       f"{sorted(was[1])}; a stale supersession hides a later regression")
    return out


def check_intended(only: str | None = None) -> int:
    """Debugging aid: fire intended rows and print actual vs intended. Never writes a file."""
    rows = load_rows(REPO / "tests" / "guard_corpus" / "intended.jsonl")
    if only:
        rows = [r for r in rows if r["id"] == only]
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        installs = {name: make_install(Path(td), REPO / ".claude" / "hooks", name) for name in LAYOUTS}
        for r in rows:
            got = fire_full(installs[r["layout"]], r)
            problems = intended_problems(r, got)
            bad += bool(problems)
            print(f"{'DIFF' if problems else 'ok  '} {r['id']}: {got['decision']} {sorted(got['rules'])}")
            for p in problems:
                print("     " + p)
    print(f"{len(rows)} intended row(s), {bad} differ")
    return 1 if bad else 0


def _from_test_hooks() -> list[tuple[str, str]]:
    """Every payload tests/test_hooks.py feeds to cmd()/fpath(), evaluated rather than retyped.

    Two sources, both by AST: (1) a cmd(...)/fpath(...) call whose argument is a literal or f-string;
    (2) a `@pytest.mark.parametrize` row, for a parameter the decorated test passes straight to
    cmd()/fpath(). Expected-decision columns are ignored: the corpus records what the hook does.
    """
    tree = ast.parse((REPO / "tests" / "test_hooks.py").read_text(encoding="utf-8"))
    ns = {"GAME": GAME, "GAMEW": GAMEW, "BS": BS}

    def ev(node):
        try:
            return eval(compile(ast.Expression(node), "<arg>", "eval"), {}, dict(ns))
        except Exception:
            return None

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("cmd", "fpath") and node.args:
            value = ev(node.args[0])
            if isinstance(value, str):
                found.add((node.func.id, value))
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for dec in fn.decorator_list:
            if not (isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "parametrize"
                    and len(dec.args) >= 2):
                continue
            names = ev(dec.args[0])
            names = [n.strip() for n in names.split(",")] if isinstance(names, str) else None
            if not names or not isinstance(dec.args[1], (ast.List, ast.Tuple)):
                continue
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and getattr(call.func, "id", None) in ("cmd", "fpath")
                        and call.args and isinstance(call.args[0], ast.Name) and call.args[0].id in names):
                    continue
                idx = names.index(call.args[0].id)
                for row in dec.args[1].elts:
                    cell = row.elts[idx] if isinstance(row, (ast.Tuple, ast.List)) and len(names) > 1 else row
                    value = ev(cell)
                    if isinstance(value, str):
                        found.add((call.func.id, value))
    return sorted(found)


def _bash_payload(c):
    return {"tool_name": "Bash", "tool_input": {"command": c}}


def _write_payload(p):
    return {"tool_name": "Write", "tool_input": {"file_path": p}}


def inputs() -> list[dict]:
    rows = []

    def add(rid, layout, hook, payload):
        rows.append({"id": rid, "layout": layout, "hook": hook, "payload": payload})

    for kind, value in _from_test_hooks():
        i = hashlib.sha1(f"{kind}\0{value}".encode("utf-8")).hexdigest()[:10]
        hook = "protect-bash.sh" if kind == "cmd" else "protect-files.sh"
        payload = _bash_payload(value) if kind == "cmd" else _write_payload(value)
        add(f"testhooks-{i}", "declared", hook, payload)

    spellings = {
        "fwd": GAME, "back": GAMEW, "msys": "/c/GOG Games/The Elder Scrolls V Skyrim VR",
        "doubled": GAME.replace("/", "//"), "doubled-back": GAMEW.replace(BS, BS * 2),
        "upper": GAME.upper(), "mixed": "c:\\GOG Games/The Elder Scrolls V Skyrim VR",
    }
    destroyers = ['rm -rf "{p}"', 'rm -r -f "{p}/Data"', 'rm --recursive --force "{p}"', 'rm -rf -- "{p}"',
                  'rmdir /s /q "{p}"', 'del /f /s /q "{p}"', 'Remove-Item -Recurse "{p}"', 'find "{p}" -delete',
                  'python -c "import shutil; shutil.rmtree(r\'{p}\')"', 'erase "{p}/x.txt"']
    for sname, p in spellings.items():
        for j, d in enumerate(destroyers):
            add(f"dialect-{sname}-{j}", "declared", "protect-bash.sh", _bash_payload(d.format(p=p)))
        add(f"dialect-{sname}-ls", "declared", "protect-bash.sh", _bash_payload(f'ls "{p}/Data"'))
        add(f"dialect-{sname}-write-esp", "declared", "protect-files.sh", _write_payload(f"{p}/Data/My.esp"))

    shell_rules = {
        "config-delete": 'rm -rf "C:/Users/u/Documents/My Games/Skyrim VR/x.ini"',
        "reg-delete": "reg delete HKLM\\SOFTWARE\\WOW6432Node\\Bethesda /f",
        "regexe-delete": "reg.exe delete HKLM\\SOFTWARE\\Bethesda /f",
        "remove-itemproperty": "Remove-ItemProperty -Path HKLM:\\SOFTWARE\\Bethesda -Name x",
        "psc-output": "tools/Champollion/Champollion.exe x.pex -o out/MyScript.psc",
        "psc-output-long": 'AutoMod papyrus decompile x.pex --output "src/A.psc"',
        "champollion-pex": f'Champollion "{GAME}/Data/Scripts/MyScript.pex"',
        "champollion-temp": 'Champollion "C:/Temp/MyScript.pex"',
        "delete-inside": f'rm "{GAME}/Data/Textures/x.dds"',
        "relative-data": "rm Data/Textures/x.dds",
        "relative-data-flags": "rm -r -f ./Data/x",
        "relative-mydata": "rm mydata/cache.bin",
        "mv-inside": f'mv a.txt "{GAME}/Data/a.txt"',
        "cp-inside": f'cp a.txt "{GAME}/Data/a.txt"',
        "redirect": f'echo x > "{GAME}/Data/a.txt"',
        "sed-i": f'sed -i "s/a/b/" "{GAME}/Data/a.ini"',
        "plugin-ref": "python read.py Data/My.esp",
        "loadorder-ref": "cat plugins.txt",
        "automod-write-nodry": 'bash tools/automod-cli.sh esp add-weapon "M.esp" X --json',
        "automod-write-dry": 'bash tools/automod-cli.sh esp add-weapon "M.esp" X --dry-run --json',
        "automod-nif": "bash tools/automod-cli.sh nif scale x.nif 2",
        "automod-archive": "bash tools/automod-cli.sh archive create d --output x.bsa",
        "resaver-apply": "bash tools/resaver-cli.sh reset-havok save.ess --apply",
        "resaver-dry": "bash tools/resaver-cli.sh reset-havok save.ess",
        "ext-tool-out": f'Caprica -o "{GAME}/Data/Scripts" x.psc',
        "benign-git": "git status",
        "benign-pytest": "python -m pytest tests/ -q",
        "multiline-destroyer-split": f'rm\n"{GAME}"',
        "multiline-two-cmds": f'echo start\nrm -rf "{GAME}"',
        "crlf": f'rm -rf "{GAME}"\r\n',
        "cr-line-end": f'cat "{GAME}/Data/x.txt"\r',
        "trailing-newlines": "git status\n\n\n",
        "unicode-path": f'rm "{GAME}/Data/Ëlven Ármor.esp"',
        "unicode-benign": "echo 'Ébène'",
        "compound-tmp": f'rm -rf /tmp/x && ls "{GAME}"',
    }
    # Unicode probes: GNU grep (bash) is ASCII-only for [[:space:]], \b and case folding; the port compiles
    # with re.ASCII. (The \b-next-to-a-non-ASCII-letter residue is accepted and tested separately.)
    ws = {"nbsp": "\u00a0", "fs": "\x1c", "nel": "\x85", "emsp": "\u2003", "lsep": "\u2028", "ideo": "\u3000"}
    for k, ch in ws.items():
        shell_rules[f"unicode-ws-rm-{k}"] = f'rm{ch}-rf "{GAME}"'
    shell_rules["unicode-ws-psc"] = "Champollion -o\u00a0out.psc"
    shell_rules["unicode-ws-archive"] = "automod archive\u00a0create a"
    shell_rules["unicode-ws-cp"] = f'cp\u00a0a "{GAME}"'
    shell_rules["unicode-ws-redirect"] = f'echo x >\u00a0"{GAME}"'
    shell_rules["unicode-fold-kelvin"] = 'rm "C:/Users/u/Documents/My Games/S\u212Ayrim VR/a.ini"'
    shell_rules["unicode-fold-dotless"] = f'rmd\u0131r "{GAME}"'
    shell_rules["unicode-fold-longs-sed"] = f'\u017Fed -i s/a/b/ {GAME}/x'
    shell_rules["unicode-fold-longs-esp"] = "cat My.e\u017Fp"
    shell_rules["unicode-lead-eacute"] = f'\u00e9automod -o "{GAME}/x"'
    for name, c in shell_rules.items():
        add(f"shell-{name}", "declared", "protect-bash.sh", _bash_payload(c))
        add(f"shell-{name}@config", "config", "protect-bash.sh", _bash_payload(c))
    # Attribution twins: the verdict must come from the hook's own logic, not from the env file.
    add("shell-config-dir-nolayout", "declared", "protect-bash.sh",
        _bash_payload('rm -rf "D:/Profiles/MO2/profiles/Default/SkyrimPrefs.ini"'))
    add("shell-gamepath-destroyer-undeclared", "undeclared", "protect-bash.sh",
        _bash_payload(f'rm -rf "{GAME}/Data"'))
    add("shell-config-dir-declared", "config", "protect-bash.sh",
        _bash_payload('rm -rf "D:/Profiles/MO2/profiles/Default/SkyrimPrefs.ini"'))

    write_rules = {
        "esp": f"{GAME}/Data/X.esp", "esm-upper": f"{GAME}/Data/X.ESM", "bsa-trailing-space": f"{GAME}/Data/X.bsa ",
        "claude-hooks": f"{GAME}/.claude/hooks/x.sh", "claude-memory": "C:/x/.claude/memory/m.md",
        "node-modules": "C:/p/node_modules/x/y.js", "skyrim-ini": "C:/Users/u/Documents/My Games/Skyrim VR/Skyrim.ini",
        "skse-ini": f"{GAME}/Data/SKSE/Plugins/x.ini", "plugins-txt": "C:/Users/u/AppData/Local/Skyrim VR/plugins.txt",
        "psc": f"{GAME}/Data/Scripts/Source/X.psc", "pex": f"{GAME}/Data/Scripts/X.pex",
        "data-other": f"{GAME}/Data/meshes/x.nif", "relative-data": "Data/x.txt",
        "outside": "C:/Users/u/Projects/notes.md", "toolkit-checkout": "C:/Users/u/Projects/skyrimvr-claude-toolkit/README.md",
        "unicode": f"{GAME}/Data/Ëlven.txt",
        "cr-plugins": "C:/Users/u/AppData/Local/Skyrim VR/plugins.txt\r",
        "cr-ini": "C:/Users/u/Documents/My Games/Skyrim VR/Skyrim.ini\r",
    }
    for name, p in write_rules.items():
        add(f"write-{name}", "declared", "protect-files.sh", _write_payload(p))

    for layout in ("undeclared", "metachar"):
        add(f"root-{layout}-self", layout, "protect-bash.sh", None)  # payload filled at record time
    for hook in ("protect-bash.sh", "protect-files.sh"):
        add(f"payload-empty-{hook}", "declared", hook, None)
        add(f"payload-invalid-{hook}", "declared", hook, "{not json")
        add(f"payload-array-{hook}", "declared", hook, "[1,2]")
        add(f"payload-notool-{hook}", "declared", hook, {"tool_name": "Bash"})
    return sorted(rows, key=lambda r: r["id"])


def record(hooks_src: Path, out: Path) -> int:
    if needs_missing_jq(hooks_src):
        raise SystemExit("refusing to record: the reference hooks need jq and it is not installed "
                         "(every row would come back GUARD INERT)")
    rows = inputs()
    with tempfile.TemporaryDirectory() as td:
        installs = {name: make_install(Path(td), hooks_src, name) for name in LAYOUTS}
        lines = []
        for r in rows:
            install = installs[r["layout"]]
            payload = r["payload"]
            if r["id"].startswith("root-"):          # delete the install's own Data, by its real path
                # Stored as a template so the corpus does not change with every temp dir.
                r = dict(r, payload=_bash_payload('rm -rf "<install>/Data"'), payload_note="install-relative")
                payload = install_relative(r["payload"], install)
            res = fire(install, r["hook"], payload)
            if "GUARD INERT" in res["text"] and not r["id"].startswith("payload-"):
                raise SystemExit(f"refusing to record: row {r['id']} came back GUARD INERT: {res['text'][:120]}")
            lines.append(json.dumps({**r, **res}, ensure_ascii=False, sort_keys=True))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hooks", type=Path, default=REPO / ".claude" / "hooks")
    ap.add_argument("--out", type=Path, default=REPO / "tests" / "guard_corpus" / "expected.jsonl")
    ap.add_argument("--check-intended", action="store_true",
                    help="fire the hand-authored rows in intended.jsonl and print actual vs intended; writes nothing")
    ap.add_argument("--id", help="with --check-intended: only this row")
    a = ap.parse_args()
    if a.check_intended:
        sys.exit(check_intended(a.id))
    print(f"recorded {record(a.hooks, a.out)} rows from {a.hooks}")
