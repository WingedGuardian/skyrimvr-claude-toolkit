"""A private MCP client; upstream never receives agent calls directly."""
from __future__ import annotations

from contextlib import AsyncExitStack
import json
import os
import re
from pathlib import Path

from . import Refusal, UPSTREAM_COMMIT, UPSTREAM_VERSION
from .files import sha
from .order import write_profile

READS = {"housecarl_records", "housecarl_check", "housecarl_asset_status", "housecarl_nif_inspect",
         "housecarl_bsa_list", "housecarl_skypatcher_layer", "housecarl_skse", "housecarl_load_order_status"}
WRITES = {"housecarl_apply", "housecarl_create", "housecarl_remove", "housecarl_forward",
          "housecarl_copy", "housecarl_create_plugin"}


def verify_installation(config):
    server = Path(config["server"])
    expected = config["server_hashes"]
    required = {server.name, "housecarl-mcp.dll", "housecarl-core.dll", "corpus.json"}
    if not required <= set(expected):
        raise Refusal("Incomplete installed-backend hash manifest; rerun setup.")
    for relative, digest in expected.items():
        if Path(relative).name != relative or sha(server.parent / relative) != digest:
            raise Refusal("House Carl installation changed: " + relative + "; rerun setup with the pinned archive.")
    return len(expected)


def response_json(result):
    texts = [x.text for x in result.content if getattr(x, "type", None) == "text"]
    try:
        return json.loads("\n".join(texts))
    except ValueError as exc:
        raise Refusal("Backend did not return a complete JSON write result; nothing may be published.") from exc


def check_index(result, expected_count):
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    match = re.search(r"resolver: (\d+) plugins resolved to real files", text)
    if not match or int(match[1]) != expected_count or "EXCLUDED plugins" in text or "refresh is still pending" in text:
        raise Refusal("Backend did not index the complete captured order; no partial answer accepted. " + text[:1800])


class Backend:
    def __init__(self, config, snapshot, work=None):
        self.config, self.snapshot = config, snapshot
        self.work = Path(work) if work else Path(config["state_dir"]) / "views" / snapshot.fingerprint
        self.stack = AsyncExitStack()
        self.session = None
        self.index_checked = False

    async def __aenter__(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        server = Path(self.config["server"])
        verify_installation(self.config)
        profile = write_profile(self.snapshot, self.work / "profile", self.config)
        mods = self.work / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        # Do not inherit any caller override that changes backend roots, corpus or transport.
        env = {k: v for k, v in env.items() if not k.lower().startswith(("housecarl", "aspnetcore_", "urls"))}
        env.update({"HOUSECARL_DATA_DIR": str(self.work / "backend-config"),
                    "HouseCarl__DataDir": self.config["data_dir"], "HouseCarl__ModsDir": str(mods),
                    "HouseCarl__ProfileDir": str(profile), "HouseCarl__MaxPlugins": "0"})
        user = self.work / "backend-config/houseCARL.user.json"
        if user.exists() and json.loads(user.read_text(encoding="utf-8")).get("Mo2InstanceDir"):
            raise Refusal("Private backend config unexpectedly selects an MO2 instance.")
        self.work.mkdir(parents=True, exist_ok=True)
        log = self.stack.enter_context((self.work / "backend.log").open("a", encoding="utf-8"))
        try:
            streams = await self.stack.enter_async_context(stdio_client(
                StdioServerParameters(command=str(server), args=[], env=env, cwd=str(server.parent)), errlog=log))
            self.session = await self.stack.enter_async_context(ClientSession(*streams))
            info = await self.session.initialize()
            if info.server_info.version != UPSTREAM_VERSION:
                raise Refusal("Unexpected backend version: " + info.server_info.version)
            self.tools = (await self.session.list_tools()).tools
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *exc):
        # Do not inject an ordinary tool refusal into the SDK's nested task groups: that wraps it
        # in ExceptionGroups and loses the CLI/MCP error contract. Close our resources, then propagate.
        await self.stack.aclose()
        return False

    async def call(self, name, arguments):
        import anyio
        if name != "housecarl_load_order_status" and not self.index_checked:
            status = await self.call("housecarl_load_order_status", {"max_chars": 12000})
            check_index(status, len(self.snapshot.names))
            self.index_checked = True
        # Kill only this owned backend on cancellation/timeout through its context manager.
        with anyio.fail_after(180):
            result = await self.session.call_tool(name, arguments)
        if result.is_error:
            raise Refusal("Backend refused: " + " ".join(getattr(c, "text", "") for c in result.content))
        for block in result.content:
            text = getattr(block, "text", "")
            if any(line.lstrip().lower().startswith("error:") for line in text.splitlines()):
                raise Refusal(text[:1800])
            if text.lstrip().startswith("{"):
                try:
                    obj = json.loads(text)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("ok") is False:
                    raise Refusal(json.dumps(obj, ensure_ascii=True)[:1800])
        return result


def context(config, snapshot):
    return {"authority": config["authority"], "asset_provider": config["asset_provider"],
            "plugins_txt": config["plugins_txt"], "edition": config["edition"],
            "plugin_count": len(snapshot.names), "input_fingerprint": snapshot.fingerprint,
            "backend": UPSTREAM_VERSION + "+" + UPSTREAM_COMMIT,
            "capabilities": {"record_reads": True, "record_writes": True, "config_reference_checks": True,
                             "runtime_addresses": False, "native_pairing": False,
                             "facegen_provenance": False, "compaction": False, "merge": False},
            "limits": ["File and modeled configuration state; not observed runtime state.",
                       "Loose Data ownership is unknown; native pairing and facegen provenance are unavailable."]}
