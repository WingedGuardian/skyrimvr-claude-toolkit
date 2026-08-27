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
//   const xelib = require('xeditlib');
//   const { loadActive } = require('./active-plugins');
//   xelib.init(); xelib.setLanguage('English');
//   xelib.setGamePath(process.env.GAME_ROOT || 'C:\\path\\to\\your\\Skyrim\\');
//   xelib.setGameMode(xelib.GM_SSE);
//   loadActive(xelib);            // reads your plugins.txt, loads everything
//   xelib.waitForLoader().then(() => { ... });
const fs = require('fs');
const path = require('path');

// Candidate per-game folders under %LOCALAPPDATA%, in priority order. VR is first because
// that is the case the SSE-mode loader gets wrong; SE/AE/LE fall through.
const GAME_FOLDERS = ['Skyrim VR', 'Skyrim Special Edition', 'Skyrim'];

function localAppData() {
    return process.env.LOCALAPPDATA
        || path.join(process.env.USERPROFILE || '', 'AppData', 'Local');
}

// Which plugins.txt files actually exist, and which one wins.
//
// Returns { chosen, others }. `others` matters: more than one of these files can exist on the
// same machine, and when they disagree the loser is usually a STALE copy left by a different
// launcher or a past install. Picking silently is the danger -- a stale file does not make the
// loader fail, it makes it succeed with a complete, plausible, wrong load order, and every
// answer computed from that order is then confidently wrong. Reporting the rivals is what lets
// the caller say so out loud.
function findPluginsTxt(base = localAppData()) {
    const existing = [];
    for (const game of GAME_FOLDERS) {
        const p = path.join(base, game, 'plugins.txt');
        try { if (fs.existsSync(p)) existing.push(p); } catch (e) { /* unreadable == absent */ }
    }
    // Fall back to the VR path even when nothing exists, so the error names a real location.
    if (!existing.length) {
        return { chosen: path.join(base, GAME_FOLDERS[0], 'plugins.txt'), others: [] };
    }
    return { chosen: existing[0], others: existing.slice(1) };
}

// Resolve the plugins.txt to read. Override with $PLUGINS_TXT; otherwise probe (see above).
function defaultPluginsTxt(base = localAppData()) {
    if (process.env.PLUGINS_TXT) return process.env.PLUGINS_TXT;
    const { chosen, others } = findPluginsTxt(base);
    if (others.length) {
        console.warn([
            '[active-plugins] more than one plugins.txt exists; using:',
            '  ' + chosen,
            ...others.map(o => '  ignoring: ' + o),
            '  They can disagree, and the stale one loads without complaint --',
            '  a wrong load order that never errors. If the wrong file won, set',
            '  PLUGINS_TXT to the one your game actually uses.',
        ].join('\n'));
    }
    return chosen;
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

module.exports = { readActivePlugins, loadActive, defaultPluginsTxt, findPluginsTxt };
