// Shared OpenCode V1 transport, extracted from the X4 toolkit's qualified source fixtures.
// Reads V1 1.18.34 contracts; running-host enforcement remains separately qualified.
// This module is imported by each toolkit's one-export plugin wrapper.
import { spawn, spawnSync } from "node:child_process"
import { existsSync, statSync } from "node:fs"
import { delimiter, dirname, isAbsolute, join } from "node:path"


export function createGuard(options) {
const label = options.label || "Modding toolkit"
const envPrefix = options.envPrefix || "MODDING_HARNESS"
const prefix = options.verdictPrefix || "HARNESS_OK"
const envVar = suffix => process.env[envPrefix + "_" + suffix]
const JUDGED = new Set(["bash", "edit", "write", "apply_patch"])
const WRITES = new Set(["edit", "write", "apply_patch"])
const DECISIONS = new Set(["allow", "advise", "deny"])
const BANNER =
  label + " GUARDS LIVE (opencode plugin v1) -- bash, edit, write and apply_patch use the guards; unknown tools warn and allow. " +
  "If you do not see this line, the plugin did not load."
const MAX_OUT = 1 << 20
const WIN = process.platform === "win32"

function inertError(cause) {
  const e = new Error(`${label} GUARD INERT: ${String(cause).replaceAll("X4_", envPrefix + "_")}. NOTHING was checked; this is a refusal, not a verdict.`)
  e.x4inert = true
  return e
}

function isJudgedTool(tool) {
  if (JUDGED.has(tool)) return true
  if (options.isJudgedTool === undefined) return false
  try {
    if (typeof options.isJudgedTool !== "function") throw new TypeError()
    const known = options.isJudgedTool(tool)
    if (typeof known !== "boolean") throw new TypeError()
    return known
  } catch {
    throw inertError("the profile's tool classifier failed")
  }
}

function timeoutSeconds() {
  const raw = envVar("OPENCODE_TIMEOUT_S")
  if (raw === undefined || raw.trim() === "") return 60
  const t = Number(raw)
  if (!Number.isFinite(t) || t <= 0) throw inertError(`X4_OPENCODE_TIMEOUT_S=${JSON.stringify(raw)} is not a positive number of seconds`)
  return t
}

// The grammar the guards judge a `bash` call in must be the grammar of the shell OpenCode RUNS it in
// (FX-G3, v4.0.0 delta review). This mirrors OpenCode's own choice (READ packages/core/src/shell.ts,
// acceptable(), at f2cf607): its configured `shell`, else $SHELL -- either one only if acceptable
// (fish and nu are refused) -- else the platform default: on Windows pwsh, powershell, Git Bash,
// %COMSPEC% in that order; elsewhere zsh (macOS) or bash or sh. The old version said "bash" for EVERY
// shell off Windows (so a pwsh user's `Remove-Item -Recurse` was judged as bash: allow, where the
// PowerShell grammar denies -- MEASURED with x4guard check), ignored the configured shell, and
// called sh, zsh and cmd "powershell". A shell the guards have no grammar for (cmd, ...) is passed
// through by name: the adapter refuses it, so the call is INERT (refused), never judged wrongly.
const POSIX_SHELLS = new Set(["bash", "dash", "ksh", "sh", "zsh"])
const PS_SHELLS = new Set(["pwsh", "powershell"])
const REFUSED_BY_OPENCODE = new Set(["fish", "nu"])

function shellBaseName(file) {
  const base = String(file).split(/[\\/]/).pop().toLowerCase()
  return WIN ? base.replace(/\.[^.]*$/, "") : base
}

function grammarOf(name) {
  if (POSIX_SHELLS.has(name)) return "bash"
  if (PS_SHELLS.has(name)) return "powershell"
  return name // no guard grammar: the adapter refuses it (inert)
}

function isFile(p) {
  try {
    return statSync(p).isFile()
  } catch {
    return false
  }
}

// A PATH lookup, as OpenCode's which() does: PATHEXT on Windows.
function which(cmd) {
  const exts = WIN ? ["", ...(process.env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";").filter(Boolean)] : [""]
  for (const dir of (process.env.PATH || "").split(delimiter)) {
    if (!dir) continue
    for (const ext of exts) {
      const p = join(dir, cmd + ext)
      if (isFile(p)) return p
    }
  }
  return null
}

// Does the shell file RESOLVE, as OpenCode's resolve() requires before it uses it (READ
// packages/core/src/shell.ts select()/resolve()/full(), FX-G4 / reviewer G-M2)? A rooted path must
// be a file; a bare name is looked up on PATH; on Windows `bash` is Git Bash (git's ../../bin/bash.exe)
// first. A configured shell that does not resolve makes OpenCode fall back to its platform default
// (Windows: pwsh first) -- e.g. `shell: zsh` on Windows without zsh runs PowerShell, while the plugin
// judged bash. Mirroring that fallback exactly needs OpenCode's own which(); the plugin REFUSES instead.
function resolves(file) {
  const f = String(file)
  const drive = /^[A-Za-z]:[\\/]/.test(f)
  // Windows `bash` given bare or as a POSIX path (Git Bash sets SHELL=/usr/bin/bash) is Git Bash.
  if (WIN && shellBaseName(f) === "bash" && !drive && !(f.includes("\\") || (f.includes("/") && !f.startsWith("/")))) {
    const git = which("git")
    if (git && isFile(join(dirname(git), "..", "bin", "bash.exe"))) return true
    if (f.startsWith("/")) return false
  }
  if (isAbsolute(f) || drive) return isFile(f)
  return which(f) !== null || (WIN && which(f.replace(/\.[^.\\/]*$/, "")) !== null)
}

// The grammar of the shell OpenCode RUNS a `bash` call in, or a refusal the adapter reports (inert).
function shellName(configShell) {
  // OpenCode consults ONE source: the configured shell when set (falling back to the platform default,
  // NOT to $SHELL, when it is refused), else $SHELL.
  const chosen = configShell || process.env.SHELL || ""
  const name = chosen ? shellBaseName(chosen) : ""
  let grammar
  if (name && !REFUSED_BY_OPENCODE.has(name)) {
    if (!resolves(chosen)) return `${chosen} (it does not resolve to a program here, so OpenCode would fall back to another shell)`
    grammar = grammarOf(name)
  } else {
    grammar = WIN ? "powershell" : "bash"
  }
  // X4_OPENCODE_SHELL may only CONFIRM the detected shell, never replace it (FX-G4 / reviewer G-I1,
  // MEASURED: the override made `del /s /q <reference>\libraries` under a cmd shell be judged as bash
  // -- allow). The installers read it to render the OpenCode skills for bash on Windows.
  const decl = envVar("OPENCODE_SHELL")
  if (decl && grammarOf(shellBaseName(decl)) !== grammar) {
    return `${grammar} (X4_OPENCODE_SHELL=${decl} disagrees with it)`
  }
  return grammar
}

let pythonCache = null
function findPython(deadline) {
  if (pythonCache) return pythonCache
  const cands = []
  if (envVar("PYTHON")) cands.push([envVar("PYTHON")])
  if (envVar("NO_PYTHON_FALLBACK") !== "1") {
    if (WIN) cands.push(["py", "-3"])
    cands.push(["python3"], ["python"])
  }
  for (const c of cands) {
    const remaining = deadline - performance.now()
    if (remaining <= 0) throw inertError("the guard transport budget expired while finding Python")
    const r = spawnSync(c[0], [...c.slice(1), "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"], {
      stdio: "ignore",
      timeout: Math.max(1, Math.min(15000, Math.floor(remaining))),
      windowsHide: true,
    })
    if (!r.error && r.status === 0) {
      pythonCache = c
      return c
    }
  }
  return null
}

function killTree(child, timeout = 1500) {
  try {
    if (WIN) {
      const killed = spawnSync("taskkill", ["/F", "/T", "/PID", String(child.pid)], { stdio: "ignore", windowsHide: true, timeout })
      if (killed.error || killed.status !== 0) child.kill("SIGKILL")
    }
    else process.kill(-child.pid, "SIGKILL")
  } catch {
    try { child.kill("SIGKILL") } catch {}
  }
}

function parse(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "")
  if (lines.length !== 1 || !lines[0].startsWith(prefix + " ")) {
    throw inertError(`the guard adapter answered ${lines.length} line(s) without one X4OK verdict`)
  }
  let v
  try {
    v = JSON.parse(lines[0].slice(prefix.length + 1))
  } catch {
    throw inertError("the guard adapter's verdict is not JSON")
  }
  if (!v || typeof v !== "object" || Array.isArray(v) || !DECISIONS.has(v.decision)) {
    throw inertError("the guard adapter's verdict names no known decision")
  }
  return v
}

