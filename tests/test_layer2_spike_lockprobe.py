"""Plan 6 Task 1: the spike's ACL mask whitelist, with its in-file mutation twin
`l2-mask-whitelist` (dropping the whitelist must turn the refusal rows red)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SPIKE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "layer2-spike"


def _load(path, name):
    sys.path.insert(0, str(SPIKE_DIR))
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SPIKE_DIR))


FORBIDDEN = [["W"], ["M"], ["F"], ["S"], ["WDAC"], ["WO"], ["D"], ["DE"], ["WD", "WDAC"], ["WD", "S"], []]


def _refuses_all(mod):
    for mask in FORBIDDEN:
        with pytest.raises(mod.MaskNotAllowed):
            mod.check_mask(mask)


def test_whitelisted_masks_pass():
    lp = _load(SPIKE_DIR / "lockprobe.py", "l2_lockprobe")
    assert lp.check_mask(["WD", "AD"]) == "WD,AD"
    assert lp.check_mask(["WD", "AD", "WA", "WEA"]) == "WD,AD,WA,WEA"


def test_masks_outside_the_whitelist_raise():
    _refuses_all(_load(SPIKE_DIR / "lockprobe.py", "l2_lockprobe"))


def test_every_mechanism_uses_only_whitelisted_masks():
    lp = _load(SPIKE_DIR / "lockprobe.py", "l2_lockprobe")
    for ro, mask in lp.MECHANISMS.values():
        if mask:
            lp.check_mask(mask)


def test_mutation_l2_mask_whitelist(tmp_path):
    src = (SPIKE_DIR / "lockprobe.py").read_text(encoding="utf-8")
    needle = "bad = [t for t in tokens if t not in ALLOWED]"
    assert needle in src, "mutation anchor moved"
    mutant = tmp_path / "lockprobe_mutant.py"
    mutant.write_text(src.replace(needle, "bad = []"), encoding="utf-8")
    mod = _load(mutant, "l2_lockprobe_mutant")
    with pytest.raises(BaseException) as info:
        _refuses_all(mod)
    # The refusal row went red under the mutant (pytest.raises did not fire).
    assert not isinstance(info.value, mod.MaskNotAllowed)
