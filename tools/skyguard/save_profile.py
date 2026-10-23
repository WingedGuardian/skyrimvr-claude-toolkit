"""Skyrim-owned save membership, including the legacy guard's backup suffixes."""
from __future__ import annotations

from pathlib import Path
import re

from modding_harness.profiles import GameProfile

_PAIR = re.compile(r"^(.*)\.(ess|skse)(\.[\w.-]+)?$", re.I)


def member_kind(path):
    match = _PAIR.fullmatch(Path(path).name)
    return match[2].casefold() if match else None


def literal_members(path):
    """Pair names in immutable legacy evidence, without listing live originals."""
    path = Path(path)
    match = _PAIR.fullmatch(path.name)
    if match is None:
        raise ValueError("not a recognized Skyrim save member")
    stem, _kind, suffix = match.groups()
    return tuple(path.with_name(stem + extension + (suffix or "")) for extension in (".ess", ".skse"))


def legacy_suffix(path):
    match = _PAIR.fullmatch(Path(path).name)
    return match[3].casefold() if match and match[3] else None


class SkyrimProfile(GameProfile):
    def members(self, path):
        suffix = legacy_suffix(path)
        if suffix is None:
            return super().members(path)
        # Reuse the core's case-ambiguity checks; a suffix stays attached to
        # both members instead of being mistaken for the save's extension.
        return GameProfile(self.name, self.save_roots, (".ess" + suffix, ".skse" + suffix), True).members(path)


def skyrim(roots=()):
    return SkyrimProfile("skyrim", tuple(map(Path, roots)), (".ess", ".skse"), True)
