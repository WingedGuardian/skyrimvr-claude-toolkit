// Shared helper: load this install's REAL active plugin list into xelib.
//
// Why this exists: xelib runs in SSE mode (GM_SSE) and therefore reads its load order from the
// *SSE* plugins.txt (...\AppData\Local\Skyrim Special Edition\plugins.txt). On a Skyrim VR install
// that file usually does NOT exist -- the real load order lives at the VR path
// (...\AppData\Local\Skyrim VR\plugins.txt). With no SSE plugins.txt, xelib's default/smartLoad path
// finds no load order and the loader fails SILENTLY (empty exception). Passing the active list
// explicitly fixes it, and never goes stale even when a mod manager rewrites plugins.txt.
//
// Usage:
//   const xelib = require('xelib');
//   const { loadActive } = require('./active-plugins');
//   xelib.init(); xelib.setLanguage('English');
//   xelib.setGamePath(process.env.GAME_ROOT || 'C:\\path\\to\\your\\Skyrim\\');
//   xelib.setGameMode(xelib.GM_SSE);
//   loadActive(xelib);            // reads your plugins.txt, loads everything
//   xelib.waitForLoader().then(() => { ... });
const fs = require('fs');
const path = require('path');

// Resolve the plugins.txt to read. Override with $PLUGINS_TXT; otherwise probe the common
// per-game folders under %LOCALAPPDATA% (first existing wins). VR is listed first because that's
// the case the SSE-mode loader gets wrong; SE/AE/LE fall through.
function defaultPluginsTxt() {
    if (process.env.PLUGINS_TXT) return process.env.PLUGINS_TXT;
    const base = process.env.LOCALAPPDATA
        || path.join(process.env.USERPROFILE || '', 'AppData', 'Local');
    const candidates = ['Skyrim VR', 'Skyrim Special Edition', 'Skyrim'];
    for (const game of candidates) {
        const p = path.join(base, game, 'plugins.txt');
        try { if (fs.existsSync(p)) return p; } catch (e) { /* ignore */ }
    }
    // Fall back to the VR path even if missing so the error message names a real location.
    return path.join(base, 'Skyrim VR', 'plugins.txt');
}

function readActivePlugins(pluginsTxt = defaultPluginsTxt()) {
    const txt = fs.readFileSync(pluginsTxt, 'utf8');
    return txt.split(/\r?\n/)
        .filter(l => l.startsWith('*'))   // '*' marks an active plugin
        .map(l => l.slice(1).trim())
        .filter(Boolean);
}

// Load the active list explicitly (bypasses a missing SSE plugins.txt).
// smartLoad=true pulls masters in correct order.
function loadActive(xelib, { smartLoad = true, buildRefs = false, pluginsTxt = defaultPluginsTxt() } = {}) {
    const active = readActivePlugins(pluginsTxt);
    xelib.loadPlugins(active.join('\n'), smartLoad, buildRefs);
    return active;
}

module.exports = { readActivePlugins, loadActive, defaultPluginsTxt };