function transportWindow(startedAt = performance.now()) {
  const limit = timeoutSeconds()
  if (limit > 300) throw inertError("X4_OPENCODE_TIMEOUT_S must be at most 300 seconds")
  const cleanup = Math.min(1500, limit * 250)
  return { limit, cleanup, deadline: startedAt + limit * 1000 - cleanup }
}

function runAdapter(adapter, event, payload, startedAt) {
  const { limit, cleanup, deadline } = transportWindow(startedAt)
  const py = findPython(deadline)
  const remaining = deadline - performance.now()
  if (remaining <= 0) throw inertError("the guard transport budget expired while finding Python")
  if (!py) throw inertError("no Python >= 3.10 found for the guards (set X4_PYTHON)")
  if (!existsSync(adapter)) throw inertError(`the guard adapter is missing: ${adapter}`)
  return new Promise((resolve, reject) => {
    let out = ""
    let done = false
    const finish = (fn, arg) => {
      if (done) return
      done = true
      clearTimeout(timer)
      fn(arg)
    }
    let child
    try {
      child = spawn(py[0], [...py.slice(1), adapter, event], {
        stdio: ["pipe", "pipe", "ignore"],
        windowsHide: true,
        detached: !WIN, // its own process group, so a timeout can kill the whole tree
      })
    } catch (e) {
      throw inertError(`the guard adapter could not start (${e && e.code ? e.code : e})`)
    }
    const timer = setTimeout(() => {
      killTree(child, cleanup)
      finish(reject, inertError(`the guard adapter timed out after ${limit} s (X4_OPENCODE_TIMEOUT_S)`))
    }, remaining)
    child.on("error", (e) => finish(reject, inertError(`the guard adapter could not run (${e && e.code ? e.code : e})`)))
    child.stdout.on("data", (b) => {
      out += b.toString("utf8")
      if (out.length > MAX_OUT) {
        killTree(child, cleanup)
        finish(reject, inertError("the guard adapter's output is over 1 MB"))
      }
    })
    child.on("close", (code) => {
      if (code !== 0) return finish(reject, inertError(`the guard adapter exited ${code}`))
      try {
        finish(resolve, parse(out))
      } catch (e) {
        finish(reject, e)
      }
    })
    child.stdin.on("error", () => {}) // a child that exits early: its exit code decides
    child.stdin.end(JSON.stringify(payload))
  })
}

