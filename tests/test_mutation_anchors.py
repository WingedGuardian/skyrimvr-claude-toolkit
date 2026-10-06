"""Every mutation's anchor occurs EXACTLY ONCE in its target file -- checked in the ordinary suite.

test_mutations.py already refuses a moved anchor, but only when that mutation RUNS, and the full mutation file is
slow enough that it is run in parts. On 2026-10-05 sixteen anchors were stale at once (code rewritten by the Plan 5
final-review fixes and Plan 6 tasks): each of those gates was testing nothing until someone happened to run it. This
reads every anchor the way the harness does (io.open, newline="": no CRLF folding) and costs well under a second.
"""
import io

import pytest

from conftest import REPO
from test_mutations import MUTATIONS


def _count(relpath, find):
    with io.open(REPO / relpath, encoding="utf-8", newline="") as fh:
        return fh.read().count(find)


@pytest.mark.parametrize("relpath,find", [(p.values[0], p.values[1]) for p in MUTATIONS],
                         ids=[p.id for p in MUTATIONS])
def test_every_mutation_anchor_occurs_exactly_once(relpath, find):
    n = _count(relpath, find)
    assert n == 1, (f"anchor found {n}x in {relpath} -- the code moved, so this mutation tests nothing (0) or "
                    f"would mutate more than the one clause it names (2+). Re-anchor it.")


def test_the_check_can_fail(tmp_path):
    """A moved anchor is reported, not skipped: the counter sees 0 for a string that is not in the file."""
    assert _count("tests/test_mutation_anchors.py", "this text" + " is not in any file") == 0
