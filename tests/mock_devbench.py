"""A stand-in for DevBench's REST surface, so devbench-cli.sh can be tested
without a running copy of Skyrim.

Run as:  python mock_devbench.py <port> <mode>

Each mode reproduces one state the real server can be in. The liveness shapes
come from DevBench's own /api/health contract: `frame` advancing means the game
is running, a frozen frame with an idle task queue is a pause rather than a
hang, and a frozen frame with work piling up while `lastTaskFrame` stalls is a
genuinely starved main thread.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
MODE = sys.argv[2]

_state = {"n": 0}
_lock = threading.Lock()


def _tick() -> int:
    with _lock:
        _state["n"] += 1
        return _state["n"]


def health_body() -> dict:
    n = _tick()
    common = {"pid": 4242, "port": PORT, "exe": "SkyrimVR.exe", "vr": True}
    if MODE in ("running", "bench"):
        return {"ok": True, "lastLifecycle": "postLoadGame", "frame": 1000 + n * 90,
                "lastTaskFrame": 990 + n * 90, "pendingTasks": 0, **common}
    if MODE == "paused":
        # Frame frozen, but the task queue is draining -- a menu, not a hang.
        return {"ok": True, "lastLifecycle": "postLoadGame", "frame": 5000,
                "lastTaskFrame": 4998, "pendingTasks": 0, **common}
    if MODE == "hung":
        # Frame frozen AND work queued that never completes.
        return {"ok": True, "lastLifecycle": "postLoadGame", "frame": 7777,
                "lastTaskFrame": 7770, "pendingTasks": 3, **common}
    if MODE == "notingame":
        # Server up, no save loaded: the frame counter is unresolved.
        return {"ok": True, "lastLifecycle": None, "frame": -1,
                "lastTaskFrame": -1, "pendingTasks": 0, **common}
    raise AssertionError(f"unknown mode {MODE}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the test output clean
        pass

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/health":
            if MODE == "legacy":  # pre-1.11.0: the endpoint does not exist
                return self._send(404, {"error": "not found"})
            return self._send(200, health_body())
        if self.path == "/api/tools":
            return self._send(200, {"tools": ["ping", "inspect"]})
        self._send(404, {"error": "not found"})

    def _bench(self, body: dict):
        """Mode "bench", for tests/test_bench_vr.py: a console that answers the commands bench-vr.py sends and
        logs each one (one per line) to $MOCK_LOG. $MOCK_FAIL_ON=<text> makes the console exec of a command that
        contains it answer HTTP 500, once, so a run can be failed partway. $MOCK_CELL is the current cell."""
        if self.path == "/api/tool/inspect":
            return self._send(200, {"cell": {"editorId": os.environ.get("MOCK_CELL", "SomeInterior")},
                                    "weather": {"editorId": "SkyrimCloudySN_A"}})
        if self.path != "/api/tool/console":
            return self._send(200, {"ok": True})
        if body.get("action") == "exec":
            cmd = str(body.get("command"))
            with open(os.environ["MOCK_LOG"], "a", encoding="utf-8") as fh:
                fh.write(cmd + "\n")
            fail = os.environ.get("MOCK_FAIL_ON")
            if fail and fail in cmd and not _state.get("failed"):
                _state["failed"] = True
                return self._send(500, {"error": "injected failure", "code": 500})
            _state["last"] = cmd
            return self._send(200, {"ok": True})
        last = _state.get("last", "")
        answers = {"show timescale": ["TimeScale = 20.00"], "show gamehour": ["GameHour = 9.50"],
                   "tgm": ["God Mode -> On"]}
        lines = answers.get(last, ["Cell 'NoSuchCell' not found"] if "NoSuchCell" in last else [])
        return self._send(200, {"lines": lines})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        if MODE == "busy504":
            return self._send(504, {"error": "main thread did not run task in time",
                                    "code": 504})
        if MODE == "badarg400":
            return self._send(400, {"error": "kind must be a string", "code": 400})
        if MODE == "bench":
            return self._bench(body)
        if self.path == "/api/tool/ping":
            return self._send(200, {"ok": True})
        if self.path == "/api/tool/inspect":
            # Legacy path: the main-thread frame counter, advancing.
            return self._send(200, {"plugin": "devbench", "vr": True,
                                    "playerLoaded": True, "frame": 2000 + _tick() * 90})
        self._send(200, {"ok": True})


if __name__ == "__main__":
    # Bind first, then announce the port we actually got. Passing 0 lets the OS
    # choose, which removes the race in "find a free port, close it, hope it is
    # still free when the server binds it".
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"PORT={server.server_address[1]}", flush=True)
    server.serve_forever()
