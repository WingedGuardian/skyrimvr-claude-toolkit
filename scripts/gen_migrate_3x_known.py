#!/usr/bin/env python3
"""Build tools/migrate-3x-known.json -- what every pre-4 release shipped -- from the git tags themselves.

The migration (tools/migrate-3x.py) must tell "the file a release shipped, rendered by its setup.sh" from "a
file the user edited". Writing that list from memory is how a version gets missed, so it is DERIVED: every tag
named `release` or `v<major>...` with major < 4 is read with `git show <tag>:<file>`, and nothing else.

Per tag it records:
  CLAUDE.md / AGENTS.md  the template's SHA-256 (LF), its line count, every line holding a {{PLACEHOLDER}}
                         (with its line number), and -- where the template has a {{MOD_MANAGER_PATHS}} line --
                         the blocks THAT tag's setup.sh could splice in there, read from its own heredocs
                         (the stock block literally; the MO2 block with its $VARS turned into placeholders).
  KNOWLEDGEBASE.md       SHA-256 and byte length (for "a shipped version with text appended").
  .claude/settings.json  SHA-256 of the text and of its canonical JSON.
and asserts that no pre-4 tag shipped Codex hook definitions (.codex/), which is why a 3.x user has nothing
to re-approve.

Before writing, it proves its own output: each template is rendered with made-up values (stock and MO2) and
must be recognised by the migration's matcher, and the same rendering with one character changed must not be.

Usage:  python scripts/gen_migrate_3x_known.py          (write)
        python scripts/gen_migrate_3x_known.py --check  (exit 1 if the committed file is stale)
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tools" / "migrate-3x-known.json"
TAG_RX = re.compile(r"\A(?:release|v([0-9]+)(?:\.[0-9]+)*)\Z")
PH = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def _tool():
    spec = importlib.util.spec_from_file_location("migrate_3x", REPO / "tools" / "migrate-3x.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(*args) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True).stdout


def show(tag: str, path: str):
    r = subprocess.run(["git", "-C", str(REPO), "show", "{}:{}".format(tag, path)], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def pre4_tags():
    tags = []
    for t in git("tag", "-l").decode().split():
        m = TAG_RX.match(t)
        if m and (m.group(1) is None or int(m.group(1)) < 4):
            tags.append(t)
    return sorted(tags, key=lambda t: [-1] if t == "release" else [int(x) for x in t[1:].split(".")])


def heredoc_blocks(setup: str):
    """The two mod-manager blocks a setup.sh writes: (stock lines, MO2 template lines). None if absent."""
    lines = setup.split("\n")

    def body(opener):
        for i, line in enumerate(lines):
            if opener in line:
                out = []
                for nxt in lines[i + 1:]:
                    if nxt == "MMEOF":
                        return out
                    out.append(nxt)
        return None

    stock = body("""cat > "$MM_BLOCK" <<'MMEOF'""")
    mo2 = body('''cat > "$MM_BLOCK" <<MMEOF''')
    if mo2 is not None:
        # An unquoted heredoc: bash expands $VAR / ${VAR} and drops the backslash of \` \$ \\.
        conv = []
        for line in mo2:
            line = re.sub(r"\$\{?([A-Z_][A-Z0-9_]*)\}?", r"{{\1}}", line.replace("\\$", "\0"))
            conv.append(line.replace("\\`", "`").replace("\\\\", "\\").replace("\0", "$"))
        mo2 = conv
    return stock, mo2


def _placeholders(lines):
    return [[i, line] for i, line in enumerate(lines) if PH.search(line)]


def doc_spec(text: str, setup: str, tag: str):
    lines = text.split("\n")
    spec = {"sha256": sha(text), "lines": len(lines), "placeholders": [], "tags": []}
    mm = [i for i, line in enumerate(lines) if line == "{{MOD_MANAGER_PATHS}}"]
    if mm:
        if len(mm) != 1:
            raise SystemExit("{}: {} {{MOD_MANAGER_PATHS}} lines, expected 1".format(tag, len(mm)))
        stock, mo2 = heredoc_blocks(setup)
        if stock is None or mo2 is None:
            raise SystemExit("{}: CLAUDE.md has {{MOD_MANAGER_PATHS}} but setup.sh's two blocks were not found".format(tag))
        variants = [{"sha256": sha("\n".join(stock)), "lines": len(stock), "placeholders": []},
                    {"sha256": sha("\n".join(mo2)), "lines": len(mo2), "placeholders": _placeholders(mo2)}]
        spec["block"] = {"index": mm[0], "variants": variants}
    spec["placeholders"] = [p for p in _placeholders(lines) if p[1] != "{{MOD_MANAGER_PATHS}}"]
    if not spec["placeholders"] and "block" not in spec:
        spec = {"sha256": spec["sha256"], "tags": []}
    return spec, (stock, mo2) if mm else (None, None)


def sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render(text: str, values: dict, block) -> str:
    out = []
    for line in text.split("\n"):
        if line == "{{MOD_MANAGER_PATHS}}" and block is not None:
            out.extend(render("\n".join(block), values, None).split("\n"))
        else:
            out.append(PH.sub(lambda m: values.get(m.group(1), "X-" + m.group(1)), line))
    return "\n".join(out)


VALUES = [
    {"GAME_ROOT": "C:/Games/Skyrim Test", "CONFIG_DIR": "D:/Docs/My Games/Skyrim VR", "LOADORDER_DIR": "E:/AppData/Skyrim VR",
     "DOCUMENTS_DIR": "D:/Docs", "USERNAME": "someone", "LOCALAPPDATA": "E:/AppData", "SKYRIM_FOLDER": "Skyrim VR",
     "MO2_INSTANCE": "F:/MO2", "MO2_PROFILE": "Default", "MO2_MODS": "F:/MO2/mods", "MO2_OVERWRITE": "F:/MO2/overwrite",
     "MO2_PROFILE_DIR": "F:/MO2/profiles/Default"},
    {"GAME_ROOT": "C:/A B/C", "CONFIG_DIR": "C:/A B/C", "LOADORDER_DIR": "x", "DOCUMENTS_DIR": "", "USERNAME": "u",
     "LOCALAPPDATA": "y", "SKYRIM_FOLDER": "Skyrim Special Edition", "MO2_INSTANCE": "m", "MO2_PROFILE": "p q",
     "MO2_MODS": "m/mods", "MO2_OVERWRITE": "m/o", "MO2_PROFILE_DIR": "m/profiles/p q"},
]


def self_test(tool, name, spec, template, blocks):
    """Renderings must match; a one-character edit (in a literal line, or inside the block) must not."""
    choices = [b for b in blocks if b is not None] or [None]
    if not tool.matches(template, spec):        # extracted, setup.sh never run: still the shipped file
        raise SystemExit("self-test: {} {} unrendered template NOT recognised".format(name, spec["tags"]))
    for values in VALUES:
        for block in choices:
            r = render(template, values, block)
            if not tool.matches(r, spec):
                raise SystemExit("self-test: {} {} rendering NOT recognised".format(name, spec["tags"]))
            lines = r.split("\n")
            probe = [len(lines) // 2]
            if "block" in spec:
                probe.append(spec["block"]["index"])
            for i in probe:
                edited = list(lines)
                edited[i] = edited[i] + "!"
                if tool.matches("\n".join(edited), spec):
                    raise SystemExit("self-test: {} {} accepted an edit on line {}".format(name, spec["tags"], i))


def build():
    tool = _tool()
    tags = pre4_tags()
    if "v3.9.1" not in tags:
        raise SystemExit("no v3.9.1 tag in this clone (fetch tags: git fetch --tags)")
    out = {"schema": 1, "generated_by": "scripts/gen_migrate_3x_known.py",
           "tags": {t: git("rev-list", "-n", "1", t).decode().strip() for t in tags},
           "codex_definitions_shipped": False,
           "docs": {"CLAUDE.md": [], "AGENTS.md": [], "KNOWLEDGEBASE.md": []}, "settings": []}
    by = {}
    for tag in tags:
        if any(p.startswith(".codex/") for p in git("ls-tree", "-r", "--name-only", tag).decode().split("\n")):
            raise SystemExit("{} ships .codex/ -- a 3.x user WOULD have Codex hooks to re-approve; "
                             "the migration's Codex note is wrong".format(tag))
        setup = (show(tag, "setup.sh") or b"").decode("utf-8")
        for name in ("CLAUDE.md", "AGENTS.md"):
            raw = show(tag, name)
            if raw is None:
                continue
            text = raw.decode("utf-8").replace("\r\n", "\n")
            spec, blocks = doc_spec(text, setup, tag)
            key = (name, json.dumps({k: v for k, v in spec.items() if k != "tags"}, sort_keys=True))
            if key not in by:
                by[key] = spec
                out["docs"][name].append(spec)
                self_test(tool, name, spec, text, blocks)
            by[key]["tags"].append(tag)
        raw = show(tag, "KNOWLEDGEBASE.md")
        if raw is not None:
            text = raw.decode("utf-8").replace("\r\n", "\n")
            key = ("KB", sha(text))
            if key not in by:
                by[key] = {"sha256": sha(text), "bytes": len(text.encode("utf-8")), "tags": []}
                out["docs"]["KNOWLEDGEBASE.md"].append(by[key])
            by[key]["tags"].append(tag)
        raw = show(tag, ".claude/settings.json")
        if raw is not None:
            text = raw.decode("utf-8").replace("\r\n", "\n")
            key = ("S", sha(text))
            if key not in by:
                hooks = sorted({(e, tool._script(c)) for e, m, c in tool._hook_entries(json.loads(text))})
                by[key] = {"sha256": sha(text), "canonical_sha256": sha(tool.canonical_json(text)), "tags": [],
                           "hooks": [list(h) for h in hooks]}
                out["settings"].append(by[key])
            by[key]["tags"].append(tag)
    return json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv) -> int:
    text = build()
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("tools/migrate-3x-known.json is stale: run python scripts/gen_migrate_3x_known.py")
            return 1
        print("tools/migrate-3x-known.json is in sync with the tags")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print("wrote {} ({} tags)".format(OUT.relative_to(REPO).as_posix(), len(json.loads(text)["tags"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
