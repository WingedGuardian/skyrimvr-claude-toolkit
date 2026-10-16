#!/usr/bin/env python3
"""bench-vr -- measure in-game frame rate at fixed locations through DevBench, before and after a change.

    python tools/bench-vr.py run --label before [--coc CELL ...] [--locations FILE] [--windows 3] [--window 30]
                                 [--settle 20] [--hour 12] [--weather FORMID] [--interval 1.0] [--port N] [--out DIR]
    python tools/bench-vr.py compare <before.json> <after.json>

Run it once before a graphics change and once after, with the same arguments, then `compare` the two results.
A comparison only means something when both runs share the conditions this pins -- timescale 0, one game hour,
one forced weather, god mode, the same locations, the same windows -- so `compare` refuses two runs whose
conditions differ.

Frame rate comes from DevBench's /api/health frame counter, which is answered OFF the game's main thread: a
main-thread stall shows up as a near-zero frame delta rather than as a missing sample.

Rules it keeps:
  * The first window at each location is DISCARDED (loading, shader compilation and the forced weather taking
    hold land there); the result pools windows 2..N, so --windows must be at least 2.
  * Everything it changes for the measurement is put back in a `finally`, also when the run fails or is
    interrupted: god mode is toggled back, and timescale and game hour are set back to the values read before the
    run. A value that could not be read or restored is printed with the console command to restore it by hand.
    The forced weather is not undone (the next weather change replaces it); use a save you can reload.
  * It never re-`coc`s into the cell the player is already in: re-entering the current cell re-fires its Story
    Manager checks, which has crashed the game. That location is measured where it stands.

Locations: --coc CELL (repeatable; a cell editor ID such as WhiterunOrigin), or --locations FILE with one per
line: a cell editor ID, or `cow <worldspace> <x> <y>`. Default: a single vanilla cell, WhiterunOrigin. Nothing else
is sent to the console from a locations file.

Port: --port, else $DEVBENCH_PORT, else Data/SKSE/Plugins/devbench/runtime.json (the port DevBench bound), else the
per-runtime default (VR 8921, SE/AE 8920). Results: <toolkit>/.claude/backups/bench-vr/ (never shipped) or --out.

Exit: 0 measured (or compared) | 1 incomplete measurement/comparison or a value could not be restored |
2 bad usage, DevBench unavailable/no save loaded, differing comparison conditions or no joint measurements.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skyrim_paths  # noqa: E402

DEFAULT_LOCATIONS = ["WhiterunOrigin"]
CELL_RE = re.compile(r"[A-Za-z0-9_]+")
COW_RE = re.compile(r"cow\s+([A-Za-z0-9_]+)\s+(-?\d+)\s+(-?\d+)", re.IGNORECASE)
NUMBER_RE = re.compile(r"(-?\d+(?:\.\d+)?)")
DEFAULT_OUT = skyrim_paths.TOOLKIT_ROOT / ".claude" / "backups" / "bench-vr"


class DevBenchError(Exception):
    pass


class Bench:
    """DevBench's REST surface over urllib."""

    def __init__(self, port: int, timeout: float = 20.0):
        self.base = "http://127.0.0.1:{}".format(port)
        self.timeout = timeout

    def _call(self, req):
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raise DevBenchError("HTTP {} from {}".format(e.code, req.full_url))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise DevBenchError("{} ({})".format(req.full_url, e))

    def health(self):
        return self._call(urllib.request.Request(self.base + "/api/health"))

    def tool(self, name, payload):
        data = json.dumps(payload).encode("utf-8")
        return self._call(urllib.request.Request(self.base + "/api/tool/" + name, data=data, method="POST",
                                                 headers={"Content-Type": "application/json"}))

    def console(self, command, settle=0.7):
        self.tool("console", {"action": "exec", "command": command, "capture": True})
        time.sleep(settle)
        return [str(x) for x in (self.tool("console", {"action": "read"}).get("lines") or [])]


def resolve_port(arg):
    if arg:
        return int(arg)
    if os.environ.get("DEVBENCH_PORT"):
        return int(os.environ["DEVBENCH_PORT"])
    game, _ = skyrim_paths.game_root()
    rt = game / "Data" / "SKSE" / "Plugins" / "devbench" / "runtime.json"
    try:
        port = json.loads(rt.read_text(encoding="utf-8")).get("port")
        if isinstance(port, int) and 0 < port < 65536:
            return port
    except (OSError, ValueError, AttributeError):
        pass
    return 8921 if (game / "SkyrimVR.exe").is_file() else 8920


def parse_locations(lines):
    """[(name, console command)]. Only `coc <cell>` and `cow <ws> <x> <y>` are ever produced."""
    out = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = COW_RE.fullmatch(s)
        if m:
            out.append(("{}_{}_{}".format(*m.groups()), "cow {} {} {}".format(*m.groups())))
        elif CELL_RE.fullmatch(s):
            out.append((s, "coc " + s))
        else:
            raise ValueError("not a cell editor ID or `cow <worldspace> <x> <y>`: {!r}".format(s))
    if not out:
        raise ValueError("no locations")
    return out


