#!/usr/bin/env python3
"""Adapter template (ADAPTING.md, section 6). tools/adapting/adapter_template.py

Copy to tools/adapters/<name>/adapter.py and edit ONLY the block between SLOTS-BEGIN and SLOTS-END.
Everything else is the fail-closed machinery; tools/adapter_conformance.py checks that it is unchanged.

What it does, per tool call: read the agent's payload from stdin -> map_payload() -> run the toolkit
engine (`run.py neutral pre-tool`) -> check the answer -> render the verdict as THIS agent's hard block.
If anything at all goes wrong it renders the same hard block, because an agent that treats a failed hook
as "allow" would otherwise run the command (that is how Codex behaves: spec E1, E17).
Python >= 3.8, standard library only.
"""
import io
import json
import os
import re
import subprocess
import sys
import time

# SLOTS-BEGIN
AGENT_NAME = "unnamed"   # letters, digits, dot, dash: names the heartbeat folder

# What this agent obeys as a HARD block, as a literal: (stdout text, exit code, stderr text).
# MEASURE that your agent really stops the call on this shape. Unfilled, this is exit 2 + stderr, which
# many agents honour, so an unfilled adapter refuses every call.
STATIC_BLOCK = ("", 2, "GUARD INERT: this adapter is not set up, so every call is refused.")

ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")


def map_payload(raw):
    """The agent's payload (bytes) -> the request dict of ADAPTING.md section 4."""
    raise NotImplementedError("fill map_payload")


def render_block(reason):
    """-> (stdout text, exit code, stderr text) that your agent honours as a hard block, with reason shown."""
    raise NotImplementedError("fill render_block")


def render_allow(context):
    """-> (stdout text, exit code, stderr text) that lets the call run; context (maybe '') is advice."""
    raise NotImplementedError("fill render_allow")


render_ask = None   # set to a function(reason) only after measuring that your agent pauses for the user
# SLOTS-END

ENGINE_BUDGET_S = 20      # the engine's own watchdog (SKYGUARD_ENGINE_TIMEOUT)
ADAPTER_TIMEOUT_S = 25    # this adapter's deadline. Your agent's hook timeout MUST be longer than this.
MARKER = "SKYGUARD-OK\n"
_UNSAFE = re.compile(r"[^A-Za-z0-9 ._:/()=,+-]")


def _root():
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isfile(os.path.join(d, "tools", "skyguard", "run.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def _hb_dir(root):
    return os.path.join(root, ".claude", "backups", ".hook-heartbeat", "adapted",
                        re.sub(r"[^A-Za-z0-9._-]", "_", AGENT_NAME))


def _hb(root, stage, inv, detail=""):
    """Best-effort heartbeat; never decides anything."""
    try:
        d = _hb_dir(root)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "pre-tool." + stage), "w") as f:
            f.write("{:.6f} {} {}\n".format(time.time(), inv, detail))
    except Exception:
        pass


def _engine(req_json, root, inv):
    run_py = os.path.join(root, "tools", "skyguard", "run.py")
    env = dict(os.environ)
    env.setdefault("SKYGUARD_ENGINE_TIMEOUT", str(ENGINE_BUDGET_S))
    cp = subprocess.run(
        [sys.executable, run_py, "neutral", "pre-tool", "--root", root, "--root-win", root,
         "--project", root, "--invocation", inv, "--telemetry", _hb_dir(root)],
        input=req_json.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=float(os.environ.get("SKYGUARD_ADAPTER_TIMEOUT_S", ADAPTER_TIMEOUT_S)), env=env)
    if cp.returncode != 0:
        raise RuntimeError("engine exited {}".format(cp.returncode))
    text = cp.stdout.decode("utf-8")
    if not text.startswith(MARKER):
        raise RuntimeError("engine answered without the marker")
    v = json.loads(text[len(MARKER):])
    if not isinstance(v, dict) or v.get("v") != 1 or v.get("decision") not in ("allow", "ask", "deny"):
        raise RuntimeError("engine verdict malformed")
    return v


def _checked(t):
    ok = (isinstance(t, tuple) and len(t) == 3 and isinstance(t[0], str)
          and isinstance(t[1], int) and not isinstance(t[1], bool) and isinstance(t[2], str))
    if not ok:
        raise RuntimeError("a render slot did not return (stdout text, exit code, stderr text)")
    return t


def _render(v):
    decision = v["decision"]
    reason = str(v.get("reason", ""))
    context = str(v.get("context", ""))
    if decision == "deny":
        return _checked(render_block(reason))
    if decision == "ask":
        if render_ask is not None:
            return _checked(render_ask(reason))
        return _checked(render_block(ASK_PREFIX + reason))
    return _checked(render_allow(context))


def _block_for_failure(why):
    reason = ("GUARD INERT: the adapter could not get a verdict from the toolkit engine ({}). NOTHING was "
              "checked: this is a refusal, not a decision. Tell the user.").format(_UNSAFE.sub("", why)[:120])
    try:
        return _checked(render_block(reason))
    except BaseException:
        return STATIC_BLOCK


def _emit(out, code, err):
    """Write UTF-8 BYTES, never text: a piped Windows stdout is cp1252 and raises on a reason it cannot encode,
    and this runs outside main()'s try. Any failure falls back to STATIC_BLOCK on raw file descriptors."""
    try:
        o, e = out.encode("utf-8", "replace"), err.encode("utf-8", "replace")
        sys.stdout.buffer.write(o)
        sys.stdout.buffer.flush()
        if e:
            sys.stderr.buffer.write(e)
            sys.stderr.buffer.flush()
    except BaseException:
        try:
            so, code, se = STATIC_BLOCK
            os.write(1, so.encode("utf-8", "replace"))
            if se:
                os.write(2, se.encode("utf-8", "replace"))
        except BaseException:
            code = 2
    sys.exit(code)


def main():
    real_stdout = sys.stdout
    sys.stdout = io.StringIO()      # a slot's print() must never reach the agent: only _emit writes stdout
    root = _root()
    inv = "1.{}-{}".format(os.getpid(), int(time.time() * 1000) % 100000)
    try:
        _hb(root, "invoked", inv)
        raw = sys.stdin.buffer.read()
        if not root:
            raise RuntimeError("cannot locate the toolkit root")
        if not raw.strip():
            raise RuntimeError("empty payload")
        req = map_payload(raw)
        if not isinstance(req, dict):
            raise RuntimeError("map_payload did not return a dict")
        v = _engine(json.dumps(req), root, inv)
        out, code, err = _render(v)
        _hb(root, "emitted", inv, v["decision"])
    except BaseException as e:
        out, code, err = _block_for_failure(e.__class__.__name__ + " " + str(e))
        _hb(root, "failed", inv, e.__class__.__name__)
    sys.stdout = real_stdout
    _emit(out, code, err)


if __name__ == "__main__":
    main()
