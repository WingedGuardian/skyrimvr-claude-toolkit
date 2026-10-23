"""Game-owned membership rules; adding a game does not change preservation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def comparison_key(resolved):
    """Normalize a resolved identity, including persisted keys, without file I/O."""
    if os.name == "nt" and resolved.startswith("\\\\?\\"):
        # Normalize only the comparison key AFTER native resolution. Keep the
        # caller's original extended spelling for all file I/O and manifests.
        tail = resolved[4:]
        if len(tail) >= 3 and tail[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" and tail[1:3] == ":\\":
            resolved = tail
        elif tail[:4].casefold() == "unc\\":
            resolved = "\\\\" + tail[4:]
    return os.path.normcase(resolved)


def _resolved(path):
    """Keep native resolution while bounding probes of a deeply missing tail.

    Windows' non-strict realpath retries every shrinking prefix. A long absent
    tail therefore repeats work on the same characters. Strict native probes
    locate its first missing component, whose ordinary non-strict resolution
    retains junction/dangling-link semantics; the absent tail is appended once.
    Other errors, dot-parent and embedded-colon semantics use the original
    resolver unchanged.
    """
    original = os.fspath(path)
    value = Path(path)
    parts = value.parts
    _drive, tail = os.path.splitdrive(original)
    components = tail.replace("/", "\\").split("\\") if os.name == "nt" else ()
    if (os.name != "nt" or len(parts) < 32
            # Rebuilding prefixes or appending a tail can reinterpret C:parts.
            # Delegate every non-anchor colon, including ADS-like spellings.
            or any(":" in component for component in components)
            or ".." in components or (_drive.startswith(("\\\\?\\", "\\\\.\\")) and "." in components)):
        return str(value.resolve())
    if not value.drive:
        # Like native realpath, a rooted operand uses the process's current
        # drive; an ordinary relative operand uses its complete current cwd.
        value = Path.cwd() / value
        parts = value.parts
    if not value.is_absolute():          # leave per-drive C:relative semantics native
        return str(Path(path).resolve())

    def missing(error):
        return getattr(error, "winerror", None) in (2, 3)

    try:
        return str(value.resolve(strict=True))
    except OSError as error:
        if not missing(error):
            return str(value.resolve())
    low, high = 1, len(parts)             # high is missing; low must resolve
    try:
        Path(parts[0]).resolve(strict=True)
        while high - low > 1:
            middle = (low + high) // 2
            try:
                Path(*parts[:middle]).resolve(strict=True)
            except OSError as error:
                if not missing(error):
                    return str(value.resolve())
                high = middle
            else:
                low = middle
    except OSError as error:
        drive = value.drive
        if drive.startswith(("\\\\?\\", "\\\\.\\")):
            drive = drive[4:]
        if (missing(error) and len(drive) == 2 and drive[0] in
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" and drive[1] == ":"):
            # A missing local drive has no existing child prefix. Resolve the
            # short anchor natively, then append the absent no-dot/no-colon tail.
            # UNC/other namespaces retain native traversal and referral behavior.
            return str(Path(parts[0]).resolve().joinpath(*parts[1:]))
        return str(value.resolve())
    boundary = Path(*parts[:high]).resolve()
    return str(boundary.joinpath(*parts[high:]))


def canonical(path):
    return comparison_key(_resolved(path))


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
