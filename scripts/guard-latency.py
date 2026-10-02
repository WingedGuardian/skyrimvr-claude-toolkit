#!/usr/bin/env python3
"""Wrapper latency (spec M4): time N invocations of each hook wrapper through `bash <wrapper>`.

Run against a CONFIGURED copy (setup.sh already run), on an otherwise idle machine:

    python scripts/guard-latency.py <configured-install> [--runs 50] [--json out.json]

Payloads are corpus rows (tests/guard_corpus/expected.jsonl) with the corpus game path rewritten to
the install's own. Gate: p95 <= 1000 ms for every wrapper. Plan 1 spike (E10): Python 3.10 p95 60-64 ms,
Python 3.12 Store alias 81-85 ms, old bash guard 297-670 ms.
"""
import argparse, json, os, statistics, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
ROWS = {  # wrapper -> corpus row ids
    "protect-bash": ["shell-delete-inside", "shell-benign-git", "shell-cp-inside"],
    "protect-files": ["write-esp", "write-pex", "write-outside"],
}
SIMPLE = {
    "backup-before-edit": lambda g: {"tool_name": "Edit", "tool_input": {"file_path": f"{g}/Data/a.ini"}},
    "snapshot-before-tool": lambda g: {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    "session-kb-guard": lambda g: {"hook_event_name": "SessionStart", "source": "startup"},
}


def git_bash() -> str:
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.isfile(c):
            return c
    return "bash"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("install")
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--json")
    a = ap.parse_args()
    inst = Path(a.install).resolve()
    game = inst.as_posix()
    corpus = {}
    for line in (REPO / "tests/guard_corpus/expected.jsonl").read_text(encoding="utf-8").split("\n"):
        if line.strip():
            r = json.loads(line)
            corpus[r["id"]] = r
    bash = git_bash()
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(inst))
    results, worst = {}, 0.0
    for hook in ["protect-bash", "protect-files", "backup-before-edit", "snapshot-before-tool", "session-kb-guard"]:
        payloads = ([json.dumps(corpus[i]["payload"]).replace(GAME, game) for i in ROWS[hook]]
                    if hook in ROWS else [json.dumps(SIMPLE[hook](game))])
        wrapper = (inst / ".claude" / "hooks" / f"{hook}.sh").as_posix()
        ms = []
        for n in range(a.runs):
            p = payloads[n % len(payloads)].encode("utf-8")
            t = time.perf_counter()
            subprocess.run([bash, wrapper], input=p, capture_output=True, env=env, timeout=60)
            ms.append((time.perf_counter() - t) * 1000)
        ms.sort()
        p95 = ms[min(len(ms) - 1, int(0.95 * len(ms)))]
        results[hook] = {"runs": len(ms), "min": ms[0], "median": statistics.median(ms), "p95": p95, "max": ms[-1]}
        worst = max(worst, p95)
        print(f"{hook:22s} n={len(ms)} min={ms[0]:6.0f} median={statistics.median(ms):6.0f} p95={p95:6.0f} max={ms[-1]:6.0f} ms")
    print(f"WORST p95 = {worst:.0f} ms  gate <= 1000 ms: {'PASS' if worst <= 1000 else 'FAIL'}")
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1))
    return 0 if worst <= 1000 else 1


if __name__ == "__main__":
    sys.exit(main())
