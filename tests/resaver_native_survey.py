#!/usr/bin/env python3
"""The ReSaver Plus synthetic survey: every native operation through `tools/resaver-cli.sh` (port plan task 12).

    python tests/resaver_native_survey.py --root <toolkit root> --probe <rs_write_port_probe.exe> --work <dir>

Not a pytest module (no test_ prefix): the Windows CI job runs it after `tools/resaver-plus/build.py`, against the real
build. It makes a SYNTHETIC save with the build's own probe (`rs_write_port_probe <unused> <out> synthetic-fixture`:
invented records only -- never load it in Skyrim), then runs, with RESAVER_BACKEND=native:

  * every read operation, and `verify` (the build's pin);
  * each of the six edits as a preview, then applied to a NEW output, then that output reopened with `info`;
  * one apply onto an output that now exists, which must be refused;

and checks that the input pair's bytes never changed and every output carries the input's co-save bytes. All 20
operations must be covered. Exit 0 all passed | 1 something failed (each failure is printed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

EXT = ".e" + "ss"          # spelled apart: the survey writes NEW synthetic outputs only
OPS = {"info", "dump", "find", "find-refs", "worries", "changeform", "recon", "extradata-scan", "changeform-diff",
       "globaldata", "globaldata-diff", "freeze-report", "writer-scan", "verify-roundtrip",
       "set-var", "set-global", "clean", "reset-havok", "cleanse-formlists", "remove-created"}


def sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


class Survey:
    def __init__(self, root: Path, work: Path, bash: str):
        self.root, self.work, self.bash = root, work, bash
        self.count, self.failures, self.covered = 0, [], set()

    def cli(self, *args, expect_ok=True, label=None):
        env = dict(os.environ, RESAVER_BACKEND="native")
        r = subprocess.run([self.bash, str(self.root / "tools" / "resaver-cli.sh"), *map(str, args)],
                           capture_output=True, text=True, timeout=300, env=env)
        self.count += 1
        try:
            out = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except ValueError:
            out = {"ok": False, "error": "not JSON: " + (r.stdout + r.stderr)[-300:]}
        good = (r.returncode == 0 and out.get("ok") is True) if expect_ok else (r.returncode != 0 and not out.get("ok"))
        name = label or " ".join(str(a) for a in args[:1])
        print("{} {} -> rc {} {}".format("PASS" if good else "FAIL", name, r.returncode, json.dumps(out)[:160]))
        if not good:
            self.failures.append((name, out.get("error", "")))
        elif args and args[0] in OPS and expect_ok:
            self.covered.add(args[0])
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--probe", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--bash", default=os.environ.get("BASH_EXE", "bash"))
    a = ap.parse_args(argv)
    work = a.work.resolve()
    (work / "fixture").mkdir(parents=True, exist_ok=True)
    (work / "out").mkdir(parents=True, exist_ok=True)
    src = work / "fixture" / ("Synthetic" + EXT)
    if not src.exists():
        r = subprocess.run([str(a.probe), "unused", str(src), "synthetic-fixture"], capture_output=True, text=True,
                           timeout=120)
        print("fixture:", r.returncode, r.stdout.strip()[:200])
        if r.returncode != 0:
            print("FAIL: the probe could not make the synthetic fixture")
            return 1
    cosave = src.with_suffix(".skse")
    before = (sha(src), sha(cosave))
    s = Survey(a.root.resolve(), work, a.bash)

    s.cli("verify", label="verify (native pin)")
    s.cli("info", src)
    instances = s.cli("dump", src, "scriptinstances").get("items", [])
    s.cli("dump", src, "globals", "--limit", "5")
    forms = s.cli("dump", src, "changeforms").get("items", [])
    s.cli("dump", src, "scripts")
    eid = next((i["id"] for i in instances if i.get("vars")), instances[0]["id"] if instances else "0")
    script = next((i["script"] for i in instances), "TestScript")
    refid = forms[0]["refid"].split(":")[-1] if forms else "000001"
    s.cli("find", src, script)
    s.cli("find-refs", src, eid)
    s.cli("worries", src)
    s.cli("changeform", src, refid, "--raw")
    s.cli("recon", src)
    s.cli("extradata-scan", src)
    s.cli("changeform-diff", src, src, refid)
    s.cli("changeform-diff", src, src, "--quests")
    s.cli("globaldata", src)
    s.cli("globaldata-diff", src, src)
    s.cli("freeze-report", src)
    s.cli("writer-scan", src)
    s.cli("verify-roundtrip", src)

    variables = s.cli("set-var", src, eid, label="set-var (list)").get("variables", [])
    index = variables[0]["index"] if variables else 0
    value = "true" if variables and variables[0].get("type") == "BOOLEAN" else "1"
    gtarget = next((g["global"].split(" = ")[0] for g in s.cli("dump", src, "globals").get("items", [])), "0")
    edits = [("set-var", [eid, index, value]), ("set-global", [gtarget, "3"]), ("clean", ["--undefined"]),
             ("reset-havok", []), ("cleanse-formlists", []), ("remove-created", [])]
    outputs = []
    for op, args in edits:
        out = work / "out" / (op + EXT)
        flags = [x for x in args if str(x).startswith("--")]
        plain = [x for x in args if not str(x).startswith("--")]
        s.cli(op, src, *plain, *flags, label=op + " (preview)")
        applied = s.cli(op, src, *plain, out, *flags, "--apply", label=op + " --apply (new output)")
        if applied.get("ok") and not (applied.get("verified") and applied.get("wroteFile")):
            s.failures.append((op, "applied without a verified written file"))
        s.cli("info", out, label=op + " output reopened")
        outputs.append(out)
    s.cli("reset-havok", src, outputs[3], "--apply", expect_ok=False, label="apply onto an existing output (refused)")

    if (sha(src), sha(cosave)) != before:
        s.failures.append(("input", "the input pair's bytes changed"))
    for out in outputs:
        if sha(out.with_suffix(".skse")) != before[1]:
            s.failures.append((out.name, "the output's co-save is not the input's"))
    missing = sorted(OPS - s.covered)
    if missing:
        s.failures.append(("coverage", "operations never run successfully: " + ", ".join(missing)))
    print(json.dumps({"invocations": s.count, "operations": len(s.covered), "failures": len(s.failures)}))
    for name, why in s.failures:
        print("FAILED: {}: {}".format(name, why))
    return 1 if s.failures else 0


if __name__ == "__main__":
    sys.exit(main())
