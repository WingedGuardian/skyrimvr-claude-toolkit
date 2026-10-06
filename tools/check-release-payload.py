#!/usr/bin/env python3
"""check-release-payload -- is a toolkit payload whole, and does it carry nothing it must not?

    python tools/check-release-payload.py <payload root> [--expect-commit SHA] [--section NAME ...]
    python tools/check-release-payload.py --leaks <dir> [<dir> ...] [--allow-prefix PATH ...] [--changed-from DIR]

ONE checker for both release gates: .github/workflows/release.yml runs it on the zip it is about to publish
(through tools/verify-release.sh --local), and verify-release.sh runs it on the zip a user downloads. It
folds in what used to be three places -- the bash asserts in release.yml, tools/check-codex-payload.py and
tools/check-adapter-payload.py -- so the two gates cannot drift apart again.

Every check is a POSITIVE assertion where one exists (a hook must contain the exact line that works; a
blocklist of broken spellings passed three more broken ones), and each failure names its own line.

Sections (all run by default; --section picks some, for tests and for a tree that is not a payload):
  build    BUILD-INFO at the root names a 40-hex commit (and the expected one, with --expect-commit)
  shape    the files every doc tells a user to run are present; dev-only and per-user paths are absent,
           among them tests/fixtures/routes-harness/ (named on its own line)
  hooks    the hook floor, recounted from the tree: CLAUDE_HOOK_FLOOR Claude wrappers plus
           CODEX_ENTRY_FLOOR Codex entries; every one reads its stdin the way that works and writes a
           liveness heartbeat outside a comment
  codex    the frozen definitions parse, are unrendered, and every script they run is here; the rules
           file; .agents skills mirror .claude skills; AGENTS.md within the size Codex reads untruncated
  adapter  the adapter kit (ADAPTING.md, tools/adapting/, the files it cites)

--leaks greps files for the home folder of whoever runs it, in every spelling (C:/Users/<name>,
C:\\Users\\<name>, /c/Users/<name>, JSON-escaped, /home/<name>, UTF-8 and UTF-16), and for the bare user name
when it is not a generic account name. The name comes from the environment at check time and is never
written into this repo. RELEASE_LEAK_NAMES (comma-separated) adds names; RELEASE_LEAK_PATHS ('|'-separated)
adds paths (e.g. the machine's real Skyrim folder). --allow-prefix blanks one path (all spellings) before
matching: an install's own folder is expected inside the files setup.sh generates there. --changed-from DIR
skips files byte-identical to DIR's (the payload), so an installed copy reports only what setup generated.

Exit 0 = every check passed, 1 = at least one failed (each named), 2 = bad usage or nothing to check.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ------------------------------------------------------------------ the hook floor (recounted 2026-10-05)
# .claude/hooks: backup-before-edit, protect-bash, protect-files, protect-mcp, session-kb-guard,
# snapshot-before-tool. .codex/hooks: skyguard-entry.sh, skyguard-entry.ps1. tests/test_release_payload.py
# asserts both numbers against the tree, so a hook added or dropped makes this file change too.
CLAUDE_HOOK_FLOOR = 6
CODEX_ENTRY_FLOOR = 2
HOOK_FLOOR = CLAUDE_HOOK_FLOOR + CODEX_ENTRY_FLOOR

AGENTS_MD_BUDGET = 32768   # scripts/gen_agent_trees.py INSTRUCTION_BUDGET (Codex truncates past it)
SCRIPT_RE = re.compile(r'\{\{CODEX_ROOT(?:_POSIX)?\}\}/([^"\\]+)')
HEARTBEAT_RE = re.compile(r"^[^#]*hook-heartbeat", re.M)
PS_STDIN_RE = re.compile(r"^[^#]*\[Console\]::OpenStandardInput\(\)", re.M)
SHA_RE = re.compile(r"[0-9a-f]{40}")

# Present: what every doc surface tells a user to run, or what the hooks delegate to.
CORE_REQUIRED = (
    "setup.sh",
    "KNOWLEDGEBASE.md",
    "CLAUDE.md",
    "AGENTS.md",
    "tools/hook-canary.sh",
    "tools/kb-guard.sh",
    "tools/skyguard/run.py",
    "scripts/gen_agent_trees.py",
)
# Absent: dev-only trees, the user's own files, and runtime state. A path, or a file NAME anywhere.
ABSENT_PATHS = (
    "tests",
    "tests/fixtures/routes-harness",
    ".github",
    ".git",
    ".superpowers",
    ".skyguard",
    "KNOWLEDGEBASE.local.md",
    ".claude/backups",
    ".claude/settings.local.json",
    "node_modules",
    ".venv",
)
ABSENT_NAMES = ("skyrim-paths.env", ".nexus_api_key")

ADAPTER_REQUIRED = (
    "ADAPTING.md",
    "tools/adapting/adapter_template.py",
    "tools/adapting/capability-report.template.json",
    "tools/adapter_conformance.py",
    "tools/inventory-runtimes.py",
    "tools/skyguard/neutral.py",
    ".claude/skills/toolkit-setup/SKILL.md",
    ".agents/skills/toolkit-setup/SKILL.md",
    # What ADAPTING.md sends the agent to read (the worked examples), and the files its citations resolve
    # against: a dropped one leaves the agent a dead reference and an unbacked claim.
    "tools/adapting/examples/json_deny.slots.py",
    "tools/adapting/examples/exit2.slots.py",
    "tools/hook-canary.sh",
    "tools/skyguard/run.py",
    "tools/skyguard/engine.py",
    "tools/skyguard/health.py",
    "tools/skyguard/mcp_rules.py",
    "tools/skyguard/patch_paths.py",
    "tools/skyguard/adapters/claude.py",
    "tools/skyguard/adapters/codex.py",
    "agent/guards/adapters/codex/skyguard-entry.sh",
    "README.md",
    "docs/safety-philosophy.md",
    "docs/superpowers/specs/2026-09-30-universal-agent-support-design.md",
    "docs/superpowers/measurements/2026-10-02-plan4-codex-derisk.md",
    "docs/superpowers/measurements/2026-10-05-plan7-adapter-latency.md",
)


# ------------------------------------------------------------------ sections

def check_build(root: Path, expect_commit=None):
    p = root / "BUILD-INFO"
    if not p.is_file():
        yield "BUILD-INFO present (built by scripts/build-release-zip.sh)", "MISSING"
        return
    fields = dict(ln.split("=", 1) for ln in p.read_text(encoding="utf-8").splitlines() if "=" in ln)
    commit = fields.get("commit", "")
    yield "BUILD-INFO present (built by scripts/build-release-zip.sh)", None
    yield "BUILD-INFO names a full commit sha", None if SHA_RE.fullmatch(commit) else "commit={!r}".format(commit)
    if expect_commit is not None:
        yield "BUILD-INFO commit is the expected one", (
            None if commit == expect_commit else "BUILD-INFO says {!r}, expected {!r}".format(commit, expect_commit))


def check_shape(root: Path):
    for rel in CORE_REQUIRED:
        p = root / rel
        yield "{} present".format(rel), None if p.is_file() and p.stat().st_size > 0 else "MISSING or empty"
    for rel in ABSENT_PATHS:
        yield "{} NOT shipped".format(rel), "PRESENT" if (root / rel).exists() else None
    for name in ABSENT_NAMES:
        found = sorted(p.relative_to(root).as_posix() for p in root.rglob(name))
        yield "no file named {} anywhere".format(name), ("PRESENT: " + ", ".join(found)) if found else None


def check_hooks(root: Path):
    claude = sorted((root / ".claude" / "hooks").rglob("*.sh"))
    yield "Claude hook wrappers: {} (floor {})".format(len(claude), CLAUDE_HOOK_FLOOR), (
        None if len(claude) >= CLAUDE_HOOK_FLOOR else "only {}: a hook is missing".format(len(claude)))
    codex = sorted(p for p in (root / ".codex" / "hooks").rglob("*") if p.is_file()) \
        if (root / ".codex" / "hooks").is_dir() else []
    yield "Codex hook entries: {} (floor {})".format(len(codex), CODEX_ENTRY_FLOOR), (
        None if len(codex) >= CODEX_ENTRY_FLOOR else "only {}: an entry is missing".format(len(codex)))
    yield "hook floor {} = {} Claude + {} Codex".format(HOOK_FLOOR, CLAUDE_HOOK_FLOOR, CODEX_ENTRY_FLOOR), (
        None if len(claude) + len(codex) >= HOOK_FLOOR else "{} in all".format(len(claude) + len(codex)))
    # `INPUT=$(cat /dev/stdin)` returns ZERO BYTES when Claude Code spawns a hook, while a bare `cat` returns
    # the payload: every hook that used it fired, read nothing, and exited 0 -- byte-identical to "this is
    # fine". No suite can see it (a suite pipes stdin). Assert the line that works, exactly.
    for h in claude + [p for p in codex if p.suffix == ".sh"]:
        text = h.read_text(encoding="utf-8", errors="replace")
        rel = h.relative_to(root).as_posix()
        yield "{} reads stdin with a bare cat".format(rel), (
            None if "INPUT=$(cat)" in text.splitlines() else "no exact `INPUT=$(cat)` line: the hook would be INERT")
        yield "{} writes a heartbeat outside a comment".format(rel), (
            None if HEARTBEAT_RE.search(text) else "no heartbeat: tools/hook-canary.sh would have nothing to read")
    for h in [p for p in codex if p.suffix == ".ps1"]:
        text = h.read_text(encoding="utf-8", errors="replace")
        rel = h.relative_to(root).as_posix()
        yield "{} reads the standard input stream".format(rel), (
            None if PS_STDIN_RE.search(text) else "no [Console]::OpenStandardInput() outside a comment")
        yield "{} writes a heartbeat outside a comment".format(rel), (
            None if HEARTBEAT_RE.search(text) else "no heartbeat outside a comment")


def check_codex(root: Path):
    """A Codex install missing a piece does not error: a hook that cannot start lets the command RUN; a missing
    rules file loses the one line that holds while the hooks are unapproved; an AGENTS.md over 32 KiB is
    truncated SILENTLY by Codex in an untrusted folder."""
    defs = root / ".codex" / "hooks.json"
    hooks = None
    if not defs.is_file():
        yield ".codex/hooks.json present and parses", "MISSING"
    else:
        try:
            hooks = json.loads(defs.read_text(encoding="utf-8"))["hooks"]
            yield ".codex/hooks.json present and parses", None
        except (ValueError, KeyError, TypeError) as e:
            yield ".codex/hooks.json present and parses", "does not parse: {}".format(e)
    if hooks is not None:
        raw = defs.read_text(encoding="utf-8")
        # Rendered = it names one machine's folder, and Codex on every other machine runs nothing.
        yield ".codex/hooks.json still unrendered (setup.sh fills it)", (
            None if "{{CODEX_ROOT}}" in raw else "it carries a real install path, not {{CODEX_ROOT}}")
        handlers, missing, unmatched = 0, [], []
        for groups in hooks.values():
            for g in groups:
                for h in g.get("hooks", []):
                    handlers += 1
                    for key in ("command", "commandWindows"):
                        found = SCRIPT_RE.findall(h.get(key, ""))
                        if not found:
                            unmatched.append(key)
                        missing += [p for p in found if not (root / p).is_file()]
        problem = None
        if handlers == 0:
            problem = "no hook commands at all -- this check would be vacuous"
        elif unmatched:
            problem = "a command names no script under the install root ({})".format(", ".join(sorted(set(unmatched))))
        elif missing:
            problem = "missing: " + ", ".join(sorted(set(missing)))
        yield "every Codex hook command's script is in the payload", problem
    rules = root / ".codex" / "rules" / "skyrim.rules"
    yield ".codex/rules/skyrim.rules present", (
        None if rules.is_file() and rules.stat().st_size > 0 else "MISSING or empty")
    claude = sorted(p.parent.name for p in (root / ".claude" / "skills").glob("*/SKILL.md"))
    agents = sorted(p.parent.name for p in (root / ".agents" / "skills").glob("*/SKILL.md"))
    if not claude:
        problem = "no .claude/skills/*/SKILL.md -- this check would be vacuous"
    elif claude != agents:
        problem = "{} Claude skills, {} Codex skills (missing in .agents: {})".format(
            len(claude), len(agents), ", ".join(sorted(set(claude) - set(agents))) or "none")
    else:
        problem = None
    yield ".agents/skills matches .claude/skills ({} each)".format(len(claude)), problem
    agents_md = root / "AGENTS.md"
    if not agents_md.is_file():
        yield "AGENTS.md present and <= {} bytes".format(AGENTS_MD_BUDGET), "MISSING"
    else:
        n = agents_md.stat().st_size
        yield "AGENTS.md present and <= {} bytes".format(AGENTS_MD_BUDGET), (
            None if n <= AGENTS_MD_BUDGET else "{} bytes: Codex would silently truncate it".format(n))


def check_adapter(root: Path):
    """The zip is built by rule, so these ship with no list; this makes a rule change that drops one fail the
    release instead of leaving an unsupported agent with no ADAPTING.md."""
    for rel in ADAPTER_REQUIRED:
        p = root / rel
        if not p.is_file():
            yield rel, "MISSING"
        elif p.stat().st_size == 0:
            yield rel, "EMPTY"
        else:
            yield rel, None


SECTIONS = {"build": check_build, "shape": check_shape, "hooks": check_hooks, "codex": check_codex,
            "adapter": check_adapter}


# ------------------------------------------------------------------ leaks

# Account names that are not a person: a bare-name search for these would hit ordinary prose ("CI runner").
# Their home-path spellings are still searched.
GENERIC_ACCOUNTS = frozenset(("runner", "runneradmin", "user", "users", "admin", "administrator", "root", "home",
                              "vagrant", "ubuntu", "codespace", "codespaces", "vscode", "docker", "default",
                              "public", "guest", "test", "ci", "build", "builder", "nobody", "system"))
BS = "\\"


def leak_names(env=None, home=None):
    """The user names to search for, from the environment at check time (never from this repo)."""
    env = os.environ if env is None else env
    names = {env.get(k, "") for k in ("USERNAME", "USER", "LOGNAME")}
    home = home if home is not None else (env.get("USERPROFILE") or env.get("HOME") or "")
    if home:
        names.add(Path(home.replace(BS, "/")).name)
    names |= {n.strip() for n in env.get("RELEASE_LEAK_NAMES", "").split(",")}
    return sorted({n for n in names if len(n) >= 3 and "/" not in n and BS not in n})


def path_spellings(path: str):
    """One path as files spell it: forward, backslash, JSON-escaped backslash, and MSYS (/c/...)."""
    fwd = path.replace(BS, "/").rstrip("/")
    out = {fwd, fwd.replace("/", BS), fwd.replace("/", BS * 2)}
    m = re.match(r"^([A-Za-z]):/(.*)$", fwd)
    if m:
        out.add("/{}/{}".format(m.group(1).lower(), m.group(2)))
    return sorted(s for s in out if s)


def leak_needles(names, extra_paths=()):
    """(label, lowercase needle) pairs. Home-path spellings for every name; the bare name when it is a person's."""
    needles = []
    for n in names:
        for s in ("Users/" + n, "Users" + BS + n, "Users" + BS * 2 + n, "/home/" + n):
            needles.append(("home path ({})".format(s), s.lower()))
        if n.lower() not in GENERIC_ACCOUNTS and len(n) >= 4:
            needles.append(("user name", n.lower()))
    for p in extra_paths:
        for s in path_spellings(p):
            needles.append(("path ({})".format(s), s.lower()))
    return needles


