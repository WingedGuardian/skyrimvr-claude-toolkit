// Generated from agent/targets/opencode/plugins/skyguard.js. OpenCode V1 only.
// One export: OpenCode invokes every exported plugin factory.
import { fileURLToPath } from "node:url"
import { createGuard } from "../../tools/modding_harness/opencode_plugin.mjs"

export const SkyrimGuard = createGuard({
  label: "Skyrim",
  envPrefix: "SKYRIM",
  verdictPrefix: "SKYGUARD-OPENCODE",
  // Mirror mcp_rules.is_devbench, including server names containing double underscores.
  // Other MCP tools keep the shared transport's unknown-tool receipt and warning behavior.
  isJudgedTool: tool => {
    const parts = typeof tool === "string" ? tool.split("__") : []
    return parts.length >= 3 && parts[0] === "mcp" &&
      parts.slice(1).join("__").toLowerCase().replace(/[-_.\s]/g, "").includes("devbench")
  },
  adapter: fileURLToPath(new URL("../hooks/skyguard_adapter.py", import.meta.url)),
})