return async ({ directory, client }) => {
  const adapter = options.adapter
  const advisories = new Map()
  const failedUnknown = new Map()
  const failedCatalog = new Map()
  const routedCalls = new Map()
  // PER PLUGIN INSTANCE (FX-G4 / reviewer G-M3): a module-global let one project's `config` decide
  // the grammar for every other OpenCode instance that loaded this module.
  let configShell = null
  let mcpServers = []
  // Used only to refuse a previously identifiable known route when a fresh
  // query fails. Old observations never authorize or identify a new call.
  const seenMcpServers = new Map()
  const callKey = input => JSON.stringify([input.sessionID ?? "", input.callID ?? ""])
  const serverRow = server => ({ server, prefix: server.replace(/[^a-zA-Z0-9_-]/g, "_") + "_" })
  const MCP_STATES = new Set(["connected", "disabled", "failed", "needs_auth", "needs_client_registration"])
  const remember = (key, note) => {
    if (note) advisories.set(key, advisories.has(key) ? advisories.get(key) + "\n\n" + note : note)
  }

  function routeFrom(input, servers) {
    const tool = input.tool
    if (typeof tool !== "string" || JUDGED.has(tool)) return input
    // V1 1.18.34 McpCatalog.toolName emits sanitize(server) + "_" + sanitize(tool).
    // Use authoritative configured or live server names, never a guessed split.
    // An authoritative native prefix takes precedence even when it begins `mcp__`:
    // that is also a valid V1 server name. Legacy canonical names are preserved
    // only when no authoritative native prefix matches. The suffix below is the
    // host-sanitized tool spelling; provenance does not claim to undo sanitizing.
    const candidates = servers.filter(row => tool.startsWith(row.prefix) && tool.length > row.prefix.length)
      .map(row => ({ ...input, tool: `mcp__${row.server}__${tool.slice(row.prefix.length)}`,
        nativeTool: tool, mcpIdentity: { server: row.server, tool: tool.slice(row.prefix.length),
          source: row.source || "OpenCode V1 configured server prefix" } }))
    if (candidates.length > 1) {
      // Prefix overlap and sanitized-name collisions cannot establish which server
      // owns the call. Do not pick a longest prefix to bypass a known game policy.
      if (candidates.some(row => isJudgedTool(row.tool))) throw inertError("ambiguous MCP tool identity")
      return input
    }
    return candidates.length === 1 ? candidates[0] : input
  }

  async function routeInput(input, startedAt) {
    if (typeof input.tool !== "string" || JUDGED.has(input.tool)) return input
    let live = []
    let unavailable = false
    let controller
    let timer
    try {
      const mcp = client?.mcp
      // Hosts without this API retain the existing configured-prefix contract.
      // That fallback does not claim dynamic-server discovery.
      if (typeof mcp?.status !== "function") return routeFrom(input, mcpServers)
      const remaining = transportWindow(startedAt).deadline - performance.now()
      if (remaining <= 0) throw new Error("catalog budget expired")
      controller = new AbortController()
      // The same-instance V1 SDK accepts RequestInit.signal and fields style.
      // Race as well as abort: an implementation ignoring abort must not hang
      // a guard, nor may its late response update this call's identity.
      const result = await Promise.race([
        Promise.resolve().then(() => mcp.status({ query: { directory },
          signal: controller.signal, throwOnError: true, responseStyle: "fields" })),
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("catalog lookup timed out")),
            Math.max(1, Math.min(1000, Math.floor(remaining))))
        }),
      ])
      const data = result?.data
      if (!result || result.error || !data || typeof data !== "object" || Array.isArray(data)) throw new Error("invalid catalog")
      // Validate the complete reply before trusting any partial server list.
      for (const [server, state] of Object.entries(data)) {
        if (!server || !state || typeof state !== "object" || Array.isArray(state) || !MCP_STATES.has(state.status)) throw new Error("invalid catalog member")
        if (state.status === "connected") live.push({ ...serverRow(server), source: "OpenCode V1 live connected server prefix" })
      }
    } catch {
      unavailable = true
    } finally {
      clearTimeout(timer)
      controller?.abort()
    }
    const servers = new Map(mcpServers.map(row => [row.server, row]))
    if (unavailable) {
      for (const row of seenMcpServers.values()) servers.set(row.server, row)
      const known = routeFrom(input, [...servers.values()])
      if (isJudgedTool(known.tool)) throw inertError("the live MCP server catalog was unavailable; native identity was not verified")
      return { ...input, nativeTool: input.tool, mcpCatalogUnavailable: true }
    }
    for (const row of live) {
      servers.set(row.server, row)
      seenMcpServers.set(row.server, row)
    }
    return routeFrom(input, [...servers.values()])
  }

  const ask = (event, input, args, startedAt) =>
    runAdapter(adapter, event, { v: 1, tool: input.tool, args: args ?? {}, directory,
      session_id: input.sessionID ?? "", call_id: input.callID ?? "",
      ...(input.mcpIdentity ? { native_tool: input.nativeTool, mcp_identity: input.mcpIdentity } : {}),
      shell: input.tool === "bash" ? shellName(configShell) : "" }, startedAt)

  return {
    // OpenCode hands every plugin its config at load (READ plugin/index.ts: hook.config?.(cfg)); its
    // `shell` decides which shell runs a `bash` call, so it decides the grammar it is judged in.
    config: async (cfg) => {
      configShell = cfg && typeof cfg.shell === "string" && cfg.shell.trim() ? cfg.shell.trim() : null
      const servers = cfg && cfg.mcp && typeof cfg.mcp === "object" && !Array.isArray(cfg.mcp) ? cfg.mcp : {}
      const configured = Object.entries(servers)
        .filter(([server, value]) => server && value && typeof value === "object" && !Array.isArray(value) &&
          (value.type === "local" || value.type === "remote"))
      mcpServers = configured.filter(([, value]) => value.enabled !== false).map(([server]) => serverRow(server))
      seenMcpServers.clear()
      // An initially disabled known server is still identifiable if its later
      // connect succeeds but status becomes unavailable. This is refusal-only.
      for (const [server] of configured) seenMcpServers.set(server, serverRow(server))
    },

    "tool.execute.before": async (input, output) => {
      const key = callKey(input)
      const startedAt = performance.now()
      const routed = await routeInput(input, startedAt)
      if (routed.mcpCatalogUnavailable) {
        const failureKey = JSON.stringify([input.sessionID ?? "", input.tool])
        const count = (failedCatalog.get(failureKey) || 0) + 1
        failedCatalog.set(failureKey, count)
        if (Number.isInteger(Math.log2(count))) remember(key,
          `${label} WARN AND ALLOW: MCP CATALOG UNAVAILABLE for unknown tool ${input.tool}; native MCP policy was not established (${count} occurrence(s) in this plugin instance).`)
      }
      let v
      try {
        v = await ask("pre", routed, output.args, startedAt)
      } catch (e) {
        if (!isJudgedTool(routed.tool)) {
          // Unknown surfaces retain warn-and-allow even when the receipt service
          // is unavailable. Do not claim a durable receipt in this fallback.
          const unknownKey = JSON.stringify([input.sessionID ?? "", input.tool])
          const count = (failedUnknown.get(unknownKey) || 0) + 1
          failedUnknown.set(unknownKey, count)
          if (routed.mcpIdentity || routed.mcpCatalogUnavailable) routedCalls.set(key, routed)
          if (Number.isInteger(Math.log2(count))) remember(key,
            `${label} WARN AND ALLOW: unknown tool ${input.tool}; coverage receipt unavailable (${count} occurrence(s) in this plugin instance).`)
          return
        }
        throw e && e.x4inert ? e : inertError(`the plugin failed (${e && e.message ? e.message : e})`)
      }
      if (v.decision === "deny") throw new Error(v.reason || label + " GUARD: refused, with no reason given")
      if (routed.mcpIdentity || routed.mcpCatalogUnavailable) routedCalls.set(key, routed)
      if (v.decision === "advise" && v.context) remember(key, v.context)
    },

    "tool.execute.after": async (input, output) => {
      const notes = []
      const key = callKey(input)
      const saved = routedCalls.get(key)
      routedCalls.delete(key)
      const mismatch = saved && saved.nativeTool !== input.tool
      const routed = saved && !mismatch ? saved : input
      const pre = mismatch ? null : advisories.get(key)
      advisories.delete(key)
      if (mismatch) notes.push(`${label} COVERAGE MISMATCH: post tool identity differs from pre; no qualified correlation.`)
      if (pre) notes.push(pre)
      {
        try {
          const v = await ask("post", routed, input.args)
          if (v.decision === "advise" && v.context) notes.push(v.context)
        } catch (e) {
          if (WRITES.has(input.tool)) notes.push(`${label} VALIDATION DID NOT RUN: ${e && e.message ? e.message : e}`)
        }
      }
      if (notes.length && output) {
        const notice = `[${label} guards] ` + notes.join("\n\n")
        if (typeof output.output === "string") output.output += "\n\n" + notice
        // V1 invokes MCP after hooks with the raw CallToolResult, before it
        // converts content into the ordinary output string. Preserve its text,
        // attachments and structured result, adding a separate valid text block.
        else if (Array.isArray(output.content)) output.content.push({ type: "text", text: notice })
      }
    },

    "experimental.chat.system.transform": async (_input, output) => {
      if (output && Array.isArray(output.system)) {
        output.system.push(BANNER)
        if (existsSync(join(directory, "KNOWLEDGEBASE.local.md"))) {
          try {
            const verdict = await ask("session", { tool: "preferences" }, {})
            if (verdict.context) output.system.push(verdict.context)
          } catch {
            output.system.push("PREFERENCES: NOT INJECTED -- read KNOWLEDGEBASE.local.md yourself.")
          }
        }
      }
    },
  }
}

}