def scan_leaks(dirs, needles, allow_prefixes=(), baseline=None):
    """[(file, label)] for every file under `dirs` holding a needle, UTF-8 or UTF-16, case-insensitively.

    `baseline`: a folder to compare against -- a file byte-identical to the one at the same path there is
    skipped, so a scan of an installed copy reports what setup GENERATED or changed, not the payload again."""
    allow = sorted({s.lower() for p in allow_prefixes for s in path_spellings(p)}, key=len, reverse=True)
    hits = []
    for d in dirs:
        d = Path(d)
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            data = f.read_bytes()
            if baseline is not None:
                twin = Path(baseline) / f.relative_to(d)
                if twin.is_file() and twin.read_bytes() == data:
                    continue
            for enc in ("utf-8", "utf-16-le"):
                try:
                    text = data.decode(enc).lower()
                except UnicodeDecodeError:
                    text = data.decode(enc, errors="ignore").lower()
                for a in allow:
                    text = text.replace(a, "<allowed>")
                for label, needle in needles:
                    if needle in text:
                        hits.append(("{}/{}".format(d.name, f.relative_to(d).as_posix()), label))
                        break
                else:
                    continue
                break
    return hits


def run_leaks(args) -> int:
    dirs, allow, baseline, i = [], [], None, 0
    while i < len(args):
        if args[i] == "--allow-prefix" and i + 1 < len(args):
            allow.append(args[i + 1])
            i += 2
        elif args[i] == "--changed-from" and i + 1 < len(args) and Path(args[i + 1]).is_dir():
            baseline, i = args[i + 1], i + 2
        elif args[i].startswith("--"):
            sys.stderr.write("unknown argument: {}\n".format(args[i]))
            return 2
        else:
            dirs.append(args[i])
            i += 1
    if not dirs or not all(Path(d).is_dir() for d in dirs):
        sys.stderr.write("usage: check-release-payload.py --leaks <dir> [<dir> ...] [--allow-prefix PATH ...] "
                         "[--changed-from DIR]\n")
        return 2
    names = leak_names()
    extra = [p for p in os.environ.get("RELEASE_LEAK_PATHS", "").split("|") if p.strip()]
    if not names and not extra:
        print("LEAKS: COULD NOT CHECK -- no user name in the environment and no RELEASE_LEAK_PATHS")
        return 2
    needles = leak_needles(names, extra)
    print("  searching {} dir(s) for {} spelling(s) of {} name(s) and {} extra path(s)".format(
        len(dirs), len(needles), len(names), len(extra)))
    hits = scan_leaks(dirs, needles, allow, baseline)
    for f, label in hits:
        print("  !! {}: {}".format(f, label))
    print("LEAKS: " + ("none" if not hits else "{} FILE(S) CARRY A HOME PATH OR NAME".format(len(hits))))
    return 1 if hits else 0


# ------------------------------------------------------------------ main

def checks(root: Path, sections=None, expect_commit=None):
    for name in sections or SECTIONS:
        fn = SECTIONS[name]
        yield from (fn(root, expect_commit) if name == "build" else fn(root))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--leaks"]:
        return run_leaks(argv[1:])
    root, expect, sections, i = None, None, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--expect-commit" and i + 1 < len(argv):
            expect, i = argv[i + 1], i + 2
        elif a == "--section" and i + 1 < len(argv) and argv[i + 1] in SECTIONS:
            sections.append(argv[i + 1])
            i += 2
        elif not a.startswith("--") and root is None:
            root, i = a, i + 1
        else:
            root = None
            break
    if root is None or not Path(root).is_dir():
        sys.stderr.write("usage: check-release-payload.py <payload root> [--expect-commit SHA] "
                         "[--section {}]\n".format("|".join(SECTIONS)))
        return 2
    fails = 0
    for name, problem in checks(Path(root), sections, expect):
        print("  {:<62} {}".format(name, "ok" if problem is None else "!! " + problem))
        fails += problem is not None
    print("RELEASE PAYLOAD: " + ("OK" if not fails else "{} CHECK(S) FAILED".format(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
