'use strict';
/*
 * resaver-resolve-names.js — lightweight, on-demand FormID -> EditorID resolver for ResaverCLI output.
 *
 * ResaverCLI emits records as "Plugin.esp:FORMID" (plugin-local hex). This script resolves those to
 * EditorIDs by loading ONLY the requested plugins (+ their masters) via xeditlib — not the whole order.
 *
 * Usage:  node tools/resaver-resolve-names.js <Plugin.esp:formidHex> [<Plugin.esp:formidHex> ...]
 * Output: JSON map { "Plugin.esp:FORMID": { found, editorId, signature } }
 *
 * Notes:
 *  - Fast path uses the plugin's load-order index + getRecord; falls back to a (ESL-safe) record scan.
 *  - Resolving a FormID from a very large master (e.g. Skyrim.esm) via the scan fallback is slow; the
 *    indexed fast path handles the common case quickly.
 */
const path = require('path');
const xelib = require('xeditlib');
process.chdir(path.dirname(require.resolve('xeditlib')));   // cwd = the xeditlib package dir (DLL + *.Hardcoded.dat)
const GAME_ROOT_RAW = process.env.GAME_ROOT || path.resolve(__dirname, '..');  // game root = parent of tools/
// XEditLib wants a TRAILING SEPARATOR on the game path. Measured 2026-08-27:
// without one, a setGamePath() called BEFORE setGameMode() makes setGameMode
// throw "SetGameMode failed". This file happens to call them the other way
// round and so survives an unterminated path -- but that is call-order luck,
// not correctness, and the next edit that reorders them would break it.
const GAME_PATH = (GAME_ROOT_RAW.endsWith(path.win32.sep) || GAME_ROOT_RAW.endsWith(path.posix.sep))
    ? GAME_ROOT_RAW : GAME_ROOT_RAW + path.sep;
const GM_SSE = 4;

const safe = (h, p) => { try { return xelib.getValue(h, p); } catch (e) { return ''; } };
function waitForLoader() {
    return new Promise((res, rej) => {
        const t0 = Date.now();
        const poll = () => {
            const s = xelib.getLoaderStatus();
            if (s === 2) return res();
            if (s === 3) return rej(new Error('loader failed'));
            if (Date.now() - t0 > 300000) return rej(new Error('loader timeout'));
            setTimeout(poll, 300);
        };
        poll();
    });
}

const args = process.argv.slice(2);
if (!args.length) { console.error('usage: node resaver-resolve-names.js <Plugin.esp:formidHex> [...]'); process.exit(1); }

const targets = args.map(a => {
    const i = a.lastIndexOf(':');
    return { raw: a, plugin: a.slice(0, i), fid: parseInt(a.slice(i + 1), 16) & 0xFFFFFF };
}).filter(t => t.plugin && !Number.isNaN(t.fid));

const byPlugin = {};
for (const t of targets) (byPlugin[t.plugin] = byPlugin[t.plugin] || []).push(t);
const plugins = Object.keys(byPlugin);

async function main() {
    xelib.init();
    const out = {};
    try {
        xelib.setGameMode(GM_SSE); xelib.setGamePath(GAME_PATH); xelib.setLanguage('English');
        xelib.loadPlugins(plugins.join('\n'), true, false);
        await waitForLoader();

        for (const plugin of plugins) {
            let file = 0;
            try { file = xelib.fileByName(plugin); } catch (e) { file = 0; }
            const want = new Map(byPlugin[plugin].map(t => [t.fid, t.raw]));
            if (!file) { for (const t of byPlugin[plugin]) out[t.raw] = { found: false, error: 'plugin not loaded' }; continue; }

            // Fast path: load-order index + getRecord(full formid)
            let idx = -1; try { idx = xelib.getFileLoadOrder(file); } catch (e) {}
            if (idx >= 0) {
                for (const [fid, raw] of [...want]) {
                    try {
                        const r = xelib.getRecord(file, ((idx << 24) >>> 0) | fid, false);
                        if (r) { out[raw] = { found: true, editorId: safe(r, 'EDID'), signature: xelib.signature(r) }; want.delete(fid); }
                    } catch (e) { /* fall through to scan */ }
                }
            }
            // Fallback scan (ESL-safe: matches plugin-local FormID directly)
            if (want.size) {
                const recs = xelib.getRecords(file, '', false);
                for (const r of recs) {
                    if (!want.size) break;
                    let lf; try { lf = xelib.getFormID(r, true) & 0xFFFFFF; } catch (e) { continue; }
                    if (want.has(lf)) { out[want.get(lf)] = { found: true, editorId: safe(r, 'EDID'), signature: xelib.signature(r) }; want.delete(lf); }
                }
            }
            for (const [, raw] of want) out[raw] = { found: false };
        }
    } finally { xelib.close(); }
    await drain(JSON.stringify(out, null, 2) + '\n');
}
function drain(s) { return new Promise(res => process.stdout.write(s, res)); }
process.stdout.on('error', () => {});   // swallow EPIPE if a reader closes early
//
// EXIT-CODE CAVEAT (Windows + Git Bash): this process reliably exits non-zero (127, sometimes
// with a stray "Failed to sync '<stdout>': Incorrect function") even on full success. The cause
// is Node's C++ stdio-flush during shutdown while koffi's native XEditLib.dll is still resident;
// it is NOT reachable from JS (proven: removing xelib.close() and forcing process.exit(0) does
// not change it, while a minimal koffi-free script exits 0). The JSON is ALWAYS fully written
// before that point. ==> Callers MUST judge success by parsing the JSON output, never by $?.
// This affects every xelib/koffi Node helper in this repo, not just this script.
main().catch(e => { console.error(e.stack); process.exit(1); });
