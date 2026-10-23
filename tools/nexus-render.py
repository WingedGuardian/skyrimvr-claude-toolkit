#!/usr/bin/env python3
"""nexus-render -- the parsing half of tools/nexus.sh: turns Nexus API responses into a desk review.

tools/nexus.sh owns the key and the HTTP status handling; it passes each response body here in an environment
variable, so a key never reaches this file and no response is ever re-fetched. Read-only, stdlib only.

    nexus-render.py find                         R     = the GraphQL v2 mods search response
    nexus-render.py page <domain> <modId> [--since VERSION] [--full]
                                                 INFO  = /v1/games/<domain>/mods/<id>.json
                                                 FILES = .../files.json   LOG = .../changelogs.json (optional)
                                                 REQ   = the GraphQL requirements response (optional)
                                                 LOG_STATUS=failed: the changelog fetch failed (never "none")
    nexus-render.py changelog [--since VERSION]  LOG
    nexus-render.py contents [VERSION]           FILES; fetches each file's content preview (below)
    nexus-render.py gql-find <name> <domain>     prints the GraphQL request body for `find`
    nexus-render.py gql-req <modId>              prints the GraphQL request body for the requirements

Nexus returns explicit nulls (category_name: null, version: null, updatedAt: null); every field read here
tolerates one. A part that could not be fetched says so ("could NOT fetch requirements -- not the same as none"),
never prints as empty.

Versions are sorted by vkey(), not as strings: as strings "3.0.35" sorts after "3.3.10".

Content previews: files.json carries each file's content_preview_link. It is fetched with this tool's own
User-Agent. If the preview host refuses that client (HTTP 403), the result says so and gives the link to open in a
browser: the toolkit does not pretend to be a browser.

Exit: 0 printed | 2 a response was not parseable, or bad usage (never print a blank "answer").
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modding_harness import discovery

UA = "skyrim-ai-assistant-toolkit-nexus/1 (+https://github.com/WingedGuardian/skyrimvr-claude-toolkit)"
GAME_IDS = {"skyrimspecialedition": 1704}   # GraphQL v2 takes a numeric game id for requirements
KEYWORDS = re.compile(r"(?i)\b(new game|existing saves?|mid[- ]?(?:save|playthrough)|clean save|uninstall\w*|"
                      r"ESL|ESPFE|VR|Skyrim ?VR|requires?|requirements|incompatib\w+|patch(?:es)?)\b")


def clean(t):
    t = html.unescape(t or "")
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"\[/?[a-zA-Z*]+(=[^\]]*)?\]", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def vkey(v):
    """Version sort key: '3.100.11' > '3.6.2' > '3.0.50b' > '3.0.50a'. Each dotted part is (numeric prefix,
    suffix); a leading v is ignored and trailing '.0' parts are dropped, so '6.4' == '6.4.0' == 'v6.4'."""
    parts = []
    for p in re.split(r"[.\-_ ]", str(v).strip().lstrip("vV")):
        m = re.match(r"(\d*)(.*)", p)
        parts.append((int(m.group(1)) if m.group(1) else -1, m.group(2).lower()))
    while len(parts) > 1 and parts[-1] == (0, ""):
        parts.pop()
    return parts


def s(v, default="?"):
    """A field that may be an explicit null."""
    return default if v is None else str(v)


def load_env(name, required=True):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        if required:
            raise ValueError("{} is empty".format(name))
        return None
    return json.loads(raw)


def print_changelogs(logs, since=None, newest=8):
    """Every version newer than `since` when given (newest first); otherwise the newest `newest`, with the count
    of what was not shown."""
    vers = sorted(logs, key=vkey, reverse=True)
    if since:
        shown = [v for v in vers if vkey(v) > vkey(since)]
        print("----- changelogs newer than {}: {} version(s) -----".format(since, len(shown)))
    else:
        shown = vers[:newest]
        print("----- changelogs (newest {} of {}; pass --since <installed version> to see everything since it) -----"
              .format(len(shown), len(vers)))
    for v in shown:
        print("  {}:".format(v))
        entries = logs[v] if isinstance(logs[v], list) else [logs[v]]
        for line in entries:
            print("    - {}".format(clean(s(line, ""))))
    if not logs:
        print("  (no changelog published on Nexus)")
    elif since and not shown:
        print("  (none newer than {} on Nexus)".format(since))


def cmd_find():
    d = load_env("R")
    result = discovery.parse(d, os.environ.get("NEXUS_DOMAIN", "skyrimspecialedition"),
                             os.environ.get("NEXUS_QUERY", "search"))
    if result["state"] == "unavailable":
        print("ERROR: Nexus search unavailable: {}".format("; ".join(result["reasons"])), file=sys.stderr)
        return 2
    for m in result["candidates"]:
        print("  [{:>6}] {}  v{}  by {}  updated {}  status={}".format(
            m["mod_id"], m["name"], s(m["version"]), s(m["author"]),
            s(m["updated_at"])[:10], s(m["status"])))
    print("  ({} match(es); 15 max -- ports, forks and rewrites share names: pick by author and page)".format(result["returned"]))
    print("  coverage={} total={} -- candidates only; installed state not checked".format(
        result["state"], s(result["total"])))
    for reason in result["reasons"]:
        print("  " + reason)
    return 0


def cmd_page(dom, mid, since, full):
    info = load_env("INFO")
    files = load_env("FILES")
    try:
        logs = None if os.environ.get("LOG_STATUS") == "failed" else (load_env("LOG", required=False) or {})
    except ValueError:
        logs = None
    if not info.get("name"):
        print("ERROR: no mod record for {} (hidden, removed, or the wrong id)".format(mid), file=sys.stderr)
        return 2
    print("=" * 66)
    print("Mod      : {}  [{}]  by {}".format(s(info.get("name")), mid, s(info.get("author"))))
    print("Latest   : {}   updated {}   status={}".format(s(info.get("version")), s(info.get("updated_time"), "")[:10],
                                                          s(info.get("status"))))
    file_list = [f for f in (files.get("files") or []) if isinstance(f, dict)]
    # The mod record's version can lag the files, and several MAIN files can target different runtimes.
    mains = sorted((x for x in file_list if x.get("category_name") == "MAIN"),
                   key=lambda x: x.get("uploaded_timestamp") or 0, reverse=True)
    if mains and s(mains[0].get("version")) != s(info.get("version")):
        print("           ! newest MAIN file is {} -- the header above is stale; check which MAIN file is for your "
              "runtime".format(s(mains[0].get("version"))))
    print("Page     : https://www.nexusmods.com/{}/mods/{}".format(dom, mid))
    print("Summary  : {}".format(clean(info.get("summary"))))

    print("----- Nexus requirements (author-declared) -----")
    try:
        req = load_env("REQ", required=False)
        nodes = req["data"]["mod"]["modRequirements"]["nexusRequirements"]["nodes"]
        if not isinstance(nodes, list):
            raise ValueError("requirements nodes are not a list")
        usable = [r for r in nodes if isinstance(r, dict) and isinstance(r.get("modName"), str) and r["modName"].strip()]
        partial = bool(req.get("errors")) or len(usable) != len(nodes)
        for r in usable:
            notes = " ".join(clean(r.get("notes")).split())[:160]
            print("  - {}{}".format(s(r.get("modName")), "  ({})".format(notes) if notes else ""))
        if partial:
            print("  (PARTIAL: could NOT fetch all requirements -- missing results are not none)")
        elif not nodes:
            print("  (none declared -- the description may still list some)")
    except (ValueError, KeyError, TypeError):
        print("  (could NOT fetch requirements -- not the same as none)")

    desc = clean(info.get("description"))
    lim = 10 ** 9 if full else 4000
    print("----- description ({} chars{}) -----".format(len(desc), "" if len(desc) <= lim else "; first 4000 -- --full for all"))
    print(desc[:lim] + ("\n  [...truncated -- re-run with --full]" if len(desc) > lim else ""))
    hits = sorted({w.lower() for w in KEYWORDS.findall(desc)})
    print("----- keyword hits in description: {}".format(", ".join(hits) or "(none)"))

    print("----- files (newest first, max 15) -----")
    fl = sorted(file_list, key=lambda x: x.get("uploaded_timestamp") or 0, reverse=True)
    for x in fl[:15]:
        print("  {:12} {:12} {}  {:>8} KB  {}".format(s(x.get("category_name")), s(x.get("version")),
                                                      s(x.get("uploaded_time"), "")[:10], x.get("size_kb") or 0,
                                                      s(x.get("name"))))
        fd = " ".join(clean(x.get("description")).split())      # where authors say "VR version" / "SE only"
        if fd:
            print("      > {}{}".format(fd[:300], "..." if len(fd) > 300 else ""))
    if not fl:
        print("  (no files listed)")
    if logs is None:
        print("----- changelogs: could NOT fetch or read them -- not the same as none -----")
    else:
        print_changelogs(logs, since)
    return 0


def fetch(url):
    """(status, tree or None). Never pretends to be a browser."""
    if not url:
        return "NO PREVIEW LINK in files.json", None
    if not url.lower().startswith("https://"):
        return "NOT FETCHED (not an https link: {})".format(url[:80]), None
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/%?=&"), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "LISTED", json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "NO PREVIEW PUBLISHED (404) -- the archive itself is needed", None
        if e.code == 403:
            return ("REFUSED THIS CLIENT (HTTP 403) -- not 'no preview'. The toolkit does not pretend to be a "
                    "browser; open the link in one: {}".format(url)), None
        return "FETCH FAILED (HTTP {})".format(e.code), None
    except (urllib.error.URLError, OSError, ValueError) as e:
        return "FETCH FAILED ({}: {})".format(type(e).__name__, str(e)[:60]), None


def walk(node, prefix=""):
    for c in node.get("children") or []:
        path = "{}/{}".format(prefix, s(c.get("name")))
        if c.get("type") == "directory":
            yield from walk(c, path)
        else:
            yield path, c.get("size") or ""


def cmd_contents(version):
    files = [f for f in (load_env("FILES").get("files") or []) if isinstance(f, dict)]
    keep = [f for f in files if f.get("category_name") in ("MAIN", "OPTIONAL", "UPDATE")
            or (version and s(f.get("version")) == version)]
    if version:
        keep = [f for f in keep if s(f.get("version")) == version]
    keep.sort(key=lambda f: f.get("uploaded_timestamp") or 0, reverse=True)
    if not keep:
        print("  (no MAIN/OPTIONAL/UPDATE files{})".format(" at version " + version if version else ""))
    for f in keep:
        print("-- {} {}  {}  (file_id {})".format(s(f.get("category_name")), s(f.get("version")), s(f.get("name")),
                                                  s(f.get("file_id"))))
        status, tree = fetch(f.get("content_preview_link") or "")
        if tree is None:
            print("     {}".format(status))
            continue
        entries = list(walk(tree))
        print("     {}: {} file(s)".format(status, len(entries)))
        for path, size in entries[:400]:
            print("     {}  {}".format(path, size))
        if len(entries) > 400:
            print("     ... {} more not shown".format(len(entries) - 400))
    return 0


def main(argv):
    args = list(argv)
    since = None
    if "--since" in args:
        i = args.index("--since")
        if i + 1 >= len(args):
            print("usage: --since needs a version", file=sys.stderr)
            return 2
        since = args[i + 1]
        del args[i:i + 2]
    full = "--full" in args
    args = [a for a in args if a != "--full"]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "find" and not rest:
            return cmd_find()
        if cmd == "page" and len(rest) == 2:
            return cmd_page(rest[0], rest[1], since, full)
        if cmd == "changelog" and not rest:
            print_changelogs(load_env("LOG"), since)
            return 0
        if cmd == "contents" and len(rest) <= 1:
            return cmd_contents(rest[0] if rest else None)
        if cmd == "gql-find" and len(rest) == 2:
            print(json.dumps(discovery.request(rest[1], rest[0], wildcard=True)))
            return 0
        if cmd == "gql-req" and len(rest) == 2 and rest[0].isdigit() and rest[1] in GAME_IDS:
            # The query text the dev install's tool uses, with the id inlined (it is all digits, checked above).
            print(json.dumps({"query": "{ mod(gameId:%d, modId:%d){ modRequirements{ nexusRequirements{ nodes{ "
                                       "modName notes } } } } }" % (GAME_IDS[rest[1]], int(rest[0]))}))
            return 0
    except (ValueError, AttributeError, TypeError) as e:
        print("ERROR: unparseable Nexus API response ({}: {})".format(type(e).__name__, e), file=sys.stderr)
        return 2
    print("usage: nexus-render.py find | page <domain> <modId> | changelog | contents [version] | gql-find | gql-req"
          "   (got {})".format(args), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    sys.exit(main(sys.argv[1:]))
