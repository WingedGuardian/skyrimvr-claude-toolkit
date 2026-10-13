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
  guards   every guard file hashes to what the payload's own GUARD-FILES.sha256 says (Plan 6 R12)

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
    # Plan 6: what README tells a user to run for the lock, lifts, snapshots and skydoctor, and the guard manifest
    # the session-start banner reads (its hashes are checked in the guards section).
    "tools/skyguard.sh",
    "tools/skyguard.cmd",
    "tools/skyguard-unlock.cmd",
    "tools/skydoctor",
    "tools/skydoctor.cmd",
    "GUARD-FILES.sha256",
    "scripts/gen-guard-hashes.py",
    # Bundled diagnostics README lists as "no install beyond Python 3", and the path discovery they import.
    "tools/skyrim_paths.py",
    "tools/skse-load-status.py",
    "tools/archive-triage.py",
    "tools/mirror-launch-logs.py",
    "tools/mod-install.py",
    "tools/nexus.sh",
    "tools/nexus-render.py",
)
# Absent: dev-only trees, the user's own files, and runtime state. A path, or a file NAME anywhere.
ABSENT_PATHS = (
    "tests",
    "tests/fixtures/routes-harness",
    ".github",
    "scripts/layer2-spike",    # Plan 6 Layer 2 lock research probes: developer-only, kept in the repo, never shipped
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
# Any dotenv-style file (review M8): a committed `.env` / `x.env` / `.env.local` is where secrets live.
# A documented `*.example` template is not one.
DOTENV_GLOBS = (".env", "*.env", ".env.*", "*.env.local")

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
    "tools/skyguard/doctor.py",
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
    found = sorted({p.relative_to(root).as_posix() for g in DOTENV_GLOBS for p in root.rglob(g)
                    if p.is_file() and not p.name.endswith(".example")})
    yield "no dotenv-style file (.env, *.env, .env.*) anywhere", ("PRESENT: " + ", ".join(found)) if found else None


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
    # Named, not only counted (review M5): every hook .claude/settings.json registers must be in the payload,
    # and the registered set must reach the floor -- a stray extra *.sh cannot stand in for a missing hook.
    settings = root / ".claude" / "settings.json"
    try:
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        cmds = [h.get("command", "") for ms in cfg.get("hooks", {}).values() for m in ms for h in m.get("hooks", [])]
        named = sorted({p for c in cmds for p in re.findall(r"\$CLAUDE_PROJECT_DIR/([^\"' ]+)", c)})
        missing = [p for p in named if not (root / p).is_file()]
        problem = ("missing: " + ", ".join(missing)) if missing else (
            None if len(named) >= CLAUDE_HOOK_FLOOR else "only {} hook(s) registered".format(len(named)))
    except (OSError, ValueError, AttributeError, TypeError) as e:
        problem = "unreadable: {}".format(e)
    yield ".claude/settings.json registers the hooks, and each is shipped", problem
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


GUARD_MANIFEST = "GUARD-FILES.sha256"
GUARD_HASHER = "scripts/gen-guard-hashes.py"


def check_guards(root: Path):
    """Plan 6 R12: the payload's guard files hash to what its own GUARD-FILES.sha256 says -- the manifest the
    session-start banner compares an install against. A stale manifest ships a banner that cries wolf on every
    install (or one that stays quiet about a real edit). Hashed by the PAYLOAD's own gen-guard-hashes.py, as an
    extracted install would; that file is then itself among the files the manifest names or the hasher needs."""
    manifest, hasher = root / GUARD_MANIFEST, root / GUARD_HASHER
    yield "{} present".format(GUARD_MANIFEST), None if manifest.is_file() and manifest.stat().st_size > 0 else "MISSING"
    if not manifest.is_file() or not hasher.is_file():
        yield "guard files match {}".format(GUARD_MANIFEST), "cannot check: {} or {} missing".format(
            GUARD_MANIFEST, GUARD_HASHER)
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("payload_gen_guard_hashes", hasher)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        problems = mod.verify(root)
        named = sum(1 for ln in manifest.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#"))
    except Exception as e:  # noqa: BLE001 -- an unreadable manifest is a failed check, never a pass
        yield "guard files match {}".format(GUARD_MANIFEST), "unreadable: {}".format(e)
        return
    yield "guard files match {} ({} named)".format(GUARD_MANIFEST, named), (
        ("; ".join(problems[:5]) + (" (+{} more)".format(len(problems) - 5) if len(problems) > 5 else ""))
        if problems else (None if named else "the manifest names no file"))


SECTIONS = {"build": check_build, "shape": check_shape, "hooks": check_hooks, "codex": check_codex,
            "adapter": check_adapter, "guards": check_guards}


# ------------------------------------------------------------------ leaks

# Account names that are not a person: a bare-name search for these would hit ordinary prose ("CI runner").
# Their home-path spellings are still searched.
GENERIC_ACCOUNTS = frozenset(("runner", "runneradmin", "user", "users", "admin", "administrator", "root", "home",
                              "vagrant", "ubuntu", "codespace", "codespaces", "vscode", "docker", "default",
                              "public", "guest", "test", "ci", "build", "builder", "nobody", "system"))
BS = "\\"
# Secrets whose VALUE is grepped for (read at check time, never printed, never stored): a value match has no
# false positives. Files are relative to the checkout running the check.
SECRET_VARS = ("X4_NEXUS_KEY", "NEXUS_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")
SECRET_FILES = ("tools/.nexus_api_key",)
# Key SHAPES, for a secret this machine does not hold. Each is labelled; the matched text is never printed.
KEY_SHAPES = (
    ("Nexus personal API key shape",
     re.compile(r"[A-Za-z0-9+/]{16,}={0,2}--[A-Za-z0-9+/]{8,}={0,2}--[A-Za-z0-9+/]{8,}={0,2}")),
    ("GitHub token shape", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("OpenAI-style secret key shape", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("AWS access key id shape", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)
# The config folder setup.sh records is allow-listed in generated files ONLY in this shape, strictly below the
# home folder: <home>/[...]/Documents/My Games/Skyrim*. Anything else (the home itself, Desktop, .ssh) shows.
CONFIG_SHAPE = re.compile(r"^(?:[^/]+/)*Documents/My Games/Skyrim[^/]*$", re.I)


def leak_names(env=None, home=None):
    """The user names to search for, from the environment at check time (never from this repo)."""
    env = os.environ if env is None else env
    names = {env.get(k, "") for k in ("USERNAME", "USER", "LOGNAME")}
    home = home if home is not None else (env.get("USERPROFILE") or env.get("HOME") or "")
    if home:
        names.add(Path(home.replace(BS, "/")).name)
    names |= {n.strip() for n in env.get("RELEASE_LEAK_NAMES", "").split(",")}
    return sorted({n for n in names if len(n) >= 3 and "/" not in n and BS not in n})


def _raw_tokens(env):
    return [t.strip() for t in env.get("LEAK_TOKENS", "").replace("\r", "\n").split("\n") if t.strip()]


def _is_path(t):
    return "/" in t or BS in t or ":" in t


def leak_tokens(env=None):
    """LEAK_TOKENS (newline-separated; the release workflow fills it from a repository secret): a token holding
    a path separator or a drive colon is a path, anything else a person's name. -> (names, paths)"""
    env = os.environ if env is None else env
    toks = _raw_tokens(env)
    paths = [t for t in toks if _is_path(t)]
    return [t for t in toks if t not in paths], paths


def token_problems(env=None):
    """Review N3: a token too short or too vague matches nearly every file and blocks every release. Each
    problem names the token by its LINE NUMBER only, never by its text."""
    env = os.environ if env is None else env
    out = []
    for k, t in enumerate(_raw_tokens(env), 1):
        if _is_path(t):
            segs = [x for x in re.split(r"[\\/]+", t) if x and not re.fullmatch(r"[A-Za-z]:", x)]
            if len(t) < 4 or len(segs) < 2 or not any(re.search(r"[A-Za-z0-9]", x) for x in segs):
                out.append("token #{}: a path needs at least two named folders (write it as a Windows path, "
                           "e.g. C:\\Games\\Skyrim; the other spellings are derived)".format(k))
        elif len(t) < 4 or not re.search(r"[A-Za-z0-9]", t):
            out.append("token #{}: a name needs at least 4 characters, letters or digits".format(k))
    return out


def secret_values(env=None, checkout=None):
    """[(label, value)] of the secrets this machine holds. The label names the source, never the value."""
    env = os.environ if env is None else env
    out = [("secret value of " + k, env[k].strip()) for k in SECRET_VARS if len(env.get(k, "").strip()) >= 8]
    for rel in SECRET_FILES:
        p = Path(checkout) / rel if checkout else None
        if p is not None and p.is_file():
            v = p.read_text(encoding="utf-8", errors="ignore").strip()
            if len(v) >= 8:
                out.append(("secret value of " + rel, v))
    return out


def config_dir_ok(cfg: str, home: str) -> bool:
    """Is `cfg` the expected Skyrim config folder: <home>/[...]/Documents/My Games/Skyrim*, strictly below home?"""
    c, h = cfg.replace(BS, "/").rstrip("/"), home.replace(BS, "/").rstrip("/")
    if not c or not h or ".." in c.split("/") or not c.lower().startswith(h.lower() + "/"):
        return False
    return bool(CONFIG_SHAPE.match(c[len(h) + 1:]))


def path_spelling_kinds(path: str):
    """[(kind, spelling)]: one path as files spell it -- forward, backslash, JSON-escaped backslash or slash,
    MSYS (/c/...), each also with URL-encoded spaces. The kind is what a report may print; the spelling never."""
    fwd = path.replace(BS, "/").rstrip("/")
    out = [("forward", fwd), ("backslash", fwd.replace("/", BS)), ("json-escaped", fwd.replace("/", BS * 2)),
           ("json-slash", fwd.replace("/", BS + "/"))]
    m = re.match(r"^([A-Za-z]):/(.*)$", fwd)
    if m:
        out.append(("msys", "/{}/{}".format(m.group(1).lower(), m.group(2))))
    out += [(k + ", url-encoded", v.replace(" ", "%20")) for k, v in out if " " in v]
    seen, uniq = set(), []
    for k, v in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append((k, v))
    return uniq


def path_spellings(path: str):
    return sorted({v for _, v in path_spelling_kinds(path)})


def leak_needles(names, extra_paths=(), persons=()):
    """(label, lowercase needle) pairs. Home-path spellings for every name; the bare name when it is a person's
    (`persons` are always people: they come from LEAK_TOKENS)."""
    # Labels are `identity token #k (<spelling kind>)` -- review N1: a hit printed in a CI log must not publish
    # the identity the gate guards, so no label ever carries the needle.
    needles, k = [], 0
    for n in list(names) + list(persons):
        k += 1
        for kind, s in (("home path, forward", "Users/" + n), ("home path, backslash", "Users" + BS + n),
                        ("home path, json-escaped", "Users" + BS * 2 + n), ("home path, /home", "/home/" + n),
                        ("home path, url-encoded", "Users%2F" + n)):
            needles.append(("identity token #{} ({})".format(k, kind), s.lower()))
        if n in persons or (n.lower() not in GENERIC_ACCOUNTS and len(n) >= 4):
            needles.append(("identity token #{} (bare name)".format(k), n.lower()))
    for p in extra_paths:
        k += 1
        for kind, s in path_spelling_kinds(p):
            needles.append(("identity token #{} (path, {})".format(k, kind), s.lower()))
    return needles


def mask_lines(env=None):
    """`::add-mask::` lines for every LEAK_TOKENS token and every spelling derived from it (both cases), so a
    GitHub log never shows one in clear. Printed by the release workflow BEFORE any step that could print."""
    env = os.environ if env is None else env
    persons, paths = leak_tokens(env)
    vals = set(persons) | set(paths)
    for _, needle in leak_needles([], paths, persons):
        vals.add(needle)
    for p in paths:
        vals |= set(path_spellings(p))
    for n in persons:
        vals |= {"Users/" + n, "Users" + BS + n, "Users" + BS * 2 + n, "/home/" + n, "Users%2F" + n}
    vals |= {v.lower() for v in vals}
    return ["::add-mask::" + v for v in sorted(vals) if v]


def _line_of(text, idx):
    return text.count("\n", 0, idx) + 1


def scan_leaks(dirs, needles, allow_prefixes=(), baseline=None, secrets=(), shapes=KEY_SHAPES):
    """[(file, label)] for every file under `dirs` holding a needle (case-insensitive), a secret's exact value,
    or a key shape -- read as UTF-8, UTF-16LE and UTF-16BE. A secret or shape label carries the line number,
    never the text.

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
            name = "{}/{}".format(d.name, f.relative_to(d).as_posix())
            if any(needle in name.lower() for _, needle in needles):
                name = "{}/<file name withheld: it holds an identity token>".format(d.name)
            label = None
            for enc in ("utf-8", "utf-16-le", "utf-16-be"):
                raw = data.decode(enc, errors="ignore")
                text = raw.lower()
                for a in allow:
                    text = text.replace(a, "<allowed>")
                line = None
                for lb, needle in needles:
                    i = text.find(needle)
                    if i >= 0:
                        label, line = lb, _line_of(text, i)
                        break
                if label is None:
                    for lb, value in secrets:
                        i = raw.find(value)
                        if i >= 0:
                            label, line = lb, _line_of(raw, i)
                            break
                if label is None:
                    for lb, rx in shapes:
                        m = rx.search(raw)
                        if m:
                            label, line = lb, _line_of(raw, m.start())
                            break
                if label is not None:
                    # file:line and a label only -- never the matched text or the line around it.
                    hits.append(("{}:{}".format(name, line), label))
                    break
    return hits


def _fold_dir(path: str) -> str:
    """A folder spelled one way for comparison: forward slashes, lower case, `/c/x` as `c:/x`, no trailing slash
    except a root's own (`c:/`, `/`)."""
    s = (path or "").strip().replace(BS, "/")
    m = re.match(r"^/([A-Za-z])(/.*)?$", s)
    if m:
        s = m.group(1) + ":" + (m.group(2) or "/")
    s = re.sub(r"/+", "/", s).lower()
    return s if s == "/" or re.fullmatch(r"[a-z]:/", s) else s.rstrip("/")


def allow_prefix_problem(prefix: str, homes) -> str:
    """Why an --allow-prefix must NOT be applied ("" when it may). Review T13 M3: the allowance blanks every spelling
    of the prefix before matching, so one that is a drive root, the home folder or a folder above it would blank the
    very home paths this scan exists to find (a Python installed straight into the home folder makes the measured
    interpreter directory exactly that). Such an allowance is dropped and the scan stays strict."""
    p = _fold_dir(prefix)
    if not p or not (p.startswith("/") or re.match(r"^[a-z]:/", p)):
        return "is not an absolute path"
    if p == "/" or re.fullmatch(r"[a-z]:/", p):
        return "is a drive or filesystem root"
    for h in homes:
        hf = _fold_dir(h)
        if hf and hf == p:
            return "is the home folder"
        if hf and hf.startswith(p.rstrip("/") + "/"):
            return "holds the home folder"
    return ""


def _with_long_names(prefixes):
    """Each allowed prefix, plus the spelling the filesystem gives the same folder when that differs.

    One folder has two Windows spellings when TEMP carries 8.3 short names (windows-latest: the CI account's
    profile folder, in its 8.3 form).
    Git Bash's /tmp mount keeps TEMP's spelling, so `cygpath -m /tmp/x` gives the SHORT one, while `cd /tmp/x &&
    pwd -W` and Python give the LONG one -- and setup.sh writes the install's root through those. The workspace
    allowance then matched nothing setup wrote, and every installed file naming its own folder was reported as a
    leak (CI run 38187755448: 43 files; MEASURED locally with a short-named TEMP: the same 43). realpath() resolves
    through the folder itself, so the added spelling is the same folder, and allow_prefix_problem still vets it."""
    out = []
    for p in prefixes:
        out.append(p)
        try:
            real = os.path.realpath(p) if os.path.isdir(p) else ""
        except (OSError, ValueError):
            real = ""
        if real and _fold_dir(real) != _fold_dir(p):
            out.append(real)
    return out


def run_leaks(args) -> int:
    dirs, allow, baseline, cfgs, cfg_homes, require, i = [], [], None, [], [], False, 0
    while i < len(args):
        if args[i] == "--allow-prefix" and i + 1 < len(args):
            allow.append(args[i + 1])
            i += 2
        elif args[i] == "--allow-config" and i + 1 < len(args):
            cfgs.append(args[i + 1])
            i += 2
        elif args[i] == "--home" and i + 1 < len(args):
            # Repeatable: the real home, and the C:/Users/<login> home setup.sh synthesizes where it cannot ask
            # Windows for Documents (Linux, the CI runner). A config folder is validated below EACH, never a prefix.
            cfg_homes.append(args[i + 1])
            i += 2
        elif args[i] == "--changed-from" and i + 1 < len(args) and Path(args[i + 1]).is_dir():
            baseline, i = args[i + 1], i + 2
        elif args[i] == "--require-tokens":
            require, i = True, i + 1
        elif args[i].startswith("--"):
            sys.stderr.write("unknown argument: {}\n".format(args[i]))
            return 2
        else:
            dirs.append(args[i])
            i += 1
    if not dirs or not all(Path(d).is_dir() for d in dirs) or (cfgs and not cfg_homes):
        sys.stderr.write("usage: check-release-payload.py --leaks <dir> [<dir> ...] [--allow-prefix PATH ...] "
                         "[--allow-config DIR --home HOME ...] [--changed-from DIR] [--require-tokens]\n")
        return 2
    persons, token_paths = leak_tokens()
    problems = token_problems()
    if problems:
        for p in problems:
            print("  !! LEAK_TOKENS " + p)
        print("LEAKS: COULD NOT CHECK -- fix the LEAK_TOKENS lines named above (by number; their text is not shown)")
        return 2
    if require and not (persons or token_paths):
        # Fail CLOSED: on a CI runner the environment names `runner`, so without the maintainer's tokens this
        # grep would pass a tree full of the maintainer's paths.
        print("LEAKS: COULD NOT CHECK -- LEAK_TOKENS is empty or absent (the release workflow reads it from the "
              "repository secret LEAK_TOKENS); refusing to pass a grep that cannot see the maintainer")
        return 2
    homes = [h for h in (*cfg_homes, os.environ.get("USERPROFILE"), os.environ.get("HOME")) if h]
    kept = []
    for p in _with_long_names(allow):
        why = allow_prefix_problem(p, homes)
        if why:
            print("  !! an --allow-prefix was NOT applied: it " + why)    # the path itself is never printed
        else:
            kept.append(p)
    allow = kept
    for c in cfgs:
        if any(config_dir_ok(c, h) for h in cfg_homes):
            allow.append(c)
            print("  allow-listed the config folder (validated <home>/.../Documents/My Games/Skyrim*)")
        else:
            print("  !! config folder NOT allow-listed: it is not <home>/.../Documents/My Games/Skyrim*")
    names = leak_names()
    extra = [p for p in os.environ.get("RELEASE_LEAK_PATHS", "").split("|") if p.strip()] + token_paths
    secrets = secret_values(checkout=Path(__file__).resolve().parent.parent)
    if not names and not extra and not persons:
        print("LEAKS: COULD NOT CHECK -- no user name in the environment and no RELEASE_LEAK_PATHS")
        return 2
    needles = leak_needles(names, extra, persons)
    print("  searching {} dir(s) for {} spelling(s) of {} name(s), {} extra path(s), {} secret value(s), "
          "{} key shape(s)".format(len(dirs), len(needles), len(names) + len(persons), len(extra), len(secrets),
                                   len(KEY_SHAPES)))
    hits = scan_leaks(dirs, needles, allow, baseline, secrets)
    for f, label in hits:
        print("  !! {}: {}".format(f, label))   # f is file:line; label never carries the matched text
    print("LEAKS: " + ("none" if not hits else "{} FILE(S) CARRY A HOME PATH, NAME OR KEY".format(len(hits))))
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
    if argv == ["--mask-tokens"]:
        for ln in mask_lines():
            print(ln)
        return 0
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
