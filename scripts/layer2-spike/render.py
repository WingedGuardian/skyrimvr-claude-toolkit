"""Render a primitives.py result JSON as the markdown matrix used in the ml2 doc.

    python render.py <results.json>
"""

from __future__ import annotations

import collections
import json
import sys

AB = {"BLOCKED": "BLOCKED", "NOT BLOCKED": "**open**", "READ OK": "ok", "READ FAILED": "**FAIL**",
      "UNMEASURED (control failed)": "UNMEASURED"}
MECHS = ["M-A", "M-B", "M-C", "M-D"]


def main(path):
    r = json.load(open(path, encoding="utf-8"))
    order = []
    for x in r:
        if x["row"] not in order:
            order.append(x["row"])
    cell = {(x["mech"], x["row"]): x for x in r}
    print("| Row | Kind | " + " | ".join(MECHS) + " | Control (all) |")
    print("|---|---|" + "---|" * len(MECHS) + "---|")
    for row in order:
        cs = [cell[(m, row)] for m in MECHS if (m, row) in cell]
        ctl = "changed" if all(c["control"].get("changed") or not c["control"]["exists"] for c in cs) else "see json"
        if cs[0]["kind"] in ("read", "stat"):
            ctl = "read ok"
        print("| %s | %s | %s | %s |" % (row, cs[0]["kind"], " | ".join(AB[cell[(m, row)]["verdict"]] for m in MECHS),
                                        ctl))
    print()
    print("| Mechanism | write rows BLOCKED | write rows open | delete rows BLOCKED | reads OK |")
    print("|---|---|---|---|---|")
    for m in MECHS:
        xs = [x for x in r if x["mech"] == m]
        w = [x for x in xs if x["kind"] == "write"]
        d = [x for x in xs if x["kind"] == "delete"]
        rd = [x for x in xs if x["kind"] in ("read", "stat")]
        print("| %s | %d / %d | %s | %d / %d | %d / %d |" % (
            m, sum(x["verdict"] == "BLOCKED" for x in w), len(w),
            ", ".join(x["row"] for x in w if x["verdict"] == "NOT BLOCKED") or "none",
            sum(x["verdict"] == "BLOCKED" for x in d), len(d),
            sum(x["verdict"] == "READ OK" for x in rd), len(rd)))


if __name__ == "__main__":
    main(sys.argv[1])
