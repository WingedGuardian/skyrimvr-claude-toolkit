// Generated from agent/targets/opencode/plugins/skyguard.js. OpenCode V1 only.
// One export: OpenCode invokes every exported plugin factory.
import { fileURLToPath } from "node:url"
import { createGuard } from "../../tools/modding_harness/opencode_plugin.mjs"

export const SkyrimGuard = createGuard({
  label: "Skyrim",
  envPrefix: "SKYRIM",
  verdictPrefix: "SKYGUARD-OPENCODE",
  adapter: fileURLToPath(new URL("../hooks/skyguard_adapter.py", import.meta.url)),
})
