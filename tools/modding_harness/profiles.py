"""Game-owned membership rules; adding a game does not change preservation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def canonical(path):
    return os.path.normcase(str(Path(path).resolve()))


@dataclass(frozen=True)
class GameProfile:
    name: str
    save_roots: tuple[Path, ...]
    extensions: tuple[str, ...]
    paired: bool = False

    def members(self, path):
        path = Path(path).absolute()
        suffix = next((ext for ext in self.extensions if path.name.lower().endswith(ext)), None)
        if suffix is None:
            raise ValueError("not a recognized {} save: {}".format(self.name, path))
        if not self.paired:
            return (path,)
        stem = path.name[:-len(suffix)]
        result = []
        for ext in self.extensions:
            expected = path.with_name(stem + ext)
            # A pair needs one unambiguous physical member per suffix. Do not
            # silently collapse distinct case variants on a POSIX/case-enabled root.
            if path.parent.is_dir():
                hits = [p for p in path.parent.iterdir() if p.name.casefold() == expected.name.casefold()]
                if len(hits) > 1:
                    raise ValueError("ambiguous save member identity")
                if hits:
                    expected = hits[0]
            result.append(expected)
        return tuple(result)


def skyrim(roots=()):
    return GameProfile("skyrim", tuple(map(Path, roots)), (".ess", ".skse"), True)


def x4(roots=()):
    return GameProfile("x4", tuple(map(Path, roots)), (".xml.gz",))


def toy(roots=()):
    return GameProfile("toy", tuple(map(Path, roots)), (".save",))
