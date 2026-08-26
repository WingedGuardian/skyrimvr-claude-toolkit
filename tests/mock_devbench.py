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
    if MODE == "running":
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

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if MODE == "busy504":
            return self._send(504, {"error": "main thread did not run task in time",
                                    "code": 504})
        if MODE == "badarg400":
            return self._send(400, {"error": "kind must be a string", "code": 400})
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
