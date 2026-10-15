"""Toolkit-owned gateway to the separately installed GPL House Carl process."""

VERSION = "1.0.0"
MCP_VERSION = "2.3.0"
TOMLKIT_VERSION = "0.14.0"
UPSTREAM_VERSION = "2.0.5"
UPSTREAM_COMMIT = "4e28505122165a1e210d0fd56d1382b377669421"
ARCHIVE_URL = "https://github.com/Avick3110/houseCARL/releases/download/v2.0.5/houseCARL-2.0.5.zip"
ARCHIVE_SHA256 = "c3d3ca7e124d92c65f7a1b935b4a598e8d69f2f1fe900224bab5d1b352f0df19"


class Refusal(ValueError):
    """A concrete refusal; callers must never convert it into an empty result."""