def stats(fps):
    if not fps:
        return {"samples": 0}
    s = sorted(fps)
    return {"samples": len(s), "median": round(statistics.median(s), 2), "mean": round(statistics.fmean(s), 2),
            "p5": round(s[max(0, int(len(s) * 0.05))], 2), "min": round(s[0], 2), "max": round(s[-1], 2),
            "stdev": round(statistics.pstdev(s), 2) if len(s) > 1 else 0.0,
            "stalls_under_5fps": sum(1 for x in s if x < 5)}


def sample(bench, seconds, interval):
    """One window: poll the off-thread frame counter and difference it."""
    fps, prev = [], None
    end = time.time() + seconds
    while time.time() < end:
        t0 = time.time()
        h = bench.health()
        t1 = time.time()
        if isinstance(h.get("frame"), int) and h["frame"] >= 0:
            f, t = h["frame"], (t0 + t1) / 2
            if prev is not None and t > prev[1]:
                fps.append((f - prev[0]) / (t - prev[1]))
            prev = (f, t)
        time.sleep(max(0.0, interval - (time.time() - t0)))
    return fps


def pooled_after_first(windows):
    """The rule: window 1 is discarded, windows 2..N are pooled."""
    return [x for w in windows[1:] for x in w]


def read_value(lines):
    for ln in lines:
        m = NUMBER_RE.search(ln.split("=", 1)[-1])
        if m and "=" in ln:
            return m.group(1)
    return None


def current_cell(bench):
    try:
        return ((bench.tool("inspect", {"kind": "scene"}).get("cell") or {}).get("editorId")) or ""
    except DevBenchError:
        return ""


def run(a) -> int:
    if a.windows < 2:
        print("ERROR: --windows must be at least 2: window 1 is always discarded", file=sys.stderr)
        return 2
    for name, value, positive in (("window", a.window, True), ("interval", a.interval, True),
                                  ("settle", a.settle, False)):
        if not math.isfinite(value) or value < 0 or (positive and value == 0):
            print("ERROR: --{} must be finite and {}".format(name, "positive" if positive else "nonnegative"), file=sys.stderr)
            return 2
    try:
        locs = parse_locations(a.coc or (Path(a.locations).read_text(encoding="utf-8").splitlines()
                                         if a.locations else DEFAULT_LOCATIONS))
    except (OSError, ValueError) as e:
        print("ERROR: locations: {}".format(e), file=sys.stderr)
        return 2
    try:
        port = resolve_port(a.port)
    except ValueError as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    bench = Bench(port)
    try:
        h = bench.health()
    except DevBenchError as e:
        print("ERROR: DevBench is not answering on port {} ({}). Start the game with DevBench installed and load a"
              " save; check Data/SKSE/Plugins/devbench/runtime.json or set DEVBENCH_PORT.".format(port, e),
              file=sys.stderr)
        return 2
    if not isinstance(h.get("frame"), int) or h["frame"] < 0:
        print("ERROR: DevBench answers on port {} but no save is loaded (frame {}). Load a save first."
              .format(port, h.get("frame")), file=sys.stderr)
        return 2

    out = Path(a.out) if a.out else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    result = {"label": a.label, "taken": time.strftime("%Y-%m-%dT%H:%M:%S"), "port": port,
              "conditions": {"timescale": 0, "gamehour": a.hour, "weather_form": a.weather,
                             "locations": [n for n, _ in locs], "windows": a.windows, "window_seconds": a.window,
                             "settle_seconds": a.settle, "window1_discarded": True},
              "results": {}}
    restore = []            # (what, console command) to run in the finally, newest first
    unrestored = []
    status = 0
    try:
        ts = read_value(bench.console("show timescale"))
        gh = read_value(bench.console("show gamehour"))
        result["restored_to"] = {"timescale": ts, "gamehour": gh}
        restore.append(("timescale", "set timescale to {}".format(ts) if ts else None))
        bench.console("set timescale to 0")
        restore.append(("gamehour", "set gamehour to {}".format(gh) if gh else None))
        bench.console("set gamehour to {}".format(a.hour))
        bench.console("fw {}".format(a.weather))
        restore.append(("god mode", "tgm"))
        bench.console("tgm")
        for name, cmd in locs:
            print("--- {}".format(name))
            here = current_cell(bench)
            if cmd.lower().startswith("coc ") and here.lower() == cmd[4:].lower():
                print("  already in {}: measured where it stands (re-entering the current cell can crash)".format(here))
            else:
                lines = bench.console(cmd, settle=2.0)
                if any("not found" in ln.lower() or "invalid" in ln.lower() for ln in lines):
                    print("  SKIPPED, the console said: {}".format(lines))
                    result["results"][name] = {"error": lines}
                    status = 1
                    continue
                for _ in range(120):
                    hh = bench.health()
                    if hh.get("lastLifecycle") == "postLoadGame" and isinstance(hh.get("frame"), int) and hh["frame"] > 0:
                        break
                    time.sleep(0.5)
                bench.console("set gamehour to {}".format(a.hour))
                bench.console("fw {}".format(a.weather))
            time.sleep(a.settle)
            windows = []
            for w in range(1, a.windows + 1):
                fps = sample(bench, a.window, a.interval)
                windows.append(fps)
                st = stats(fps)
                print("  window {}{}: median {} fps  p5 {}  n={}".format(
                    w, " (discarded)" if w == 1 else "", st.get("median"), st.get("p5"), st.get("samples")))
            pooled = stats(pooled_after_first(windows))
            result["results"][name] = {"windows": [stats(w) for w in windows], "pooled_excluding_window1": pooled,
                                       "cell": current_cell(bench)}
            if not pooled["samples"]:
                result["results"][name]["error"] = "no retained frame-rate samples"
                status = 1
            print("  POOLED (windows 2+): median {} fps  p5 {}  stalls {}".format(
                pooled.get("median"), pooled.get("p5"), pooled.get("stalls_under_5fps")))
    except (DevBenchError, KeyboardInterrupt) as e:
        print("ERROR: the run stopped: {}. Putting back what it changed.".format(e or "interrupted"), file=sys.stderr)
        result["error"] = str(e) or "interrupted"
        status = 1
    finally:
        for what, cmd in reversed(restore):
            if cmd is None:
                unrestored.append((what, "its value could not be read before the run; set it by hand"))
                continue
            try:
                bench.console(cmd)
            except DevBenchError as e:
                unrestored.append((what, "run `{}` in the console ({})".format(cmd, e)))
        result["unrestored"] = [{"what": w, "how": h_} for w, h_ in unrestored]
        path = out / "bench-{}-{}.json".format(re.sub(r"[^A-Za-z0-9_.-]", "_", a.label), time.strftime("%Y%m%d-%H%M%S"))
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("wrote {}".format(path))
        for what, how in unrestored:
            print("NOT RESTORED: {}: {}".format(what, how))
    return 1 if (status or unrestored) else 0


