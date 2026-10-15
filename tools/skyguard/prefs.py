"""Compatibility import for the pinned shared preference parser."""
import sys
from modding_harness import preferences as _shared

# Preserve module-level constants and monkeypatchable legacy helpers as one
# module, so callers never edit a disconnected copy of the parser settings.
sys.modules[__name__] = _shared
