#!/usr/bin/env python3
"""Third-party version and integration-drift audit (Plan 8 T15b; a standing release gate).

    python scripts/thirdparty-audit.py [--repo DIR] [--offline] [--cache-dir DIR] [--max-age HOURS]
                                       [--only NAME[,NAME...]] [--json] [--list]

For every third-party tool, runtime, package, image and CI action the toolkit pins, recommends or
integrates with, it prints  pinned -> latest upstream  and runs the integration-drift checks that are
mechanical:

  * pin sites agree       every place that states a pin says the same thing (a declared pin site that
                          cannot be read is COULD NOT CHECK: the prose moved, the registry must follow);
  * install refs          every install command / version statement on a user-facing surface (README,
                          setup.sh, docs/, agent/, examples/, tools/, Dockerfile, workflows) equals the
                          pin file -- what a fresh user gets by following the README must BE the pinned
                          set (Plan 8 reproducibility ruling, item 4). An unpinned install line for a
                          pinned tool is drift too;
  * pinned version exists the pin is still published upstream (npm / PyPI / NuGet / a git tag);
  * tag -> commit         a tag named next to a commit SHA still points at that SHA (AutoMod, Actions);
  * upstream paths        a documented file the pinned release must ship (a zip member of a release
                          asset, a file at the pinned commit, a nupkg target-framework folder);
  * CLI flags / APIs      a documented flag or endpoint is still in the pinned upstream source / README.

Statuses:  OK | NEWER (upstream moved on; reported, never a failure) | UNPINNED (recommended, no pin;
reported) | NOT DELIVERED (nothing the toolkit ships installs it; the reason is printed) | DRIFT |
COULD NOT CHECK (always with a reason).
Exit:  1 = at least one DRIFT;  2 = no DRIFT but at least one row could not be checked;  0 = otherwise.
A run is never silently OK: a row nobody could check is printed and changes the exit code.

Network: GitHub (releases API + raw files + release downloads), PyPI, npm, NuGet, endoflife.date, the
.NET release index, MCR, Docker Hub, download.blender.org, and Nexus ONLY when X4_NEXUS_KEY is set. Keys
(X4_NEXUS_KEY, GITHUB_TOKEN / GH_TOKEN) go in request headers only; they are never printed, logged or
cached, and any echo of one in a response body is redacted before it is stored. Responses (2xx and 404)
are cached in --cache-dir; --offline reads only the cache (a miss is COULD NOT CHECK). An exhausted
GitHub rate limit stops further GitHub API calls and marks those rows COULD NOT CHECK.

Stdlib only. Read-only on the repo.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OK, NEWER, UNPINNED, DRIFT, UNCHECKED = "OK", "NEWER", "UNPINNED", "DRIFT", "COULD NOT CHECK"
OUTSIDE = "NOT DELIVERED"   # nothing the toolkit ships installs it; the row says why (reported, never a failure)
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "thirdparty-audit-cache"
USER_AGENT = "skyrim-ai-assistant-toolkit-thirdparty-audit/1"

# User-facing instruction surfaces: what a fresh user (or their agent) reads and follows. agent/ is the source of
# the generated .claude/ and .agents/ copies (gen_agent_trees --check keeps those identical), so it is read once.
SURFACES = ("README.md", "SETUP_PROMPT.txt", "setup.sh", "KNOWLEDGEBASE.md", "ADAPTING.md", "docs/*.md",
            "agent/**/*.md", "examples/*", "tools/*.sh", "tools/*.py", ".devcontainer/*", ".github/workflows/*.yml")
SURFACE_EXCLUDES = ("docs/superpowers/*",)
# A mention that only RECORDS history ("releases before 29.x kept it at ...", "was 0.40.0") is not an instruction.
# Matched in the text before the reference on its own line (and the line before, when that one ends with ':').
HISTORY = re.compile(r"(?i)\b(was|were|before|older|earlier|previous(?:ly)?|no longer|end-of-life|eol|used to|"
                     r"legacy|until|instead of|v3|3\.x|formerly|prior)\b")
COMMENT_SUFFIXES = (".sh", ".py", ".ps1", ".yml", ".yaml", ".toml")


class CouldNotCheck(Exception):
    """A row could not be decided; the message is the reason printed in the table."""


# ----------------------------------------------------------------------------------------------- fetching

class Response:
    def __init__(self, status, body, headers=None):
        self.status, self.body, self.headers = status, body, {k.lower(): v for k, v in (headers or {}).items()}

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError as exc:
            raise CouldNotCheck("unparseable JSON response (%s)" % exc) from None

    def text(self):
        return self.body.decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _resolve_redirects(url, headers, timeout):
    """Follow redirects with HEAD and no Range: github.com answers a ranged GET on a release download with 501."""
    opener = urllib.request.build_opener(_NoRedirect)
    plain = {k: v for k, v in headers.items() if k.lower() != "range"}
    for _hop in range(6):
        try:
            with opener.open(urllib.request.Request(url, headers=plain, method="HEAD"), timeout=timeout):
                return url
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                url = urllib.parse.urljoin(url, exc.headers["Location"])
                continue
            return url
    return url


def _urllib_transport(url, headers, secret_headers, timeout=25):
    if "Range" in headers and not secret_headers:
        url = _resolve_redirects(url, headers, timeout)
    req = urllib.request.Request(url, headers=headers)
    for k, v in secret_headers.items():
        req.add_unredirected_header(k, v)   # never forwarded to a redirect target
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except OSError:
            pass
        return Response(exc.code, body, dict(exc.headers or {}))


class Fetcher:
    """GET with a response cache, an offline mode, per-host pacing and rate-limit awareness."""

    KEPT_HEADERS = ("content-range", "x-ratelimit-remaining", "x-ratelimit-reset", "retry-after")

    def __init__(self, cache_dir, offline=False, max_age=6 * 3600, transport=None, env=None, pace=0.25,
                 sleep=time.sleep, now=time.time):
        self.cache_dir, self.offline, self.max_age = Path(cache_dir), offline, max_age
        self.transport = transport or _urllib_transport
        env = os.environ if env is None else env
        self.nexus_key = env.get("X4_NEXUS_KEY") or ""
        self.github_token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or ""
        self.secrets = [s for s in (self.nexus_key, self.github_token) if s]
        self.pace, self.sleep, self.now = pace, sleep, now
        self.last_call, self.exhausted = {}, {}
        self.calls = 0

    def _key(self, url, byte_range):
        return hashlib.sha256((url + "|" + (byte_range or "")).encode("utf-8")).hexdigest()[:40]

    def redact(self, data):
        for s in self.secrets:
            data = data.replace(s.encode("utf-8"), b"<redacted>")
        return data

    def cache_path(self, url, byte_range=None):
        return self.cache_dir / (self._key(url, byte_range) + ".json")

    def put(self, url, status, body, headers=None, byte_range=None, fetched=None):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        rec = {"url": url, "range": byte_range or "", "status": status, "fetched": fetched or self.now(),
               "headers": {k: v for k, v in (headers or {}).items() if k.lower() in self.KEPT_HEADERS},
               "body": self.redact(body).decode("latin-1")}
        tmp = self.cache_path(url, byte_range).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, self.cache_path(url, byte_range))

    def _cached(self, url, byte_range):
        p = self.cache_path(url, byte_range)
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        return Response(rec["status"], rec["body"].encode("latin-1"), rec.get("headers")), rec.get("fetched", 0)

    def get(self, url, byte_range=None, accept=None):
        cached, fetched = self._cached(url, byte_range)
        if cached is not None and (self.offline or self.now() - fetched <= self.max_age):
            return cached
        if self.offline:
            raise CouldNotCheck("offline and not cached: " + url)
        host = url.split("/")[2]
        if host in self.exhausted:
            raise CouldNotCheck("%s rate limit exhausted (resets %s); set GITHUB_TOKEN or retry later"
                                % (host, self.exhausted[host]))
        if host == "api.nexusmods.com" and not self.nexus_key:
            raise CouldNotCheck("Nexus needs X4_NEXUS_KEY (not set)")
        headers = {"User-Agent": USER_AGENT}
        if accept:
            headers["Accept"] = accept
        if byte_range:
            headers["Range"] = "bytes=" + byte_range
        secret = {}
        if host == "api.github.com" and self.github_token:
            secret["Authorization"] = "Bearer " + self.github_token
        if host == "api.nexusmods.com":
            secret["apikey"] = self.nexus_key
        resp = None
        for attempt in (1, 2):
            wait = self.pace - (self.now() - self.last_call.get(host, 0))
            if wait > 0:
                self.sleep(wait)
            self.last_call[host] = self.now()
            self.calls += 1
            try:
                resp = self.transport(url, headers, secret)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                reason = getattr(exc, "reason", exc)
                if attempt == 2:
                    raise CouldNotCheck("unreachable: %s (%s)" % (host, self._clean(str(reason)))) from None
                self.sleep(2)
                continue
            if resp.status in (403, 429) and (resp.headers.get("x-ratelimit-remaining") == "0"
                                              or resp.status == 429):
                reset = resp.headers.get("x-ratelimit-reset", "")
                try:
                    reset = time.strftime("%H:%M", time.localtime(int(reset)))
                except ValueError:
                    reset = resp.headers.get("retry-after", "?") + "s"
                self.exhausted[host] = reset
                raise CouldNotCheck("%s rate limit exhausted (resets %s); set GITHUB_TOKEN or retry later"
                                    % (host, reset))
            if resp.status >= 500 and attempt == 1:
                self.sleep(2)
                continue
            break
        if resp.status in (200, 206, 404):
            self.put(url, resp.status, resp.body, resp.headers, byte_range)
            return Response(resp.status, self.redact(resp.body), resp.headers)
        raise CouldNotCheck("HTTP %d from %s" % (resp.status, host))

    def _clean(self, text):
        for s in self.secrets:
            text = text.replace(s, "<redacted>")
        return text

    def get_ok(self, url, **kw):
        resp = self.get(url, **kw)
        if resp.status == 404:
            raise CouldNotCheck("404 Not Found: " + url)
        return resp


# ------------------------------------------------------------------------------------------- upstreams

def gh_api(f, path):
    return f.get_ok("https://api.github.com/" + path, accept="application/vnd.github+json").json()


def gh_latest(f, repo, prerelease=False):
    """The tag a user downloading 'the latest release' gets (prerelease=True: newest release of any kind)."""
    if not prerelease:
        resp = f.get("https://api.github.com/repos/%s/releases/latest" % repo, accept="application/vnd.github+json")
        if resp.status == 200:
            return resp.json()["tag_name"]
    rels = gh_api(f, "repos/%s/releases?per_page=10" % repo)
    rels = [r for r in rels if not r.get("draft")]
    if rels:
        return rels[0]["tag_name"]
    tags = gh_api(f, "repos/%s/tags?per_page=1" % repo)
    if tags:
        return tags[0]["name"]
    raise CouldNotCheck("no releases or tags in " + repo)


def gh_release_assets(f, repo, tag):
    return [a["name"] for a in gh_api(f, "repos/%s/releases/tags/%s" % (repo, tag)).get("assets", [])]


def gh_tag_commit(f, repo, tag):
    resp = f.get("https://api.github.com/repos/%s/git/ref/tags/%s" % (repo, tag), accept="application/vnd.github+json")
    if resp.status == 404:
        return None
    obj = resp.json()["object"]
    if obj["type"] == "tag":   # annotated tag: dereference
        obj = gh_api(f, "repos/%s/git/tags/%s" % (repo, obj["sha"]))["object"]
    return obj["sha"]


def gh_path_exists(f, repo, ref, path):
    """File OR directory at a ref (the contents API answers both; raw.githubusercontent.com only files)."""
    resp = f.get("https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, ref),
                 accept="application/vnd.github+json")
    return resp.status == 200


def gh_raw(f, repo, ref, path, probe=False):
    resp = f.get("https://raw.githubusercontent.com/%s/%s/%s" % (repo, ref, path), byte_range="0-0" if probe else None)
    if resp.status == 404:
        return None
    return resp.text()


def pypi_latest(f, name):
    return f.get_ok("https://pypi.org/pypi/%s/json" % name).json()["info"]["version"]


def pypi_has(f, name, version):
    return f.get("https://pypi.org/pypi/%s/%s/json" % (name, version)).status == 200


def npm_latest(f, name):
    return f.get_ok("https://registry.npmjs.org/%s/latest" % name).json()["version"]


def npm_has(f, name, version):
    return f.get("https://registry.npmjs.org/%s/%s" % (name, version)).status == 200


def nuget_versions(f, pkg):
    return f.get_ok("https://api.nuget.org/v3-flatcontainer/%s/index.json" % pkg.lower()).json()["versions"]


def nuget_latest(f, pkg):
    stable = [v for v in nuget_versions(f, pkg) if "-" not in v]
    if not stable:
        raise CouldNotCheck("no stable version of " + pkg)
    return max(stable, key=vkey)


def nexus_version(f, game, mod_id):
    return str(f.get_ok("https://api.nexusmods.com/v1/games/%s/mods/%s.json" % (game, mod_id)).json()["version"])


def nexus_file_latest(f, game, mod_id):
    """Newest version among a mod's MAIN files. Some pages never bump the mod-level version (ReSaver: page 6.0.636,
    newest MAIN file 6.0.643), and the file a user downloads is what counts."""
    files = f.get_ok("https://api.nexusmods.com/v1/games/%s/mods/%s/files.json" % (game, mod_id)).json().get("files", [])
    main = [str(x.get("version")) for x in files if x.get("category_name") == "MAIN" and x.get("version")]
    if not main:
        raise CouldNotCheck("no MAIN file listed for %s/%s" % (game, mod_id))
    return max(main, key=vkey)


def eol_latest(f, product, cycle):
    return f.get_ok("https://endoflife.date/api/%s/%s.json" % (product, cycle)).json()["latest"]


def dotnet_channel(f, channel):
    idx = f.get_ok("https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/releases-index.json").json()
    for rel in idx["releases-index"]:
        if rel["channel-version"] == channel:
            return rel["latest-sdk"], rel.get("support-phase", "?")
    raise CouldNotCheck(".NET channel %s is not in the release index" % channel)


def mcr_latest(f, repo, pattern):
    tags = f.get_ok("https://mcr.microsoft.com/v2/%s/tags/list" % repo).json().get("tags", [])
    rx = re.compile(pattern)
    hits = [t for t in tags if rx.fullmatch(t)]
    if not hits:
        raise CouldNotCheck("no MCR tag of %s matches %s" % (repo, pattern))
    return max(hits, key=vkey)


def dockerhub_latest(f, repo):
    data = f.get_ok("https://hub.docker.com/v2/repositories/%s/tags?page_size=100&ordering=last_updated" % repo).json()
    tags = [r["name"] for r in data.get("results", []) if re.fullmatch(r"\d+\.\d+(\.\d+)?", r["name"])]
    if not tags:
        raise CouldNotCheck("no numeric tag of " + repo)
    return max(tags, key=vkey)


def blender_latest(f, line):
    html = f.get_ok("https://download.blender.org/release/Blender%s/" % line).text()
    found = set(re.findall(r"blender-(%s\.\d+)" % re.escape(line), html))
    if not found:
        raise CouldNotCheck("no Blender %s.x build listed" % line)
    return max(found, key=vkey)


def zip_names(f, url):
    """Names in a remote zip, from its central directory, read with HTTP Range (no full download)."""
    # Explicit ranges only: GitHub's release-asset host answers a suffix range (bytes=-N) with 501.
    head = f.get(url, byte_range="0-0")
    if head.status == 404:
        return None
    if head.status == 206:
        m = re.match(r"bytes \d+-\d+/(\d+)", head.headers.get("content-range", ""))
        if not m:
            raise CouldNotCheck("partial response without a total size: " + url)
        size = int(m.group(1))
        start = max(0, size - 66000)
        tail = f.get(url, byte_range="%d-%d" % (start, size - 1))
        if tail.status != 206:
            raise CouldNotCheck("the server ignored the tail range of " + url)
        buf = tail.body
    else:   # the server sent the whole (small) file
        buf, start = head.body, 0
    at = buf.rfind(b"PK\x05\x06")
    if at < 0 or at + 22 > len(buf):
        raise CouldNotCheck("no zip end-of-central-directory record in " + url)
    _d, _cd, _n, total, cd_size, cd_off, _c = struct.unpack("<HHHHIIH", buf[at + 4:at + 22])
    if cd_off == 0xFFFFFFFF or total == 0xFFFF:
        raise CouldNotCheck("zip64 archive not supported: " + url)
    rel = cd_off - start
    if 0 <= rel and rel + cd_size <= len(buf):
        cd = buf[rel:rel + cd_size]
    else:
        part = f.get(url, byte_range="%d-%d" % (cd_off, cd_off + cd_size - 1))
        cd = part.body if part.status == 206 else part.body[cd_off:cd_off + cd_size]
    names, p = [], 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b"PK\x01\x02":
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        names.append(cd[p + 46:p + 46 + nlen].decode("utf-8", "replace"))
        p += 46 + nlen + elen + clen
    if len(names) != total:
        raise CouldNotCheck("zip central directory lists %d of %d entries: %s" % (len(names), total, url))
    return names


# -------------------------------------------------------------------------------------------- versions

def norm(v):
    v = str(v).strip()
    v = re.sub(r"^(?:rust-|release-|xedit-|pyffi-)", "", v)
    v = re.sub(r"^[vV](?=\d)", "", v)
    # Git for Windows tags v2.55.0.windows.5 and names that installer Git-2.55.0.5-64-bit.exe (.windows.1 -> no
    # fourth part): a tag compares in the installer's form, which is what the pin records.
    m = re.fullmatch(r"(\d+\.\d+\.\d+)\.windows\.(\d+)", v)
    return (m.group(1) + ("" if m.group(2) == "1" else "." + m.group(2))) if m else v


def vkey(v):
    m = re.match(r"\d+(?:\.\d+)*", norm(v))
    nums = tuple(int(x) for x in m.group(0).split(".")) if m else ()
    return nums + (0,) * (4 - len(nums)) if len(nums) < 4 else nums


def same(a, b, compare="exact"):
    a, b = norm(a), norm(b)
    if compare == "major":
        return vkey(a)[:1] == vkey(b)[:1]
    if compare == "minor":
        return vkey(a)[:2] == vkey(b)[:2]
    if compare == "prefix":
        return a.lower().startswith(b.lower()) or b.lower().startswith(a.lower())
    return a == b


def version_status(pinned, latest):
    """OK when equal, NEWER when upstream moved on; a pin AHEAD of upstream is DRIFT (a user cannot get it)."""
    if same(pinned, latest):
        return OK, ""
    if vkey(latest) > vkey(pinned):
        return NEWER, "upstream moved on (reported, not a failure)"
    if vkey(latest) == vkey(pinned):
        return NEWER, "upstream tag differs only in suffix"
    return DRIFT, "pin is ahead of upstream latest -- not published?"


# ----------------------------------------------------------------------------------------------- repo

class Repo:
    def __init__(self, root):
        self.root = Path(root)
        self._surfaces = None

    def read(self, rel):
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def surfaces(self):
        if self._surfaces is None:
            seen = []
            for pattern in SURFACES:
                for p in sorted(self.root.glob(pattern)):
                    rel = p.relative_to(self.root).as_posix()
                    if p.is_file() and rel not in seen and not any(fnmatch.fnmatch(rel, x) for x in SURFACE_EXCLUDES):
                        seen.append(rel)
            self._surfaces = seen
        return self._surfaces


def read_pin_site(repo, site):
    """One pin site -> (label, value or None). value None = the site could not be read."""
    kind, rel = site[0], site[1]
    label = rel
    if kind == "dirs":
        found = sorted({p.name for p in repo.root.glob(rel) if p.is_dir() and re.fullmatch(r"\d+(\.\d+)+", p.name)},
                       key=vkey)
        return label, (found[-1] if found else None)
    text = repo.read(rel)
    if text is None:
        return label, None
    if kind == "text":
        return label, text.strip().splitlines()[0].strip() if text.strip() else None
    if kind == "json":
        try:
            node = json.loads(text)
            for k in site[2]:
                node = node[k]
            return label, str(node)
        except (ValueError, KeyError, TypeError):
            return label, None
    if kind == "regex":
        vals = sorted(set(m.group("v") for m in re.finditer(site[2], text)))
        if len(vals) > 1:
            return label + " (disagrees with itself: %s)" % ", ".join(vals), vals[0]
        return label, (vals[0] if vals else None)
    raise ValueError("unknown pin site kind " + kind)


HISTORY_WINDOW = 30


def history_context(lines, idx, start):
    """The text a history marker must sit in to make a reference historical: the last HISTORY_WINDOW characters
    before it on its own line, cut at a ';' (a new clause), plus the line before when that line ends with ':'.
    Kept this narrow on purpose -- every skipped mention is a reference nobody compares with the pin."""
    before = lines[idx][:start][-HISTORY_WINDOW:].rsplit(";", 1)[-1]
    if idx > 0 and lines[idx - 1].rstrip().endswith(":"):
        before = lines[idx - 1] + " " + before
    return before


def scan_refs(repo, ref):
    """Yield (file, line no, version-or-None, matched text) for every instruction a ref pattern finds."""
    rx = re.compile(ref["pattern"])
    vrx = re.compile(ref["version"]) if ref.get("version") else None
    need = re.compile(ref["line"]) if ref.get("line") else None
    files = ref.get("files") or repo.surfaces()
    for rel in files:
        text = repo.read(rel)
        if text is None:
            continue
        lines = text.splitlines()
        code = rel.endswith(COMMENT_SUFFIXES) or rel.endswith("Dockerfile")
        for i, line in enumerate(lines):
            if code and line.lstrip().startswith("#"):
                continue
            if need and not need.search(line):
                continue
            for m in rx.finditer(line):
                if HISTORY.search(history_context(lines, i, m.start())):
                    yield rel, i + 1, "history", m.group(0)
                    continue
                if vrx is None:
                    yield rel, i + 1, m.group("v"), m.group(0)
                    continue
                vm = vrx.search(m.group("tail"))
                yield rel, i + 1, (vm.group("v") if vm else None), m.group(0)


# ------------------------------------------------------------------------------------------- registry
# The floor is toolkit-thirdparty-audit-2026-10-05.md; the dynamic sections (package.json, requirements.txt,
# workflow `uses:`) enumerate themselves from the repo at run time. Pins are never written here: a pin is READ
# from the pin file / pin site named in `pins`, so a bump in the repo is seen without touching this file.

NODE_REFS = [
    {"pattern": r"setup_(?P<v>\d+)\.x", "compare": "major"},
    {"pattern": r"NODE_VERSION=(?P<v>\d+\.\d+\.\d+)"},
    {"pattern": r"OpenJS\.NodeJS(?:\.LTS)? --exact --version[ =]+(?P<v>\d+\.\d+\.\d+)"},
    {"pattern": r"\"node\":\s*\">=\s*(?P<v>\d+)", "compare": "major", "files": ["package.json"]},
    {"pattern": r"\bNode(?:\.js)? (?P<v>\d{2})\b(?!\.\d)", "compare": "major"},
    {"pattern": r"\bNode(?:\.js)? \*?\*?(?P<v>\d+\.\d+\.\d+)"},
    {"pattern": r"node-v(?P<v>\d+\.\d+\.\d+)-"},
]
DOTNET_REFS = [
    {"pattern": r"\.NET (?P<v>\d+)(?:\.0)? (?:SDK|runtime)", "compare": "major"},
    {"pattern": r"Microsoft\.DotNet\.SDK\.(?P<v>\d+)", "compare": "major"},
    {"pattern": r"--channel (?P<v>\d+)\.\d", "compare": "major"},
    {"pattern": r"dotnet-version:\s*[\"']?(?P<v>\d+)\.", "compare": "major"},
    # The exact SDK every install hint names, and the variable toolchain-check fills its hint from.
    {"pattern": r"Microsoft\.DotNet\.SDK\.\d+ --exact --version[ =]+(?P<v>[\d$%{][^\s`'\")]*)"},
    {"pattern": r"SDK_PIN=(?P<v>\d+\.\d+\.\d+)", "files": ["tools/toolchain-check.sh"]},
    {"pattern": r"\.NET SDK (?P<v>\d+\.\d+\.\d+)"},
]
# A download pinned in docs/installing-tools.md's *Pinned downloads* table: | Tool | Version | File | SHA-256 | From |
PINNED_DOWNLOADS = "docs/installing-tools.md"


def table_row(tool):
    """Pin-site regex for one row of the Pinned downloads table (named groups: v, sha, url)."""
    return (r"(?m)^\| " + re.escape(tool) + r" \| (?P<v>[\w.]+) \| [^|]+\| `(?P<sha>[0-9a-f]{64})` \| "
            r"<?(?P<url>https://[^>|\s]+|[^|]+?)>? \|")


def hashes(tool, published=None):
    """The `hashes` check for one table row: published=URL template (fields from the row) whose text must contain
    the pinned hash, or None to download the file itself and hash it (small assets only)."""
    return ("hashes", {"items": [{"label": tool, "file": PINNED_DOWNLOADS, "pattern": table_row(tool),
                                  "published": published}]})

REGISTRY = [
    {"name": "House Carl", "pins": [("regex", PINNED_DOWNLOADS, table_row("House Carl")),
                                    ("regex", "tools/housecarl_integration/__init__.py", r'UPSTREAM_VERSION = "(?P<v>\d+\.\d+\.\d+)"')],
     "upstream": ("github", "Avick3110/houseCARL"),
     "checks": [hashes("House Carl", "https://api.github.com/repos/Avick3110/houseCARL/releases/tags/v{v}")]},
    {"name": "House Carl MCP SDK", "pins": [("regex", "tools/housecarl-requirements.txt", r"(?m)^mcp==(?P<v>\d+\.\d+\.\d+)"),
                                             ("regex", "tools/housecarl_integration/__init__.py", r'MCP_VERSION = "(?P<v>\d+\.\d+\.\d+)"'),
                                             ("regex", "tools/housecarl-requirements.lock", r"(?m)^mcp==(?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("pypi", "mcp")},
    {"name": "House Carl TOML support", "pins": [("regex", "tools/housecarl-requirements.txt", r"(?m)^tomlkit==(?P<v>\d+\.\d+\.\d+)"),
                                                  ("regex", "tools/housecarl_integration/__init__.py", r'TOMLKIT_VERSION = "(?P<v>\d+\.\d+\.\d+)"'),
                                                  ("regex", "tools/housecarl-requirements.lock", r"(?m)^tomlkit==(?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("pypi", "tomlkit")},
    {"name": "Spriggit", "pins": [("json", ".config/dotnet-tools.json", ["tools", "spriggit.cli", "version"])],
     "upstream": ("nuget", "spriggit.cli"),
     "refs": [{"pattern": r"dotnet tool install\b[^\n`\"]*?Spriggit\.CLI\b(?P<tail>[^\n`\"]*)",
               "version": r"--version[ =]+(?P<v>[0-9$%{][^\s`\"';)]*)", "unpinned": "Spriggit.CLI install without --version"},
              {"pattern": r"--PackageVersion[ =]+[\"']?(?P<v>\d+\.\d+\.\d+)"}],
     "checks": [("cli_flags", {"repo": "Mutagen-Modding/Spriggit", "line": r"(?i)spriggit",
                               "sources": ["Spriggit.Core/Commands/SerializeCommand.cs",
                                           "Spriggit.Core/Commands/DeserializeCommand.cs"]}),
                ("nupkg_tfm", {"package": "spriggit.cli", "runtime": ".NET SDK"})]},
    {"name": ".NET SDK", "pins": [("regex", ".devcontainer/Dockerfile", r"DOTNET_SDK_VERSION=(?P<v>\d+\.\d+\.\d+)"),
                                  ("regex", ".github/workflows/ci.yml", r"dotnet-version:\s*[\"']?(?P<v>\d+\.\d+)")],
     "compare": "minor", "upstream": ("dotnet", None), "refs": DOTNET_REFS,
     "checks": [("hashes", {"items": [
         {"label": ".NET SDK " + arch, "file": ".devcontainer/Dockerfile",
          "pattern": r"DOTNET_SDK_VERSION=(?P<v>(?P<ch>\d+\.\d+)\.\d+)[\s\S]*?DOTNET_SHA512_%s=(?P<sha>[0-9a-f]{128})" % arch,
          "published": "https://builds.dotnet.microsoft.com/dotnet/release-metadata/{ch}/releases.json"}
         for arch in ("AMD64", "ARM64")]})]},
    {"name": "Node.js", "pins": [("text", ".node-version")], "upstream": ("eol", "nodejs", "major"), "refs": NODE_REFS,
     "checks": [("hashes", {"items": [
         {"label": "Node.js " + arch, "file": ".devcontainer/Dockerfile",
          "pattern": r"NODE_VERSION=(?P<v>\d+\.\d+\.\d+)[\s\S]*?NODE_SHA256_%s=(?P<sha>[0-9a-f]{64})" % arch,
          "published": "https://nodejs.org/dist/v{v}/SHASUMS256.txt"} for arch in ("AMD64", "ARM64")]})]},
    {"name": "Python", "pins": [("text", ".python-version")], "upstream": ("eol", "python", "minor"),
     "refs": [{"pattern": r"devcontainers/python:[\d.]+-(?P<v>\d+\.\d+)-", "compare": "minor"}]},
    {"name": "AutoMod", "pins": [("regex", "tools/automod-cli.sh", r"PIN_COMMIT=\"(?P<v>[0-9a-f]{40})\"")],
     "tag_pins": [("regex", "tools/automod-cli.sh", r"AutoMod (?P<v>v\d+\.\d+\.\d+) = commit")],
     "upstream": ("github", "SpookyPirate/spookys-automod-toolkit"),
     "refs": [{"pattern": r"(?:--branch|at tag) (?P<v>v\d+\.\d+\.\d+)", "line": r"(?i)automod", "against": "tag"},
              {"pattern": r"commit `?(?P<v>[0-9a-f]{7,40})\b", "line": r"(?i)automod", "compare": "prefix"},
              {"pattern": r"git clone\b(?P<tail>[^\n`\"]*spookys-automod-toolkit[^\n`\"]*)",
               "version": r"--branch (?P<v>[^\s`\"']+)", "unpinned": "AutoMod clone without --branch", "against": "tag"}],
     "checks": [("tag_commit", {"repo": "SpookyPirate/spookys-automod-toolkit"}),
                ("repo_paths", {"repo": "SpookyPirate/spookys-automod-toolkit", "files": ["tools/automod-cli.sh"],
                                "pattern": r"(?P<p>src/SpookysAutomod\.[A-Za-z]+(?:/[A-Za-z.]+\.csproj)?|Directory\.Build\.props)"})]},
    {"name": "PyNifly", "pins": [("regex", "docs/installing-tools.md", r"verified with:? (?P<v>\d+\.\d+\.\d+)"),
                                 ("regex", "agent/skills/skyrim-nif/SKILL.md", r"Verified on \*\*PyNifly (?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("github", "BadDogSkyrim/PyNifly"),
     "newer_note": "29.1.0 was re-validated and rejected (2026-10-06): its Blender add-on strips effect shaders on a "
                   "round trip when the texture is not beside the NIF; re-validate any newer release the same way",
     "refs": [{"pattern": r"(?m)^\| PyNifly \| (?P<v>\d+\.\d+\.\d+) \|", "files": [PINNED_DOWNLOADS]},
              {"pattern": r"PyNifly/releases/download/V(?P<v>\d+\.\d+\.\d+)/io_scene_nifly\.zip", "line": r"^\| PyNifly \|"}],
     "checks": [("follows_latest", {"pattern": r"(?i)latest release[^\n]*PyNifly|PyNifly[^\n]*latest release"}),
                hashes("PyNifly", "https://api.github.com/repos/BadDogSkyrim/PyNifly/releases/tags/V{v}"),
                hashes("PyNifly Blender add-on", "https://api.github.com/repos/BadDogSkyrim/PyNifly/releases/tags/V{v}"),
                ("zip_paths", {"repo": "BadDogSkyrim/PyNifly", "asset": "io_scene_nifly.zip", "tag_format": "V{v}",
                               "pattern": r"(?<![\w/])(?:tools/pynifly/)?(?P<p>io_scene_nifly/[\w./-]+\.(?:dll|py))"})]},
    {"name": "DevBench", "pins": [("regex", "README.md", r"newest release the docs were read against is \*\*(?P<v>\d+\.\d+\.\d+)"),
                                  ("regex", "agent/skills/devbench/SKILL.md", r"unchanged in (?P<v>\d+\.\d+\.\d+) per the")],
     "upstream": ("github", "alandtse/devbench"), "nexus": ("skyrimspecialedition", 181326),
     "checks": [("upstream_text", {"repo": "alandtse/devbench", "path": "README.md", "files": ["tools/devbench-cli.sh"],
                                   "pattern": r"(?P<p>/api/(?:health|tools?)\b/?)"})]},
    {"name": "VR Address Library", "pins": [("regex", "README.md", r"newest seen: (?P<v>\d+\.\d+\.\d+)"),
                                            ("regex", "agent/skills/devbench/SKILL.md", r"VR Address Library seen when this was written is \*\*(?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("nexus", "skyrimspecialedition", 58101)},
    {"name": "CrashLoggerSSE", "upstream": ("github", "alandtse/CrashLoggerSSE"), "nexus": ("skyrimspecialedition", 59818),
     "not_delivered": "a game mod the user installs with their own mod manager; the toolkit never installs or names a version of it; crash-triage reads the dumps of whatever version is installed"},
    {"name": "SKSE VR", "upstream": ("nexus", "skyrimspecialedition", 30457), "not_delivered": "a game mod the user installs with their own mod manager; the toolkit never installs or names a version of it"},
    {"name": "Champollion", "pins": [("regex", PINNED_DOWNLOADS, table_row("Champollion"))],
     "upstream": ("github", "Orvid/Champollion"),
     "refs": [{"pattern": r"Champollion[. ](?P<v>v\d+\.\d+\.\d+)"}],
     "checks": [hashes("Champollion")]},
    {"name": "Caprica", "pins": [("regex", PINNED_DOWNLOADS, table_row("Caprica"))], "upstream": ("github", "Orvid/Caprica"),
     "refs": [{"pattern": r"Caprica[. ](?P<v>v\d+\.\d+\.\d+)"}],
     "checks": [hashes("Caprica")]},
    {"name": "xeditlib", "pins": [("regex", "package.json", r"xeditlib/tar\.gz/(?P<v>[0-9a-f]{40})"),
                                  ("regex", "package-lock.json", r"xeditlib/tar\.gz/(?P<v>[0-9a-f]{40})")],
     "tag_pins": [("regex", PINNED_DOWNLOADS, r"xeditlib (?P<v>v\d+\.\d+\.\d+) = commit")],
     "upstream": ("github", "WingedGuardian/xeditlib"),
     "refs": [{"pattern": r"WingedGuardian/xeditlib#(?P<v>[0-9a-f]{40})"},
              {"pattern": r"(?:xeditlib/tar\.gz/|commit `)(?P<v>[0-9a-f]{7,40})", "line": r"xeditlib",
               "compare": "prefix"},
              {"pattern": r"\bnpm (?:install|i)\b(?P<tail>[^\n`\"']*WingedGuardian/xeditlib[^\n`\"']*)",
               "version": r"xeditlib#(?P<v>[0-9a-f]{40})", "unpinned": "xeditlib install without #<commit>"}],
     "checks": [("tag_commit", {"repo": "WingedGuardian/xeditlib"})]},
    {"name": "zEdit", "upstream": ("github", "z-edit/zedit"),
     "not_delivered": "credited only: XEditLib.dll reaches users inside the pinned xeditlib commit, not from zEdit"},
    {"name": "xEdit (SSEEdit)", "upstream": ("github", "TES5Edit/TES5Edit"),
     "not_delivered": "the user's own modding tool; the toolkit uses XEditLib.dll from the pinned xeditlib, never xEdit"},
    {"name": "koffi", "pins": [("json", "package.json", ["optionalDependencies", "koffi"]),
                               ("json", "package-lock.json", ["packages", "node_modules/koffi", "version"])],
     "upstream": ("npm", "koffi"),
     "refs": [{"pattern": r"\bkoffi@(?P<v>\d+\.\d+\.\d+)"}, {"pattern": r"\bkoffi (?P<v>\d+\.\d+\.\d+)\b"}]},
    {"name": "PyFFI", "pins": [("regex", "tools/requirements-tools.txt", r"(?m)^PyFFI==(?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("pypi", "PyFFI")},
    {"name": "ReSaver (FallrimTools)", "pins": [("regex", PINNED_DOWNLOADS, table_row("ReSaver"))],
     "upstream": ("nexus-file", "skyrimspecialedition", 5031),
     "refs": [{"pattern": r"ReSaver \*?\*?(?P<v>6\.\d+\.\d+)"}]},
    {"name": "JDK (recommended LTS)", "pins": [("regex", "docs/installing-tools.md", r"JDK (?P<v>\d+) LTS recommended")],
     "upstream": ("eol", "eclipse-temurin", "major"), "refs": [{"pattern": r"JDK (?P<v>\d+) LTS recommended", "compare": "major"}]},
    {"name": "Microsoft OpenJDK", "pins": [("regex", ".devcontainer/Dockerfile", r"JDK_VERSION=(?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("eol", "microsoft-build-of-openjdk", "major"),
     "refs": [{"pattern": r"Microsoft\.OpenJDK\.\d+ --exact --version[ =]+(?P<v>\d+\.\d+\.\d+)", "compare": "prefix"},
              {"pattern": r"microsoft-jdk-(?P<v>\d+\.\d+\.\d+)-linux", "files": [".devcontainer/Dockerfile"]},
              # A JDK version a doc states (exact anywhere; a bare major in the doc that describes the image) must
              # be the image's (review I5: "JDK 17 is in the image" outlived the move to 21).
              {"pattern": r"\b(?:Open)?JDK (?P<v>\d+\.\d+\.\d+)\b"},
              {"pattern": r"\b(?:Open)?JDK (?P<v>\d{2})\b(?![+.\d])", "compare": "major",
               "files": ["docs/container-vs-windows.md"]}],
     "checks": [("hashes", {"items": [
         {"label": "JDK amd64", "file": ".devcontainer/Dockerfile",
          "pattern": r"JDK_VERSION=(?P<v>[\d.]+)[\s\S]*?JDK_SHA256_AMD64=(?P<sha>[0-9a-f]{64})",
          "published": "https://aka.ms/download-jdk/microsoft-jdk-{v}-linux-x64.tar.gz.sha256sum.txt"},
         {"label": "JDK arm64", "file": ".devcontainer/Dockerfile",
          "pattern": r"JDK_VERSION=(?P<v>[\d.]+)[\s\S]*?JDK_SHA256_ARM64=(?P<sha>[0-9a-f]{64})",
          "published": "https://aka.ms/download-jdk/microsoft-jdk-{v}-linux-aarch64.tar.gz.sha256sum.txt"}]})]},
    {"name": "Python (Windows install)",
     "pins": [("regex", "README.md", r"Python\.Python\.3\.\d+ --exact --version (?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("eol", "python", "minor"),
     "refs": [{"pattern": r"Python\.Python\.3\.\d+ --exact --version[ =]+(?P<v>\d+\.\d+\.\d+)"},
              {"pattern": r"python-(?P<v>\d+\.\d+\.\d+)-amd64\.exe"}],
     "newer_note": "stays NEWER by design: 3.12.10 is the last 3.12 with a Windows installer (later 3.12.x are "
                   "source-only security releases); moving off it means another minor (3.13.x) -- a T7/user decision"},
    {"name": "NifSkope", "pins": [("regex", PINNED_DOWNLOADS, table_row("NifSkope"))],
     "upstream": ("github", "hexabits/nifskope"),   # the maintained fork the docs link and the toolkit was tested with
     "checks": [hashes("NifSkope")]},
    {"name": "7-Zip", "pins": [("regex", PINNED_DOWNLOADS, table_row("7-Zip")),
                               ("regex", PINNED_DOWNLOADS, table_row("7-Zip (Linux)"))],
     "upstream": ("github", "ip7z/7zip"),   # 7-zip.org links its downloads from these GitHub releases
     "refs": [{"pattern": r"7-Zip (?P<v>\d+\.\d+)\b"}],
     "checks": [hashes("7-Zip", "https://api.github.com/repos/ip7z/7zip/releases/tags/{v}"),
                hashes("7-Zip (Linux)", "https://api.github.com/repos/ip7z/7zip/releases/tags/{v}")]},
    {"name": "Blender (4.2 LTS)", "pins": [("regex", PINNED_DOWNLOADS, table_row("Blender"))], "upstream": ("blender", "4.2"),
     "checks": [hashes("Blender", "https://download.blender.org/release/Blender4.2/blender-{v}.sha256")]},
    {"name": "Cathedral Assets Optimizer", "upstream": ("nexus", "skyrimspecialedition", 23316),
     "not_delivered": "evaluated and NOT adopted (KNOWLEDGEBASE.md); nothing tells a user to install it"},
    {"name": "BSArch", "pins": [("regex", PINNED_DOWNLOADS, table_row("BSArch"))], "upstream": ("nexus", "newvegas", 64745)},
    {"name": "Mod Organizer 2", "upstream": ("github", "ModOrganizer2/modorganizer"),
     "not_delivered": "the user's own mod manager; the toolkit only reads its layout"},
    {"name": "Vortex", "upstream": ("github", "Nexus-Mods/Vortex"),
     "not_delivered": "the user's own mod manager; the toolkit only reads its layout"},
    {"name": "Wrye Bash", "upstream": ("github", "wrye-bash/wrye-bash"),
     "not_delivered": "the user's own tool; no shipped instruction installs it"},
    {"name": "Git for Windows", "pins": [("regex", "tools/inventory-runtimes.py",
                                          r"Git-(?P<v>\d+(?:\.\d+){2,3})-64-bit\.exe")],
     "upstream": ("github", "git-for-windows/git"),
     "refs": [{"pattern": r"Git\.Git --exact --version[ =]+(?P<v>\d+(?:\.\d+){2,3})"},
              {"pattern": r"Git-(?P<v>\d+(?:\.\d+){2,3})-64-bit\.exe"},
              {"pattern": r"tested with Git for Windows \*{0,2}(?P<v>\d+(?:\.\d+){2,3})"}],
     "checks": [("hashes", {"items": [{
         "label": "Git for Windows installer", "file": "tools/inventory-runtimes.py",
         "pattern": r"git-for-windows/git/releases/download/(?P<tag>v[\w.]+)/[^\n]*\n[^\n]*?(?P<sha>[0-9a-f]{64})",
         "published": "https://api.github.com/repos/git-for-windows/git/releases/tags/{tag}"}]})],
     "newer_note": "the Git these suites run under on the dev machine: a bump is deliberate and tested -- move every "
                   "pin (inventory-runtimes MANUAL_STEPS: version, release tag, SHA-256; setup.sh; the docs) together"},
    {"name": "Codex CLI", "pins": [("dirs", "tests/fixtures/codex/*"), ("dirs", "tests/fixtures/payloads/codex/*"),
                                   ("regex", "tools/skyguard/codex_trust.py", r"MEASURED on codex-cli (?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("npm", "@openai/codex"),
     "newer_note": "re-capture payload fixtures + re-run conformance (T15b)"},
    {"name": "Claude Code", "pins": [("regex", "tools/skyguard/adapters/claude.py", r"MEASURED, Claude Code (?P<v>\d+\.\d+\.\d+)"),
                                     ("regex", "docs/guard-coverage.md", r"MEASURED 2026-10-04, Claude Code (?P<v>\d+\.\d+\.\d+)")],
     "upstream": ("npm", "@anthropic-ai/claude-code"),
     "newer_note": "re-capture payload fixtures + re-run conformance (T15b)"},
    {"name": "devcontainer base image", "pins": [("regex", ".devcontainer/Dockerfile", r"devcontainers/python:(?P<v>\d+\.\d+\.\d+)-")],
     "checks": [("hashes", {"items": [{
         "label": "image index digest", "file": ".devcontainer/Dockerfile",
         "pattern": r"devcontainers/python:(?P<tag>[\w.-]+)@sha256:(?P<sha>[0-9a-f]{64})",
         "url": "https://mcr.microsoft.com/v2/devcontainers/python/manifests/{tag}",
         "accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"}]})],
     "upstream": ("mcr", "devcontainers/python", r"\d+\.\d+\.\d+-{pyminor}-bookworm")},
    {"name": "busybox (CI sentinel)", "pins": [("regex", ".github/workflows/ci.yml", r"busybox:(?P<v>\d+\.\d+)\b")],
     "upstream": ("dockerhub", "library/busybox"), "compare": "minor"},
]
# Names the manual audit (2026-10-05) covered; tests/test_thirdparty_audit.py holds the registry to this floor.
# Every entry either reads a pin from the repo or says why the toolkit never delivers it (tests hold this).
AUDIT_FLOOR = ("Spriggit", ".NET SDK", "Node.js", "Python", "AutoMod", "PyNifly", "DevBench", "VR Address Library",
               "CrashLoggerSSE", "SKSE VR", "Champollion", "Caprica", "xeditlib", "zEdit", "koffi", "PyFFI",
               "ReSaver (FallrimTools)", "NifSkope", "Blender (4.2 LTS)", "Cathedral Assets Optimizer", "BSArch",
               "Mod Organizer 2", "Vortex", "Codex CLI", "Claude Code", "devcontainer base image",
               "busybox (CI sentinel)")


def dynamic_entries(repo):
    """npm dependencies, Python requirements and workflow actions, enumerated from the repo itself."""
    out = []
    pkg = repo.read("package.json")
    if pkg is not None:
        try:
            data = json.loads(pkg)
            deps = dict(data.get("dependencies", {}), **data.get("optionalDependencies", {}))
        except ValueError:
            deps = None
            out.append({"name": "package.json", "broken": "package.json is not valid JSON"})
        for name in sorted(deps or {}):
            if re.fullmatch(r"https://codeload\.github\.com/[\w.-]+/[\w.-]+/tar\.gz/[0-9a-f]{40}", deps[name]):
                continue   # a commit tarball (xeditlib): its registry entry holds the commit and the tag
            out.append({"name": "npm " + name, "npm": name, "spec": deps[name]})
    req = repo.read("requirements.txt")
    if req is not None:
        for line in req.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r"([A-Za-z0-9_.\-\[\]]+)\s*(==|>=|~=|<=|>|<|!=)?\s*([^\s;]*)", line)
            if line and m:
                out.append({"name": "pypi " + m.group(1), "pypi": re.sub(r"\[.*\]", "", m.group(1)),
                            "op": m.group(2) or "", "spec": m.group(3)})
    seen = set()
    for wf in sorted(repo.root.glob(".github/workflows/*.y*ml")):
        for line in wf.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"uses:\s*(?P<repo>[\w.-]+/[\w.-]+)(?:/[\w./-]+)?@(?P<ref>[\w.-]+)(?:\s*#\s*(?P<tag>v?[\w.-]+))?", line)
            if m and (m.group("repo"), m.group("ref")) not in seen:
                seen.add((m.group("repo"), m.group("ref")))
                out.append({"name": "action " + m.group("repo"), "action": m.group("repo"), "ref": m.group("ref"),
                            "tag": m.group("tag"), "where": wf.relative_to(repo.root).as_posix()})
    return out


# --------------------------------------------------------------------------------------------- engine

class Row:
    def __init__(self, tool, check, pinned, upstream, status, detail=""):
        self.tool, self.check, self.pinned, self.upstream, self.status, self.detail = (
            tool, check, pinned or "-", upstream or "-", status, detail)

    def as_dict(self):
        return dict(tool=self.tool, check=self.check, pinned=self.pinned, upstream=self.upstream,
                    status=self.status, detail=self.detail)


def guarded(rows, tool, check, pinned, fn):
    """Run one check; a CouldNotCheck becomes a COULD NOT CHECK row with its reason, never a silent pass."""
    try:
        fn()
    except CouldNotCheck as exc:
        rows.append(Row(tool, check, pinned, None, UNCHECKED, str(exc)))
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        rows.append(Row(tool, check, pinned, None, UNCHECKED, "unexpected upstream shape (%s: %s)"
                        % (type(exc).__name__, exc)))


def latest_of(f, upstream, pinned):
    kind = upstream[0]
    if kind == "github":
        return gh_latest(f, upstream[1])
    if kind == "github-pre":
        return gh_latest(f, upstream[1], prerelease=True)
    if kind == "nuget":
        return nuget_latest(f, upstream[1])
    if kind == "npm":
        return npm_latest(f, upstream[1])
    if kind == "pypi":
        return pypi_latest(f, upstream[1])
    if kind == "nexus":
        return nexus_version(f, upstream[1], upstream[2])
    if kind == "nexus-file":
        return nexus_file_latest(f, upstream[1], upstream[2])
    if kind == "blender":
        return blender_latest(f, upstream[1])
    if kind == "dockerhub":
        return dockerhub_latest(f, upstream[1])
    if kind == "eol":
        if not pinned:
            raise CouldNotCheck("no pin to choose the release line from")
        cycle = ".".join(str(x) for x in vkey(pinned)[:1 if upstream[2] == "major" else 2])
        return eol_latest(f, upstream[1], cycle)
    if kind == "dotnet":
        if not pinned:
            raise CouldNotCheck("no pin to choose the .NET channel from")
        sdk, phase = dotnet_channel(f, "%d.%d" % vkey(pinned)[:2])
        return sdk + " (" + phase + ")"
    raise CouldNotCheck("unknown upstream kind " + kind)


def audit_entry(f, repo, spec, rows, ctx):
    name = spec["name"]
    # 1. pins: every declared site must be readable, and all must agree.
    pinned = None
    sites = [read_pin_site(repo, s) for s in spec.get("pins", [])]
    unread = [label for label, v in sites if v is None]
    values = {label: v for label, v in sites if v is not None}
    compare = spec.get("compare", "exact")
    if spec.get("pins"):
        if unread:
            rows.append(Row(name, "pin sites", None, None, UNCHECKED,
                            "declared pin site unreadable (moved or reworded?): " + "; ".join(unread)))
        if values:
            pinned = list(values.values())[0]
            bad = {k: v for k, v in values.items() if not same(v, pinned, compare) or "disagrees" in k}
            if bad:
                rows.append(Row(name, "pin sites", pinned, None, DRIFT,
                                "pin sites disagree: " + "; ".join("%s=%s" % kv for kv in values.items())))
    tag_pin = None
    for site in spec.get("tag_pins", []):
        label, tag_pin = read_pin_site(repo, site)
        if tag_pin is None:
            rows.append(Row(name, "pin sites", pinned, None, UNCHECKED, "tag pin site unreadable: " + label))

    # 2. version vs upstream.
    upstream = spec.get("upstream")
    latest_box = {}
    if spec.get("not_delivered"):
        # Nothing the toolkit ships puts this on a machine, so there is no pin to hold. Upstream latest is shown when
        # it can be read; an unreadable one changes nothing here (no decision depends on it).
        try:
            latest = latest_of(f, upstream, pinned) if upstream else None
            latest_box["v"] = latest
        except CouldNotCheck as exc:
            latest = "unknown (%s)" % exc
        rows.append(Row(name, "delivery", None, latest, OUTSIDE, spec["not_delivered"]))
        upstream = None
    if upstream:
        def version_row():
            shown_pin = tag_pin or pinned
            if upstream[0] == "mcr":
                if not pinned:
                    raise CouldNotCheck("no pinned image tag")
                pyminor = "%d.%d" % vkey(ctx.get("Python") or "0.0")[:2]
                latest = mcr_latest(f, upstream[1], upstream[2].replace("{pyminor}", re.escape(pyminor)))
                latest_box["v"] = latest
                st, why = version_status(pinned, latest.split("-")[0])
                rows.append(Row(name, "version", pinned + "-" + pyminor + "-bookworm", latest, st, why))
                return
            latest = latest_of(f, upstream, pinned)
            latest_box["v"] = latest
            if not shown_pin:
                rows.append(Row(name, "version", None, latest, UNPINNED, "recommended without a pin"))
                return
            cmp_pin, cmp_latest = shown_pin, latest.split(" ")[0]
            st, why = (OK, "") if same(cmp_pin, cmp_latest, compare) else version_status(cmp_pin, cmp_latest)
            if st == NEWER and spec.get("newer_note"):
                why = spec["newer_note"]
            rows.append(Row(name, "version", shown_pin, latest, st, why))
        guarded(rows, name, "version", pinned, version_row)
        if upstream[0] in ("nuget",) and pinned:
            def exists():
                have = pinned in nuget_versions(f, upstream[1])
                rows.append(Row(name, "pin published", pinned, upstream[1], OK if have else DRIFT,
                                "" if have else "pinned version is not on NuGet"))
            guarded(rows, name, "pin published", pinned, exists)
    nexus = spec.get("nexus")
    if nexus and f.nexus_key:
        def nexus_row():
            v = nexus_version(f, nexus[0], nexus[1])
            gh = latest_box.get("v")
            st = OK if gh is None or same(v, gh) else NEWER
            rows.append(Row(name, "nexus %s/%s" % nexus, tag_pin or pinned, v, st,
                            "" if st == OK else "Nexus and GitHub disagree (GitHub %s)" % gh))
        guarded(rows, name, "nexus", pinned, nexus_row)

    # 3. install refs on user-facing surfaces equal the pin (one summary row per pin they are held to).
    tally = {}
    for ref in spec.get("refs", []):
        target = tag_pin if ref.get("against") == "tag" else pinned
        agree, history = audit_refs(repo, name, ref, target, rows)
        t = tally.setdefault(target, [0, 0])
        t[0], t[1] = t[0] + agree, t[1] + history
    for target, (agree, history) in tally.items():
        if agree and target is not None:
            rows.append(Row(name, "install refs", target, None, OK, "%d ref(s) agree%s" % (
                agree, (", %d historical mention(s) skipped" % history) if history else "")))

    # 4. integration checks.
    for check, args in spec.get("checks", []):
        fn = CHECKS[check]
        guarded(rows, name, check, pinned, lambda fn=fn, args=args: fn(f, repo, name, pinned, tag_pin, args, rows,
                                                                        ctx, latest_box))


def audit_refs(repo, name, ref, target, rows):
    """DRIFT rows for every reference that disagrees; returns (agreeing count, historical mentions skipped)."""
    hits = list(scan_refs(repo, ref))
    live = [h for h in hits if h[2] != "history"]
    history = len(hits) - len(live)
    if not live:
        return 0, history
    if target is None:
        rows.append(Row(name, "install refs", None, None, UNCHECKED,
                        "%d reference(s) but no pin to compare them with (%s:%d)" % (len(live), live[0][0], live[0][1])))
        return 0, history
    compare = ref.get("compare", "exact")
    agree = 0
    for rel, line, v, _text in live:
        if v is None:
            rows.append(Row(name, "install refs", target, None, DRIFT, "%s:%d %s" % (rel, line, ref.get("unpinned", "no version"))))
        elif re.search(r"[$%{]", v):
            agree += 1   # a variable the script fills from the pin file
        elif not same(v, target, compare):
            rows.append(Row(name, "install refs", target, None, DRIFT, "%s:%d says %s" % (rel, line, v)))
        else:
            agree += 1
    return agree, history


def check_cli_flags(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    if not pinned:
        raise CouldNotCheck("no pin: cannot pick the upstream source revision")
    documented = set()
    need = re.compile(args["line"])
    for rel in repo.surfaces():
        text = repo.read(rel) or ""
        for line in text.splitlines():
            if need.search(line):
                documented.update(re.findall(r"(?<![\w-])--([A-Z][A-Za-z]+)\b", line))
    if not documented:
        raise CouldNotCheck("no documented flags found (pattern %s)" % args["line"])
    upstream_src = ""
    for path in args["sources"]:
        src = gh_raw(f, args["repo"], pinned, path)
        if src is None:
            raise CouldNotCheck("%s missing at %s@%s" % (path, args["repo"], pinned))
        upstream_src += src
    options = set(re.findall(r"Option\((?:'\w',\s*)?\"(\w+)\"", upstream_src))
    if not options:
        raise CouldNotCheck("no Option(...) declarations parsed from the upstream source")
    missing = sorted(documented - options)
    if missing:
        rows.append(Row(name, "cli flags", pinned, None, DRIFT,
                        "documented flag(s) not in %s@%s: %s" % (args["repo"], pinned, ", ".join("--" + m for m in missing))))
    else:
        rows.append(Row(name, "cli flags", pinned, None, OK, "%d documented flag(s) exist upstream" % len(documented)))


def check_nupkg_tfm(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    if not pinned:
        raise CouldNotCheck("no pin")
    runtime = ctx.get(args["runtime"])
    if not runtime:
        raise CouldNotCheck("no %s pin to compare the package's target framework with" % args["runtime"])
    pkg = args["package"]
    url = "https://api.nuget.org/v3-flatcontainer/%s/%s/%s.%s.nupkg" % (pkg, pinned, pkg, pinned)
    names = zip_names(f, url)
    if names is None:
        rows.append(Row(name, "nupkg tfm", pinned, None, DRIFT, "pinned package not on NuGet: " + url))
        return
    tfms = sorted({n.split("/")[1] for n in names if n.startswith("tools/") and n.count("/") >= 2})
    want = "net%d.0" % vkey(runtime)[0]
    if want in tfms:
        rows.append(Row(name, "nupkg tfm", pinned, ",".join(tfms), OK, "ships tools/%s/ for the pinned SDK" % want))
    else:
        rows.append(Row(name, "nupkg tfm", pinned, ",".join(tfms) or "none", DRIFT,
                        "%s %s ships no tools/%s/ (pinned %s %s)" % (pkg, pinned, want, args["runtime"], runtime)))


def check_tag_commit(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    if not pinned or not tag_pin:
        raise CouldNotCheck("needs both a commit pin and a tag pin")
    sha = gh_tag_commit(f, args["repo"], tag_pin)
    if sha is None:
        rows.append(Row(name, "tag -> commit", tag_pin, None, DRIFT, "tag %s no longer exists upstream" % tag_pin))
    elif same(sha, pinned, "prefix"):
        rows.append(Row(name, "tag -> commit", tag_pin, sha[:12], OK, "tag still points at the pinned commit"))
    else:
        rows.append(Row(name, "tag -> commit", tag_pin, sha[:12], DRIFT,
                        "tag moved: now %s, pinned %s" % (sha[:12], pinned[:12])))


def documented_paths(repo, files, pattern):
    rx = re.compile(pattern)
    out = []
    for rel in files or repo.surfaces():
        text = repo.read(rel)
        if text is None:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            for m in rx.finditer(line):
                if not HISTORY.search(history_context(lines, i, m.start())):
                    out.append((m.group("p").rstrip("/"), "%s:%d" % (rel, i + 1)))
    return out


def check_repo_paths(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    if not pinned:
        raise CouldNotCheck("no pin: cannot pick the upstream revision")
    paths = documented_paths(repo, args["files"], args["pattern"])
    if not paths:
        raise CouldNotCheck("no documented upstream path found in " + ", ".join(args["files"]))
    missing = []
    for path in sorted({p for p, _ in paths}):
        if not gh_path_exists(f, args["repo"], pinned, path):
            missing.append("%s (cited at %s)" % (path, next(w for p, w in paths if p == path)))
    if missing:
        for m in missing:
            rows.append(Row(name, "upstream path", pinned[:12], None, DRIFT, "not at the pinned commit: " + m))
    else:
        rows.append(Row(name, "upstream path", pinned[:12], None, OK,
                        "%d documented path(s) exist at the pinned commit" % len({p for p, _ in paths})))


def check_follows_latest(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    latest = latest_box.get("v")
    if latest is None:
        raise CouldNotCheck("upstream latest unknown")
    hits = []
    for rel in repo.surfaces():
        for i, line in enumerate((repo.read(rel) or "").splitlines()):
            if re.search(args["pattern"], line):
                hits.append("%s:%d" % (rel, i + 1))
    if not hits:
        return
    if not pinned:
        rows.append(Row(name, "install = pin", None, latest, UNPINNED, "install follows the latest release"))
    elif same(pinned, latest):
        rows.append(Row(name, "install = pin", pinned, latest, OK,
                        "install says 'latest release', which is the verified version today (%s)" % hits[0]))
    else:
        rows.append(Row(name, "install = pin", pinned, latest, DRIFT,
                        "install says 'latest release' (%s) but the verified version is %s" % (hits[0], pinned)))


def check_zip_paths(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    # The release users are told to download is the PINNED one; without a tag format, the latest release.
    tag = args["tag_format"].format(v=pinned) if args.get("tag_format") and pinned else latest_box.get("v")
    if tag is None:
        raise CouldNotCheck("upstream release unknown")
    paths = documented_paths(repo, None, args["pattern"])
    if not paths:
        raise CouldNotCheck("no documented path inside %s found" % args["asset"])
    assets = gh_release_assets(f, args["repo"], tag)
    if args["asset"] not in assets:
        rows.append(Row(name, "release asset", pinned, tag, DRIFT,
                        "%s no longer ships %s (assets: %s)" % (tag, args["asset"], ", ".join(assets) or "none")))
        return
    names = zip_names(f, "https://github.com/%s/releases/download/%s/%s" % (args["repo"], tag, args["asset"]))
    if names is None:
        raise CouldNotCheck("release asset listed but its download is 404")
    have = {n.rstrip("/") for n in names}
    missing = sorted({p for p, _ in paths if p not in have})
    if missing:
        for p in missing:
            rows.append(Row(name, "upstream path", pinned, tag, DRIFT, "%s not in %s/%s (cited at %s)" % (
                p, tag, args["asset"], next(w for q, w in paths if q == p))))
    else:
        rows.append(Row(name, "upstream path", pinned, tag, OK,
                        "%d documented path(s) ship in %s" % (len({p for p, _ in paths}), args["asset"])))


def check_upstream_text(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    tag = latest_box.get("v")
    if tag is None:
        raise CouldNotCheck("upstream release unknown")
    items = sorted({p for p, _ in documented_paths(repo, args["files"], args["pattern"])})
    if not items:
        raise CouldNotCheck("no documented items found in " + ", ".join(args["files"]))
    doc = gh_raw(f, args["repo"], tag, args["path"])
    if doc is None:
        raise CouldNotCheck("%s missing at %s@%s" % (args["path"], args["repo"], tag))
    missing = [i for i in items if i not in doc]
    if missing:
        rows.append(Row(name, "documented API", pinned, tag, DRIFT,
                        "not in upstream %s@%s: %s" % (args["path"], tag, ", ".join(missing))))
    else:
        rows.append(Row(name, "documented API", pinned, tag, OK,
                        "%d documented item(s) still in upstream %s" % (len(items), args["path"])))


def check_hashes(f, repo, name, pinned, tag_pin, args, rows, ctx, latest_box):
    """Every pinned SHA in the repo is the one upstream publishes for that exact file. An item with `published`
    fetches that URL (fields filled from the pattern's named groups) and requires the hash in its text; without it,
    the file at the row's URL is downloaded and hashed (small assets only)."""
    for it in args["items"]:
        m = re.search(it["pattern"], repo.read(it["file"]) or "")
        if not m:
            rows.append(Row(name, "pinned hash", pinned, it["label"], UNCHECKED,
                            "no pinned hash found in %s (moved or reworded?)" % it["file"]))
            continue
        want, fields = m.group("sha").lower(), {k: v for k, v in m.groupdict().items() if v is not None}
        if it.get("published"):
            url = it["published"].format(**fields)
            ok = want in f.get_ok(url).text().lower()
        else:
            url = it["url"].format(**fields) if it.get("url") else fields.get("url", "")
            if not url.startswith("https://"):
                raise CouldNotCheck("%s: no download URL to hash" % it["label"])
            ok = hashlib.sha256(f.get_ok(url, accept=it.get("accept")).body).hexdigest() == want
        rows.append(Row(name, "pinned hash", fields.get("v", pinned), it["label"], OK if ok else DRIFT,
                        ("SHA-256 matches " if ok else "pinned SHA %s... is NOT what upstream has at " % want[:12]) + url))


CHECKS = {"hashes": check_hashes, "cli_flags": check_cli_flags, "nupkg_tfm": check_nupkg_tfm, "tag_commit": check_tag_commit,
          "repo_paths": check_repo_paths, "follows_latest": check_follows_latest, "zip_paths": check_zip_paths,
          "upstream_text": check_upstream_text}


def audit_dynamic(f, repo, d, rows):
    name = d["name"]
    if "broken" in d:
        rows.append(Row(name, "pin file", None, None, UNCHECKED, d["broken"]))
        return
    if "npm" in d:
        spec, pkg = d["spec"], d["npm"]
        exact = re.fullmatch(r"\d+\.\d+\.\d+(?:-[\w.]+)?", spec)
        if not exact:
            rows.append(Row(name, "exact pin", spec, None, DRIFT, "package.json range, not an exact pin"))
        lock = repo.read("package-lock.json")
        if lock is not None and exact:
            try:
                locked = json.loads(lock).get("packages", {}).get("node_modules/" + pkg, {}).get("version")
            except ValueError:
                locked = None
            if locked != spec:
                rows.append(Row(name, "lock agrees", spec, None, DRIFT, "package-lock.json has %s" % locked))
        if exact:
            guarded(rows, name, "pin published", spec, lambda: rows.append(
                Row(name, "pin published", spec, None, OK if npm_has(f, pkg, spec) else DRIFT,
                    "" if npm_has(f, pkg, spec) else "pinned version is not on npm")))

        def ver():
            latest = npm_latest(f, pkg)
            st, why = version_status(spec, latest) if exact else (UNPINNED, "range")
            rows.append(Row(name, "version", spec, latest, st, why))
        guarded(rows, name, "version", spec, ver)
        return
    if "pypi" in d:
        spec, pkg = d["spec"], d["pypi"]
        if d["op"] != "==":
            rows.append(Row(name, "exact pin", d["op"] + spec, None, DRIFT, "requirements.txt is not an exact == pin"))
        else:
            lock = repo.read("requirements.lock")
            if lock is not None:
                canon = re.sub(r"[-_.]+", "-", pkg).lower()
                locked = [m.group(2) for m in re.finditer(r"(?im)^([A-Za-z0-9_.\-]+)==([^\s\\;]+)", lock)
                          if re.sub(r"[-_.]+", "-", m.group(1)).lower() == canon]
                if spec not in locked:
                    rows.append(Row(name, "lock agrees", spec, None, DRIFT,
                                    "requirements.lock has %s" % (", ".join(locked) or "no entry")))

            def pub():
                have = pypi_has(f, pkg, spec)
                rows.append(Row(name, "pin published", spec, None, OK if have else DRIFT,
                                "" if have else "pinned version is not on PyPI"))
            guarded(rows, name, "pin published", spec, pub)

        def ver():
            latest = pypi_latest(f, pkg)
            st, why = version_status(spec, latest) if d["op"] == "==" else (UNPINNED, "range")
            rows.append(Row(name, "version", d["op"] + spec, latest, st, why))
        guarded(rows, name, "version", spec, ver)
        return
    if "action" in d:
        repo_name, ref, tag = d["action"], d["ref"], d["tag"]
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            rows.append(Row(name, "sha pin", ref, None, DRIFT, "%s uses %s@%s, not a commit SHA" % (d["where"], repo_name, ref)))
        elif not tag:
            rows.append(Row(name, "sha pin", ref[:12], None, UNCHECKED, "SHA without a '# vX' tag comment to verify"))
        else:
            def tc():
                sha = gh_tag_commit(f, repo_name, tag)
                if sha is None:
                    rows.append(Row(name, "tag -> commit", tag, None, DRIFT, "tag %s does not exist upstream" % tag))
                elif sha != ref:
                    rows.append(Row(name, "tag -> commit", tag, sha[:12], DRIFT,
                                    "comment says %s but %s is %s, workflow pins %s" % (tag, tag, sha[:12], ref[:12])))
                else:
                    rows.append(Row(name, "tag -> commit", tag, sha[:12], OK, "SHA matches its tag comment"))
            guarded(rows, name, "tag -> commit", tag, tc)

        def ver():
            latest = gh_latest(f, repo_name)
            st, why = version_status(tag, latest) if tag else (UNPINNED, "no tag comment")
            rows.append(Row(name, "version", tag or ref[:12], latest, st, why))
        guarded(rows, name, "version", tag, ver)


def pin_of(repo, spec):
    vals = [v for _label, v in (read_pin_site(repo, s) for s in spec.get("pins", [])) if v is not None]
    return vals[0] if vals else None


# ------------------------------------------------------------------------------------- delivery paths
# Plan 8 T6d (user ruling 2026-10-05: every user gets EXACTLY the versions we tested). Every instruction in a shipped
# text file that puts third-party software on a machine must name ONE version and give a way to verify it. Purely
# local (no network): `tests/test_thirdparty_audit.py` runs it offline on the real tree and on synthetic ones.
#
# There is NO wording-based history skip here (review I1: "Before you start, run `pip install lz4`" read as history
# and silenced every rule). History lives in DELIVERY_HISTORY_FILES, skipped by file; any other line that only
# records history needs an exemption with its reason.

DELIVERY_SUFFIXES = (".md", ".txt", ".sh", ".ps1", ".cmd", ".bat", ".py", ".json", ".yml", ".yaml", ".js", ".example")
DELIVERY_SKIP_DIRS = {".git", "node_modules", "tests", ".superpowers", ".pytest_cache", "__pycache__", ".venv"}
DELIVERY_PRIVATE_PATHS = (".skyguard", ".claude/backups", ".claude/settings.local.json", "KNOWLEDGEBASE.local.md")
# Files that only record history (file-scoped; nothing in them is an instruction), plus this file's own rule text.
DELIVERY_HISTORY_FILES = ("CHANGELOG.md", "docs/superpowers/*", "scripts/thirdparty-audit.py")
SHA256_HEX = re.compile(r"\b[0-9a-f]{64}\b")
EXACT_NPM = re.compile(r"(?:@[\w.-]+/)?[\w.-]+@\d+\.\d+\.\d+(?:-[\w.]+)?")
COMMIT_NPM = re.compile(r"(?:github:)?[\w.-]+/[\w.-]+#[0-9a-f]{40}")
EXACT_PY = re.compile(r"[\w.\-\[\]]+==\d+(?:\.\d+)+")
# A clone is pinned when it names a tag AND something checks the commit after cloning: repo -> (file, proof regex).
VERIFIED_CLONES = {"SpookyPirate/spookys-automod-toolkit": ("tools/automod-cli.sh", r'PIN_COMMIT="[0-9a-f]{40}"')}
DOWNLOAD_HOSTS = (r"github\.com/[\w.-]+/[\w.-]+/releases|blender\.org|nodejs\.org|python\.org|git-scm\.com|"
                  r"nexusmods|Nexus mod \d+|FallrimTools page|aka\.ms/|dot\.net/")
# A download's hash may sit on the same line or the next two (a wrapped string literal or bullet).
HASH_WINDOW = 3


def _command_args(line, head):
    """The arguments of each `head` command on the line, up to where the command ends (a quote, a backtick, a
    pipe, ...): a flag elsewhere on the line -- another command's --version -- must not pin this one."""
    return [m.group("args") for m in re.finditer(head + r"(?P<args>[^`\"'\n|;&)]*)", line)]


def _specs(args):
    return [t for t in args.split() if not t.startswith("-") and not t.startswith("<")]


def _npm_problems(line):
    out = []
    for args in _command_args(line, r"\bnpm\s+(?:install|i|add)\b"):
        specs = _specs(args)
        if not specs:
            out.append("bare `npm install` resolves ranges at install time: use `npm ci` (the lockfile)")
        for s in specs:
            if not (EXACT_NPM.fullmatch(s) or COMMIT_NPM.fullmatch(s)):
                out.append("npm package `%s` without an exact version (@x.y.z) or a commit (#<40-hex sha>)" % s)
    return out


def _runner_problems(line):
    """npx / npm exec / uvx run a package fetched at that moment: the package must carry its exact version."""
    out = []
    for args in _command_args(line, r"\b(?:npx|npm\s+exec|uvx|pnpm\s+dlx|bunx)(?=\s)"):
        specs = _specs(args)
        if specs and not (EXACT_NPM.fullmatch(specs[0]) or EXACT_PY.fullmatch(specs[0])):
            out.append("package runner fetches `%s` with no exact version" % specs[0])
    return out


def _pip_problems(line):
    """pip (and `uv pip`, whose command contains it): per command (review M1), hash-locked or a dry run that
    installs nothing."""
    out = []
    for args in _command_args(line, r"\bpip3?(?:\.exe)?\s+install\b"):
        if "--dry-run" in args.split():
            continue
        if not (re.search(r"--require-hashes\b", args) and re.search(r"-r\s+\S+\.lock\b", args)):
            out.append("pip install without `--require-hashes -r <file>.lock`: versions and wheels are not verified")
    return out


def _tool_installer_problems(line):
    """pipx / uv tool: exact == pin (they have no hash lock); scoop: app@version; cargo: --version + --locked;
    go install: @vX.Y.Z (never @latest)."""
    out = []
    for args in _command_args(line, r"\b(?:pipx\s+(?:install|run)|uv\s+tool\s+(?:install|run))\b"):
        if not any(EXACT_PY.fullmatch(s) for s in _specs(args)):
            out.append("pipx / uv tool without an exact `pkg==x.y.z`")
    for args in _command_args(line, r"\bscoop\s+install\b"):
        out += ["scoop app `%s` without @version" % s for s in _specs(args) if not re.search(r"@\d", s)]
    for args in _command_args(line, r"\bcargo\s+install\b"):
        if not (re.search(r"--version[ =]+\d", args) and "--locked" in args.split()):
            out.append("cargo install without --version <x.y.z> --locked")
    for args in _command_args(line, r"\bgo\s+install\b"):
        out += ["go install `%s` without @vX.Y.Z" % s for s in _specs(args) if not re.search(r"@v\d", s)]
    return out


def _gh_download_problems(line, window):
    out = []
    for m in re.finditer(r"\bgh\s+release\s+download\b(?P<args>[^`\n|;&)]*)", line):   # a quoted "$TAG" counts
        toks = m.group("args").split()
        if not toks or toks[0].startswith("-"):   # the tag is the first positional; a flag's value is not one
            out.append("gh release download without a tag: you get the newest release")
        elif not re.search(r"\bsha(?:256|512)sum\b|Get-FileHash|\b[0-9a-f]{64}\b", window):
            out.append("gh release download with no checksum check")
    return out


def _clone_problems(line, repo):
    out = []
    for m in re.finditer(r"\bgit clone\s+(?P<args>(?:-|https?://|git@|\$)[^`\"'\n|;&]*)", line):
        args = m.group("args")
        if not re.search(r"--branch[ =]+\S+", args):
            out.append("git clone without --branch <tag>: you get the default branch's HEAD")
            continue
        url = re.search(r"github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?(?:\s|$)", args + " ")
        proof = VERIFIED_CLONES.get(url.group(1)) if url else None
        if not proof or not re.search(proof[1], repo.read(proof[0]) or ""):
            out.append("git clone of %s is not commit-verified after cloning (no entry in VERIFIED_CLONES whose "
                       "proof is present)" % (url.group(1) if url else "an unknown repo"))
    return out


def _pkg_manager_problems(line):
    out = []
    for args in _command_args(line, r"\b(?:apt(?:-get)?|choco|brew|dnf|yum|apk)\s+install\b"):
        if re.search(r"--version[ =]+\d", args):   # choco install x --version 1.2.3
            continue
        pkgs = _specs(args)
        out += ["system package `%s` without an exact version (pkg=version)" % p for p in pkgs
                if "=" not in p and not re.search(r"@\d", p)]
    return out


def _shell_pipe_problems(line):
    sh = re.search(r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", line)
    ps = (re.search(r"(?i)\b(?:irm|iwr|curl|wget|Invoke-RestMethod|Invoke-WebRequest|DownloadString)\b", line)
          and re.search(r"(?i)\|\s*(?:iex|Invoke-Expression)\b|\b(?:iex|Invoke-Expression)\s*\(", line))
    return ["download piped into a shell: nothing pins or verifies what runs"] if sh or ps else []


# rule id -> (applies to file?, finder(line, repo, window) -> [problem, ...]); window = the line plus the next
# HASH_WINDOW - 1 lines, for rules whose proof may wrap onto the next line.
DELIVERY_RULES = {
    "pip": (lambda rel: True, lambda line, repo, window: _pip_problems(line)),
    "npm": (lambda rel: True, lambda line, repo, window: _npm_problems(line)),
    "package-runner": (lambda rel: True, lambda line, repo, window: _runner_problems(line)),
    "tool-installer": (lambda rel: True, lambda line, repo, window: _tool_installer_problems(line)),
    "gh-release": (lambda rel: True, lambda line, repo, window: _gh_download_problems(line, window)),
    "dotnet-tool": (lambda rel: True, lambda line, repo, window: [
        "dotnet tool install/update without --version: you get whatever is newest"
        for args in _command_args(line, r"\bdotnet tool (?:install|update)\b")
        if not re.search(r"--version[ =]+[^\s`'\"]", args)]),
    "git-clone": (lambda rel: True, lambda line, repo, window: _clone_problems(line, repo)),
    "winget": (lambda rel: True, lambda line, repo, window: [
        "winget install without --exact --version <V>: you get whatever is newest"
        for args in _command_args(line, r"\bwinget install\b")
        if not (re.search(r"--exact\b", args) and re.search(r"--version[ =]+[^\s`'\"-]", args))]),
    "pipe-to-shell": (lambda rel: True, lambda line, repo, window: _shell_pipe_problems(line)),
    "docker-from": (lambda rel: True, lambda line, repo, window: [
        "base image by tag, not by digest (@sha256:...)"] if (
        re.match(r"\s*FROM\s+(?!scratch\b)\S+", line) and "@sha256:" not in line) else []),
    "system-package": (lambda rel: True, lambda line, repo, window: _pkg_manager_problems(line)),
    "download-checksum": (lambda rel: rel.endswith("Dockerfile") or "/.devcontainer/" in "/" + rel,
                          lambda line, repo, window: ["download with no checksum check in the same step"] if (
                              re.search(r"\b(?:curl|wget)\b", line)
                              and not re.search(r"\bsha(?:256|512)sum\b", line)) else []),
    "follows-latest": (lambda rel: True, lambda line, repo, window: [
        "install follows whatever is newest"] if re.search(r"(?i)\b(?:download|install|acquire|click|grab)", line)
        and re.search(
        r"(?i)\b(?:latest|newest|current)\s+(?:LTS\s+)?(?:release|version|build)\b|releases/latest|big green LTS|"
        r"\bthe LTS release\b|\bdownload\s+(?:a|any)\s+release\b", line) else []),
    "download-unverified": (lambda rel: True, lambda line, repo, window: [
        "download instruction without a pinned version + SHA-256 (or a pointer to *Pinned downloads*)"] if (
        re.search(r"(?i)\bdownload", line) and re.search(DOWNLOAD_HOSTS, line)
        and not SHA256_HEX.search(window) and "Pinned downloads" not in window) else []),
}

# Lines that match a rule but are not a delivery path, each with its reason. Every entry must still match something
# on the real tree (tests/test_thirdparty_audit.py), so a fixed line cannot leave a silent hole behind.
_CLAUDE = ("the agent itself: Claude Code updates itself after install, so an install-time pin cannot hold; the "
           "toolkit records the versions its adapter was measured on instead (the Claude Code registry row)")
_SKSE = ("third-party recipe quoted verbatim and labeled 'Not pinned and not verified' in the doc; the toolkit "
         "never runs it")
_OWN = ("downloads the toolkit's OWN release by its tag in order to verify it: the script itself is the verification "
        "(and a published zip's contents are checked member by member)")
_CI = ("CI runner tooling (T10's workflows): never delivered to a user; versions follow the runner image")
DELIVERY_EXEMPT = [
    ("docs/getting-started.md", "npm", r"npm install -g @anthropic-ai/claude-code", _CLAUDE),
    ("KNOWLEDGEBASE.md", "dotnet-tool", r"a `dotnet tool update` on an SDK 6/8/9 machine failed",
     "records the failure that motivated the pin; the instruction two lines up is pinned"),
    ("docs/skse-cross-compile.md", "system-package", r"apt-get install -y --no-install-recommends", _SKSE),
    ("docs/skse-cross-compile.md", "pipe-to-shell", r"xmake\.io/shget\.text \| bash", _SKSE),
    ("docs/skse-cross-compile.md", "download-unverified", r"Jake-Shadle/xwin/releases/download", _SKSE),
    (".github/workflows/ci.yml", "system-package", r"sudo apt-get install -y|choco install jq -y", _CI),
    ("*settings.local.json.example", "npm", r'"Bash\(npm install:\*\)"', "a permission-allowlist pattern, not an install"),
    ("*settings.local.json.example", "pip", r'"Bash\(pip install:\*\)"', "a permission-allowlist pattern, not an install"),
    ("*", "follows-latest", r"install locations \(newest version first\)",
     "the hook wrappers' search order for an interpreter already installed, not an install"),
    ("scripts/build-nexus-pack.sh", "gh-release", r'gh release download "\$TAG"', _OWN),
    ("tools/verify-release.sh", "gh-release", r"gh release download", _OWN),
    ("tools/skyguard/shellparse.py", "pipe-to-shell", r"An installer piped to sh \(`curl \.\.\. \| sh`\)",
     "skyguard's own rule documentation, describing what that rule does not treat as decoded text"),
]


def logical_lines(text):
    """(first line number, text) with backslash continuations joined: one shell command is one line."""
    out, buf, start = [], [], None
    for i, line in enumerate(text.splitlines()):
        if start is None:
            start = i + 1
        stripped = line.rstrip()
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(line)
        out.append((start, " ".join(b.strip() for b in buf)))
        buf, start = [], None
    if buf:
        out.append((start, " ".join(b.strip() for b in buf)))
    return out


def delivery_files(repo):
    """Every shipped text file: DELIVERY_SUFFIXES or a Dockerfile, outside DELIVERY_SKIP_DIRS (tests and VCS/cache
    folders never ship) and DELIVERY_HISTORY_FILES."""
    out = []
    for dirpath, dirnames, filenames in os.walk(repo.root):
        dirnames[:] = sorted(d for d in dirnames if d not in DELIVERY_SKIP_DIRS and
                             (Path(dirpath) / d).relative_to(repo.root).as_posix() not in DELIVERY_PRIVATE_PATHS)
        for name in sorted(filenames):
            if not (name.endswith(DELIVERY_SUFFIXES) or name == "Dockerfile" or name.startswith("Dockerfile.")):
                continue
            rel = (Path(dirpath) / name).relative_to(repo.root).as_posix()
            if rel in DELIVERY_PRIVATE_PATHS:
                continue
            if not any(fnmatch.fnmatch(rel, x) for x in DELIVERY_HISTORY_FILES):
                out.append(rel)
    return out


def delivery_findings(repo, exempt=None):
    """-> (findings, exempted, stale): findings/exempted are (file, line, rule, problem, text[, reason]); stale lists
    the exemptions that matched nothing (tests hold the real tree to none, so a fixed line cannot leave a silent hole
    behind; a synthetic tree legitimately leaves most of them unused)."""
    exempt = DELIVERY_EXEMPT if exempt is None else exempt
    findings, exempted, used = [], [], set()
    for rel in delivery_files(repo):
        text = repo.read(rel) or ""
        raw = text.splitlines()
        for lineno, line in logical_lines(text):
            window = " ".join([line] + raw[lineno:lineno + HASH_WINDOW - 1])
            for rule, (applies, finder) in DELIVERY_RULES.items():
                if not applies(rel):
                    continue
                for problem in finder(line, repo, window):
                    hit = next((k for k, e in enumerate(exempt) if fnmatch.fnmatch(rel, e[0]) and e[1] == rule
                                and re.search(e[2], line)), None)
                    if hit is None:
                        findings.append((rel, lineno, rule, problem, line.strip()[:160]))
                    else:
                        used.add(hit)
                        exempted.append((rel, lineno, rule, problem, line.strip()[:160], exempt[hit][3]))
    stale = [e for k, e in enumerate(exempt) if k not in used]
    return findings, exempted, stale


def audit_delivery(repo, rows):
    findings, exempted, _stale = delivery_findings(repo)
    for rel, line, rule, problem, _text in findings:
        rows.append(Row("delivery paths", rule, None, None, DRIFT, "%s:%d %s" % (rel, line, problem)))
    if not findings:
        rows.append(Row("delivery paths", "pinned+verified", None, None, OK,
                        "%d file(s) scanned; %d exempt with a reason" % (len(delivery_files(repo)), len(exempted))))


def run(repo_root, fetcher, only=None):
    repo = Repo(repo_root)
    rows = []
    # Every pin is read up front, so a cross-pin check (Spriggit's package vs the .NET SDK pin) does not depend
    # on registry order or on --only.
    ctx = {spec["name"]: pin_of(repo, spec) for spec in REGISTRY}
    wanted = {o.strip().lower() for o in only} if only else None
    for spec in REGISTRY:
        if wanted is None or spec["name"].lower() in wanted:
            audit_entry(fetcher, repo, spec, rows, ctx)
    for d in dynamic_entries(repo):
        if wanted is None or d["name"].lower() in wanted:
            audit_dynamic(fetcher, repo, d, rows)
    if wanted is None or "delivery paths" in wanted:
        audit_delivery(repo, rows)
    if wanted is not None:
        known = ({s["name"].lower() for s in REGISTRY} | {d["name"].lower() for d in dynamic_entries(repo)}
                 | {"delivery paths"})
        for o in sorted(wanted - known):
            rows.append(Row(o, "selection", None, None, UNCHECKED, "--only names no known integration"))
    if not rows:
        rows.append(Row("(audit)", "selection", None, None, UNCHECKED, "nothing was audited"))
    unique, seen = [], set()
    for r in rows:   # two matches on one line report once
        key = tuple(r.as_dict().values())
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def exit_code(rows):
    statuses = {r.status for r in rows}
    if DRIFT in statuses:
        return 1
    if UNCHECKED in statuses or not rows:
        return 2
    return 0


def render(rows, secrets=()):
    head = ("TOOL", "CHECK", "PINNED", "UPSTREAM", "STATUS", "DETAIL")
    table = [head] + [(r.tool, r.check, r.pinned, r.upstream, r.status, r.detail) for r in rows]
    widths = [min(max(len(str(row[i])) for row in table), cap) for i, cap in enumerate((26, 15, 22, 26, 15, 999))]
    out = []
    for row in table:
        out.append("  ".join(str(c).ljust(widths[i]) if i < 5 else str(c) for i, c in enumerate(row)).rstrip())
    counts = {s: sum(1 for r in rows if r.status == s) for s in (OK, NEWER, UNPINNED, OUTSIDE, DRIFT, UNCHECKED)}
    out.append("")
    out.append("rows: %d  " % len(rows) + "  ".join("%s %d" % kv for kv in counts.items()))
    text = "\n".join(out)
    for s in secrets:
        text = text.replace(s, "<redacted>")
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--offline", action="store_true", help="read only the response cache")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--max-age", type=float, default=6.0, help="hours a cached response stays fresh online")
    ap.add_argument("--only", help="comma-separated integration names")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="list the integrations and exit")
    a = ap.parse_args(argv)
    if a.list:
        for spec in REGISTRY:
            print(spec["name"])
        for d in dynamic_entries(Repo(a.repo)):
            print(d["name"])
        return 0
    if not Path(a.repo).is_dir():
        print("COULD NOT CHECK: no repo at " + a.repo)
        return 2
    f = Fetcher(a.cache_dir, offline=a.offline, max_age=a.max_age * 3600)
    rows = run(a.repo, f, a.only.split(",") if a.only else None)
    if a.json:
        text = json.dumps({"exit": exit_code(rows), "rows": [r.as_dict() for r in rows]}, indent=1)
        for s in f.secrets:
            text = text.replace(s, "<redacted>")
        print(text)
    else:
        print(render(rows, f.secrets))
        code = exit_code(rows)
        print("exit %d: %s" % (code, {0: "no blocking drift", 1: "DRIFT in a pinned integration",
                                       2: "some rows could not be checked"}[code]))
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())