def compare(before_path, after_path) -> int:
    try:
        a = json.loads(Path(before_path).read_text(encoding="utf-8"))
        b = json.loads(Path(after_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    keys = ("gamehour", "weather_form", "locations", "windows", "window_seconds", "settle_seconds",
            "window1_discarded")
    diff = [k for k in keys if a.get("conditions", {}).get(k) != b.get("conditions", {}).get(k)]
    if diff:
        print("ERROR: the runs were not taken under the same conditions ({}); the comparison would mean nothing"
              .format(", ".join(diff)), file=sys.stderr)
        return 2
    print("{} -> {}".format(a.get("label"), b.get("label")))
    compared, missing = 0, 0
    for loc in a["conditions"]["locations"]:
        pa = (a["results"].get(loc) or {}).get("pooled_excluding_window1") or {}
        pb = (b["results"].get(loc) or {}).get("pooled_excluding_window1") or {}
        if not pa.get("samples") or not pb.get("samples"):
            print("  {:<28} not measured in both runs".format(loc))
            missing += 1
            continue
        compared += 1
        d = pb["median"] - pa["median"]
        print("  {:<28} median {:>7} -> {:>7}  ({:+.2f}, {:+.1f}%)   p5 {} -> {}".format(
            loc, pa["median"], pb["median"], d, 100.0 * d / pa["median"] if pa["median"] else 0.0, pa["p5"], pb["p5"]))
    return 2 if not compared else (1 if missing else 0)


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="bench-vr.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--coc", action="append", help="a cell editor ID (repeatable)")
    r.add_argument("--locations", help="a file: one cell editor ID or `cow <ws> <x> <y>` per line")
    r.add_argument("--windows", type=int, default=3)
    r.add_argument("--window", type=float, default=30)
    r.add_argument("--settle", type=float, default=20)
    r.add_argument("--hour", default="12")
    r.add_argument("--weather", default="0010E1EF",
                   help="a weather FormID (default 0010E1EF: Skyrim.esm's SkyrimCloudySN_A, which every install has; "
                        "a weather mod may change how it looks -- what matters is the same form in both runs)")
    r.add_argument("--interval", type=float, default=1.0)
    r.add_argument("--port")
    r.add_argument("--out")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if a.cmd == "run":
        if not re.fullmatch(r"\d{1,2}(\.\d+)?", a.hour) or not re.fullmatch(r"[0-9A-Fa-f]{1,8}", a.weather):
            print("ERROR: --hour must be a number and --weather a FormID", file=sys.stderr)
            return 2
        return run(a)
    if a.cmd == "compare":
        return compare(a.before, a.after)
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
