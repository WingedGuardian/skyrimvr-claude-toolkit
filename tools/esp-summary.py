#!/usr/bin/env python3
"""What does a plugin add, and what does it override? READ-ONLY: parses the bytes directly (tools/esp_records.py),
independent of xEdit, so it is a second opinion rather than the same one repeated.

    python tools/esp-summary.py PLUGIN [PLUGIN ...] [--list-overrides]

Per plugin: masters, header flags, NEW records by type, OVERRIDES by type and by master, and anything the parser
could read only partly (a duplicate FormID, a compressed record that does not inflate, a FormID whose master index is
past the master list). `--list-overrides` also prints one line per overridden record. Nothing is ever written.

A record is an override when its FormID's high byte indexes one of the plugin's own masters; which plugin finally
wins that record in a load order is a different question (tools/skyrim-winner.sh).

Exit: 0 every plugin read cleanly · 1 a plugin was read only partly (see "problems") · 2 usage, or a file that is not
a plugin this parser can walk.
"""
from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from esp_records import PluginError, read  # noqa: E402


def _counts(records):
    return ", ".join("{} {}".format(t, n) for t, n in sorted(collections.Counter(r.type for r in records).items())) \
        or "none"


def summarize(path, list_overrides=False) -> int:
    plugin = read(path)
    over = [r for r in plugin.records if plugin.is_override(r)]
    new = [r for r in plugin.records if not plugin.is_override(r)]
    print("== {}".format(os.path.basename(path)))
    print("   masters ({}): {}".format(len(plugin.masters), ", ".join(plugin.masters) or "none"))
    print("   header flags: {:08X} ({})".format(plugin.header.flags, ", ".join(plugin.header_flag_names()) or "none"))
    print("   records: {} ({} new, {} overrides)".format(len(plugin.records), len(new), len(over)))
    print("   new: {}".format(_counts(new)))
    print("   overrides: {}".format(_counts(over)))
    by_master = collections.Counter(plugin.master_of(r) for r in over)
    print("   overrides by master: {}".format(", ".join("{} {}".format(m, n) for m, n in by_master.items()) or "none"))
    if list_overrides:
        for r in sorted(over, key=lambda r: r.formid):
            print("   OVERRIDE {:08X} {} {} ({})".format(r.formid, r.type, r.edid, plugin.master_of(r)))
    for p in plugin.problems:
        print("   PROBLEM {}".format(p))
    print("   problems: {}".format(len(plugin.problems)))
    return 1 if plugin.problems else 0


def main(argv) -> int:
    list_overrides = "--list-overrides" in argv
    unknown = [a for a in argv if a.startswith("--") and a != "--list-overrides"]
    paths = [a for a in argv if not a.startswith("--")]
    if unknown or not paths:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    worst = 0
    for p in paths:
        try:
            worst = max(worst, summarize(p, list_overrides))
        except (OSError, PluginError) as e:
            print("esp-summary: {}: {}".format(p, e), file=sys.stderr)
            worst = 2
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
