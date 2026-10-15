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

function runAdapter(adapter, event, payload) {
  const limit = timeoutSeconds()
  if (limit > 300) throw inertError("X4_OPENCODE_TIMEOUT_S must be at most 300 seconds")
  const cleanup = Math.min(1500, limit * 250)
  const deadline = performance.now() + limit * 1000 - cleanup
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

return async ({ directory }) => {
  const adapter = options.adapter
  const advisories = new Map()
  const failedUnknown = new Map()
  // PER PLUGIN INSTANCE (FX-G4 / reviewer G-M3): a module-global let one project's `config` decide
  // the grammar for every other OpenCode instance that loaded this module.
  let configShell = null
  const ask = (event, input, args) =>
    runAdapter(adapter, event, { v: 1, tool: input.tool, args: args ?? {}, directory,
      session_id: input.sessionID ?? "", call_id: input.callID ?? "",
      shell: input.tool === "bash" ? shellName(configShell) : "" })

  return {
    // OpenCode hands every plugin its config at load (READ plugin/index.ts: hook.config?.(cfg)); its
    // `shell` decides which shell runs a `bash` call, so it decides the grammar it is judged in.
    config: async (cfg) => {
      configShell = cfg && typeof cfg.shell === "string" && cfg.shell.trim() ? cfg.shell.trim() : null
    },

    "tool.execute.before": async (input, output) => {
      let v
      try {
        v = await ask("pre", input, output.args)
      } catch (e) {
        if (!JUDGED.has(input.tool)) {
          // Unknown surfaces retain warn-and-allow even when the receipt service
          // is unavailable. Do not claim a durable receipt in this fallback.
          const key = JSON.stringify([input.sessionID ?? "", input.tool])
          const count = (failedUnknown.get(key) || 0) + 1
          failedUnknown.set(key, count)
          if (Number.isInteger(Math.log2(count))) advisories.set(input.callID,
            `${label} WARN AND ALLOW: unknown tool ${input.tool}; coverage receipt unavailable (${count} occurrence(s) in this plugin instance).`)
          return
        }
        throw e && e.x4inert ? e : inertError(`the plugin failed (${e && e.message ? e.message : e})`)
      }
      if (v.decision === "deny") throw new Error(v.reason || label + " GUARD: refused, with no reason given")
      if (v.decision === "advise" && v.context) advisories.set(input.callID, v.context)
    },

    "tool.execute.after": async (input, output) => {
      const notes = []
      const pre = advisories.get(input.callID)
      advisories.delete(input.callID)
      if (pre) notes.push(pre)
      {
        try {
          const v = await ask("post", input, input.args)
          if (v.decision === "advise" && v.context) notes.push(v.context)
        } catch (e) {
          if (WRITES.has(input.tool)) notes.push(`${label} VALIDATION DID NOT RUN: ${e && e.message ? e.message : e}`)
        }
      }
      if (notes.length && output && typeof output.output === "string") {
        output.output += `\n\n[${label} guards] ` + notes.join("\n\n")
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
