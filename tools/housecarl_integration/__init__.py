"""Toolkit-owned gateway to the separately installed GPL House Carl process."""

VERSION = "1.0.0"
MCP_VERSION = "2.3.0"
TOMLKIT_VERSION = "0.14.0"
UPSTREAM_VERSION = "2.1.2"
UPSTREAM_COMMIT = "1bdd0a8d1f34a7cef99a856ac61d0f4fb33cb732"
ARCHIVE_URL = "https://github.com/Avick3110/houseCARL/releases/download/v2.1.2/houseCARL-2.1.2.zip"
ARCHIVE_SHA256 = "4e69494cc4cadd1e040a0762318d6e0ad0ac7082f68c413ffa96e8fbd879be96"
BACKEND_RUNTIME = "Microsoft.NETCore.App 10."


class Refusal(ValueError):
    """A concrete refusal; callers must never convert it into an empty result."""
