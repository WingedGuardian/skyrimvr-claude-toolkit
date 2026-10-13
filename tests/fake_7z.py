"""A stand-in for 7-Zip's console program, for tests/test_archive_triage.py.

The "archive" is a JSON file: {"entries": [{"path": ..., "data": "<text>", "crc": optional override ("" = none),
"attributes": optional, "folder": optional bool}]}. Anything that is not such JSON is refused the way 7-Zip
refuses a file it cannot open (exit 2). It answers the two commands archive-triage runs:

    l -slt -ba [switches] <archive>          one block per entry, CRLF line ends as 7-Zip writes them on Windows
    e -so [switches] <archive> -- <entry>    the entry's bytes on stdout
    x [switches] -o<dir> <archive> @<list>   the listed entries written under <dir> (FAKE_7Z_DROP=<entry> skips one)

The tests/bin shims are bash PATH shims; a Python tool on Windows cannot execute one, so each test writes a tiny
launcher (7z.cmd / 7z) that runs this file, and points SEVENZIP at it.
"""
import json
import os
import sys
import zlib


def main(argv):
    cmd, rest = argv[0], argv[1:]
    inner = None
    if "--" in rest:
        i = rest.index("--")
        inner, rest = rest[i + 1], rest[:i]
    positional = [a for a in rest if not a.startswith("-")]
    arc = positional[0]
    try:
        with open(arc, encoding="utf-8") as fh:
            entries = json.load(fh)["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        sys.stderr.write("ERROR: {}\nCannot open the file as archive\n".format(arc))
        return 2
    out = sys.stdout.buffer
    if cmd == "l":
        for e in entries:
            data = e.get("data", "").encode("utf-8")
            crc = e["crc"] if "crc" in e else "{:08X}".format(zlib.crc32(data) & 0xFFFFFFFF)
            block = ["Path = " + e["path"], "Folder = " + ("+" if e.get("folder") else "-"),
                     "Size = {}".format(len(data)), "Attributes = " + e.get("attributes", " 01800000"),
                     "CRC = " + ("" if e.get("folder") else crc)]
            out.write(("\r\n".join(block) + "\r\n\r\n").encode("utf-8"))
        return 0
    if cmd == "x":
        # x -y -spd -scsUTF-8 -o<dir> <archive> @<list file>: write each listed entry under <dir>
        outdir = next(a[2:] for a in rest if a.startswith("-o"))
        listfile = next(a[1:] for a in argv[1:] if a.startswith("@"))
        with open(listfile, encoding="utf-8") as fh:
            wanted = [ln for ln in fh.read().split("\n") if ln]
        by_path = {e["path"]: e for e in entries}
        for name in wanted:
            e = by_path.get(name)
            if e is None or os.environ.get("FAKE_7Z_DROP") == name:
                continue                                  # 7-Zip skips a name it does not have
            dest = os.path.join(outdir, *[s for s in name.replace("\\", "/").split("/") if s])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as out_f:
                out_f.write(e.get("data", "").encode("utf-8"))
        return 0
    if cmd == "e":
        for e in entries:
            if e["path"] == inner:
                out.write(e.get("data", "").encode("utf-8"))
                return 0
        sys.stderr.write("No files to process\n")
        return 1
    sys.stderr.write("unsupported command {}\n".format(cmd))
    return 7


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
