#!/usr/bin/env python3
"""Optional House Carl gateway. Run --help; nothing installs implicitly."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys

from housecarl_integration import MCP_VERSION, TOMLKIT_VERSION, Refusal


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=True))


async def serve(config):
    import mcp.server.stdio
    from mcp.server import Server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    from housecarl_integration.gateway import Gateway
    from housecarl_integration.order import capture
    from housecarl_integration.runtime import Backend, READS, WRITES
    gateway = Gateway(config)
    snap = capture(config)
    async with Backend(config, snap) as backend:
        tools = [t for t in backend.tools if t.name in READS | WRITES]
    for tool in tools:
        if tool.name == "housecarl_load_order_status":
            tool.input_schema = {"type": "object", "properties": {"filter": {"type": "string",
                "description": "Optional plugin basename, matched against captured membership."}}, "additionalProperties": False}
            tool.description = "Configured authority's active/inactive plugin membership and integration context; no alternate profiles."
            continue
        tool.description = ("Through the toolkit gateway; writes are staged, independently checked and backed up. "
                            "Order comes from the configured authority, independent of deployment. "
                            "Native pairing, facegen provenance, runtime FormIDs and existing-patch closure copy are unavailable. "
                            "Use in_place/into with a plugin basename for an existing file; patch creates an inactive staged file. "
                            + (tool.description or ""))
    tools.append(Tool(name="toolkit_housecarl_status", description="Integration inputs, capabilities and exact active order.",
                      input_schema={"type": "object", "properties": {}, "additionalProperties": False}))

    async def list_tools(_ctx, _params):
        return ListToolsResult(tools=tools)

    async def call_tool(_ctx, params):
        try:
            result = await gateway.call(params.name, params.arguments or {})
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=True))])
        except (Refusal, OSError, ValueError) as exc:
            return CallToolResult(content=[TextContent(type="text", text=str(exc))], isError=True)

    app = Server("toolkit-housecarl", version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool,
                 instructions="Use House Carl for supported Skyrim data-layer tasks. Consult toolkit-housecarl skill. "
                              "Context states limits; no tool observes gameplay or changes load order.")
    async with mcp.server.stdio.stdio_server() as streams:
        await app.run(*streams, app.create_initialization_options())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--archive")
    s.add_argument("--edition", choices=["SE", "AE", "VR"], required=True)
    s.add_argument("--plugins-txt")
    s.add_argument("--config-dir")
    s.add_argument("--data-dir")
    s.add_argument("--xelib-root")
    s.add_argument("--mo2-instance", help="Use the selected native MO2 profile instead of deployed Data")
    s.add_argument("--agents", choices=["none", "claude", "codex", "all"], default="all")
    r = sub.add_parser("register")
    r.add_argument("--agents", choices=["claude", "codex", "all"], default="all")
    r = sub.add_parser("unregister", help="Remove only this gateway's project registrations; retain all evidence")
    r.add_argument("--agents", choices=["claude", "codex", "all"], default="all")
    sub.add_parser("doctor")
    sub.add_parser("serve")
    c = sub.add_parser("call")
    c.add_argument("tool")
    c.add_argument("--arguments", default="{}", help="JSON object; prefer --arguments-file for shell-safe input")
    c.add_argument("--arguments-file")
    r = sub.add_parser("restore")
    r.add_argument("transaction_id")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            from housecarl_integration.setup import setup
            emit(setup(args))
            return 0
        if args.command in {"call", "serve", "register", "unregister"}:
            py = Path(args.root).resolve() / ".skyguard/housecarl/venv/Scripts/python.exe"
            if not py.exists():
                raise Refusal("Optional gateway environment missing; run setup. No packages installed implicitly.")
            if py.resolve() != Path(sys.executable).resolve():
                return subprocess.call([str(py), str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])])
            try:
                from importlib.metadata import version
                if version("mcp") != MCP_VERSION or version("tomlkit") != TOMLKIT_VERSION:
                    raise Refusal("Optional gateway dependency versions changed; rerun pinned setup.")
            except ImportError as exc:
                raise Refusal("Optional gateway dependencies missing; rerun setup.") from exc
        from housecarl_integration.gateway import Gateway, load_config
        config = load_config(args.root)
        if args.command == "register":
            from housecarl_integration.setup import register
            emit({"registered": register(config, ["claude", "codex"] if args.agents == "all" else [args.agents])})
        elif args.command == "unregister":
            from housecarl_integration.setup import unregister
            emit({"unregistered": unregister(config, ["claude", "codex"] if args.agents == "all" else [args.agents])})
        elif args.command == "doctor":
            from housecarl_integration.order import capture
            from housecarl_integration.runtime import context, verify_installation
            count = verify_installation(config)
            emit({**context(config, capture(config)), "verified_backend_files": count,
                  "host_discovery": "Not established by doctor; restart hosts and exercise a tool."})
        elif args.command == "restore":
            emit(Gateway(config).restore(args.transaction_id))
        elif args.command == "serve":
            asyncio.run(serve(config))
        else:
            arguments = json.loads(Path(args.arguments_file).read_text(encoding="utf-8")) if args.arguments_file else json.loads(args.arguments)
            emit(asyncio.run(Gateway(config).call(args.tool, arguments)))
        return 0
    except (Refusal, OSError, ValueError, subprocess.SubprocessError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
