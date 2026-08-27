// skyrim-winner -- "which plugin actually wins this record?"
//
// xEdit already computes conflict resolution correctly and Skyrim's load order
// is explicit and total, so this deliberately builds NO index and keeps no
// cache: it asks xEdit, through xelib, and prints the answer. There is exactly
// one source of truth and it is not this script.
//
// Why it exists: tools/xelib/ holds 276 one-off scripts, 43 of which inspect
// override chains -- and 17 of those load a HAND-PICKED subset of plugins
// rather than the real order. A who-wins answer computed against a guessed
// subset is only as complete as the guess: a plugin outside the list can win
// and the script will never see it. Proven live -- the real MUSCombat chain
// contains Skyrim Extended Cut, which the existing hand-rolled script's
// 7-plugin list omitted. Worse, a short list can silently load NOTHING and
// still resolve (CLAUDE.md #10), giving a confident answer from an empty tree.
// This always loads the full active order. Measured cost: ~4s.
//
// Usage:
//   node tools/skyrim-winner.js winner    <formid> [plugin]
//   node tools/skyrim-winner.js chain     <formid> [plugin]
//   node tools/skyrim-winner.js conflicts <plugin>
//
// FormID accepts 0x0003418E / 0003418E / 3418E, or a qualified
// Plugin.esp:0003418E (the form tools/resaver-resolve-names.js uses). Anything
// else is REJECTED rather than sanitised -- silently stripping stray characters
// turns a typo into a different, real, wrong record and prints RESULT: OK.
//
// [plugin] scopes the lookup to a file that CONTAINS the record (defining it or
// overriding it); masters are always searched either way.
//
// The game root is derived as the parent of tools/, so there is nothing to
// configure; set $GAME_ROOT to override. xelib is resolved from the npm
// package or from a local tools/xelib/xelib.js, whichever is installed.
//
// NOTE: node's own exit code is unreliable here -- koffi's DLL is still
// resident when stdio flushes, so it exits 127 on full success (CLAUDE.md #12).
// Judge the RESULT: line, or use skyrim-winner.sh which does it for you.

const path = require('path');

// The game root is the PARENT of tools/, so it needs no configuration and no
// per-machine path baked into the source. $GAME_ROOT overrides it when the tool
// lives elsewhere. Same contract as tools/resaver-resolve-names.js.
// XEditLib requires a TRAILING SEPARATOR here, and only sometimes says so:
// measured 2026-08-27, a game path without one makes the NEXT setGameMode()
// throw "SetGameMode failed" -- but only when setGamePath is called FIRST. Call
// it after setGameMode and the same unterminated path is tolerated and loads
// all 659 plugins, which is why the sibling tools never tripped over it. Do not
// rely on call order; terminate the path.
function ensureTrailingSep(dir) {
    const last = dir.slice(-1);
    // Both separator styles, on every platform: game paths are Windows paths
    // even when the test running against them is not.
    return (last === path.posix.sep || last === path.win32.sep) ? dir : dir + path.sep;
}

const GAME_PATH = ensureTrailingSep(process.env.GAME_ROOT || path.resolve(__dirname, '..'));
const UNKNOWN = '(unknown file)';

// xelib sits in one of two places depending on how the toolkit was installed:
// the npm package (the documented setup) or a local tools/xelib/xelib.js. Both
// are resolved LAZILY, so this file can be require()d -- and its argument
// parsing tested -- on a machine with neither koffi nor XEditLib.dll present.
let xelib = null;
let loadActive = null;

function loadXelib() {
    if (xelib) return;
    const attempts = ['xeditlib', path.join(__dirname, 'xelib', 'xelib')];
    const errors = [];
    for (const spec of attempts) {
        try { xelib = require(spec); break; } catch (e) { errors.push(spec + ': ' + e.message); }
    }
    if (!xelib) die('cannot load xelib -- ' + errors.join(' | '));
    ({ loadActive } = require(path.join(__dirname, 'xelib', 'active-plugins')));
}

function die(msg) {
    // xelib's fail() appends its NUL-padded exception buffer and can contain
    // newlines. Both must go, or the RESULT marker is multi-line and the
    // wrapper's command substitution warns about null bytes.
    const flat = String(msg)
        .replace(/\0/g, '')
        .replace(/[\r\n]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    console.log('RESULT: ERROR -- ' + flat);
    process.exit(1);
}

// --- input parsing, done BEFORE the 4s load so a typo fails instantly -------

function parseTarget(raw) {
    if (!raw) throw new Error('no FormID given');
    let plugin = null;
    let hex = String(raw).trim();

    const qualified = hex.match(/^(.+\.es[pml])\s*[:|]\s*(.+)$/i);
    if (qualified) {
        plugin = qualified[1];
        hex = qualified[2];
    }
    hex = hex.replace(/^0x/i, '');
    if (!/^[0-9a-fA-F]{1,8}$/.test(hex)) {
        throw new Error('unparseable FormID: "' + raw
            + '" -- expected hex, or Plugin.esp:HEX');
    }
    return { formID: parseInt(hex, 16), plugin };
}

// --------------------------------------------------------------------------

let command, arg1, arg2;
let target = null;

function parseArgv(argv) {
    [, , command, arg1, arg2] = argv;
    const COMMANDS = ['winner', 'chain', 'conflicts'];
    if (!command) die('usage: winner <formid> [plugin] | chain <formid> [plugin] | conflicts <plugin>');
    if (!COMMANDS.includes(command)) die('unknown command: ' + command);

    target = null;
    if (command !== 'conflicts') {
        try {
            target = parseTarget(arg1);
        } catch (e) {
            die(e.message);
        }
        if (arg2) target.plugin = arg2;   // an explicit arg wins over the qualified form
    } else if (!arg1) {
        die('conflicts needs a plugin name');
    }
}

// --- helpers that need a loaded tree ---------------------------------------

function fileNameOrThrow(handle) {
    return xelib.name(xelib.getElementFile(handle));
}

function fileName(handle) {
    try { return fileNameOrThrow(handle); } catch (e) { return UNKNOWN; }
}

function describe(rec) {
    let sig = '?', edid = '';
    try { sig = xelib.signature(rec); } catch (e) {}
    try { edid = xelib.getValue(rec, 'EDID'); } catch (e) {}
    return sig + (edid ? ' ' + edid : '');
}

function openFile(name) {
    // fileByName THROWS on a miss (it calls fail()); it does not return 0, so a
    // falsy-check after it would be dead code.
    try {
        return xelib.fileByName(name);
    } catch (e) {
        die('plugin not loaded: ' + name);
    }
}

// --------------------------------------------------------------------------

function main(argv) {
    parseArgv(argv);
    loadXelib();

    xelib.init();
    xelib.setLanguage('English');
    xelib.setGamePath(GAME_PATH);
    xelib.setGameMode(xelib.GM_SSE);
    xelib.clearMessages();

    const t0 = Date.now();
    const active = loadActive(xelib);

    xelib.waitForLoader().then(() => {
        xelib.clearMessages();
        console.log('# loaded ' + active.length + ' active plugins in '
            + ((Date.now() - t0) / 1000).toFixed(1) + 's (full order)');
        try {
            run();
        } catch (e) {
            die(String(e && e.message ? e.message : e));
        }
    }).catch(e => {
        // Only a genuine loader rejection reaches here. Everything thrown by the
        // command bodies is handled above, so this label cannot send someone
        // debugging the wrong layer.
        die('loader failed: ' + (e.message || '(empty message)'));
    });
}

function findByLocalID(file, localID) {
    // getRecord() wants a FULL FormID -- load-order index in the high byte. A
    // caller writing "Plugin.esp:000871" means the LOCAL id, and passing that
    // straight through searched index 00 (Skyrim.esm) instead, so a record that
    // plainly existed came back as "no record". That false negative is exactly
    // what this tool exists to prevent, so match on the low 3 bytes instead --
    // the same thing the existing hand-rolled scripts do.
    for (const r of xelib.getRecords(file, '', false)) {
        let fid;
        try { fid = xelib.getFormID(r, false); } catch (e) { continue; }
        if ((fid & 0x00FFFFFF) === (localID & 0x00FFFFFF)) return r;
        try { xelib.release(r); } catch (e) {}
    }
    return null;
}

function run() {
    if (command === 'conflicts') return conflicts(arg1);

    let rec = null;
    if (target.plugin) {
        // Scoped: the id is local to that plugin.
        rec = findByLocalID(openFile(target.plugin), target.formID);
        if (!rec) {
            die('no record with local FormID 0x'
                + (target.formID & 0x00FFFFFF).toString(16).toUpperCase().padStart(6, '0')
                + ' in ' + target.plugin);
        }
    } else {
        // Unscoped: treat the input as a full FormID, as before.
        try {
            rec = xelib.getRecord(0, target.formID, true);
        } catch (e) {
            rec = null;
        }
        if (!rec) {
            die('no record with FormID ' + arg1
                + ' -- for a plugin-local id use Plugin.esp:' + arg1);
        }
    }

    return command === 'winner' ? winner(rec) : chain(rec);
}

// --- commands --------------------------------------------------------------

function baseRecord(rec) {
    // getRecord(scope, ...) hands back the record AS IT EXISTS IN scope, which
    // is an override when the named plugin does not define it. Labelling that
    // row "defines" would name the wrong file and drop the real master from the
    // chain entirely.
    try {
        const master = xelib.getMasterRecord(rec);
        return master || rec;
    } catch (e) {
        return rec;
    }
}

function resolveWinner(rec) {
    const name = fileName(xelib.getWinningOverride(rec));
    if (name === UNKNOWN) {
        die("could not resolve the winning override's file -- refusing to "
            + 'report a placeholder as an answer');
    }
    return name;
}

function winner(rec) {
    const base = baseRecord(rec);
    console.log('record   : ' + describe(base));
    console.log('defined  : ' + fileName(base));
    console.log('overrides: ' + xelib.getOverrides(base).length);
    console.log('WINNER   : ' + resolveWinner(base));
    console.log('RESULT: OK');
}

function chain(rec) {
    const base = baseRecord(rec);
    const winFile = resolveWinner(base);
    console.log('record   : ' + describe(base));

    const rows = [[fileName(base), 'defines']];
    for (const o of xelib.getOverrides(base)) rows.push([fileName(o), 'overrides']);

    console.log('chain    : ' + rows.length + ' entr' + (rows.length === 1 ? 'y' : 'ies'));
    rows.forEach(([f, kind], i) => {
        console.log('  ' + String(i).padStart(2) + '. ' + f + '  [' + kind + ']'
            + (i === rows.length - 1 ? '   <-- WINS' : ''));
    });
    if (rows[rows.length - 1][0] !== winFile) {
        console.log('  !! xEdit reports the winner as ' + winFile
            + ', which is NOT the last row above. Trust xEdit.');
    }
    console.log('WINNER   : ' + winFile);
    console.log('RESULT: OK');
}

function conflicts(pluginArg) {
    const file = openFile(pluginArg);
    // Compare against xEdit's CANONICAL filename, not the string as typed.
    // fileByName resolves case-insensitively, so comparing the raw argument
    // reports every record as lost -- to the plugin itself.
    const canonical = fileName(file);
    if (canonical === UNKNOWN) die('could not resolve a canonical name for ' + pluginArg);

    const records = xelib.getRecords(file, '', true);
    let overridden = 0, checked = 0, unreadable = 0;
    const losers = [];

    for (const r of records) {
        checked++;
        let winFile;
        try {
            winFile = fileNameOrThrow(xelib.getWinningOverride(r));
        } catch (e) {
            // A handle we cannot read is UNREADABLE, not a conflict. Counting it
            // as "lost to (unknown file)" is exactly the lie the denominator
            // below exists to prevent.
            unreadable++;
            continue;
        }
        if (winFile !== canonical) {
            overridden++;
            if (losers.length < 40) losers.push([describe(r), winFile]);
        }
    }

    // Denominator before the finding, always.
    console.log('plugin      : ' + canonical);
    console.log('records     : ' + checked);
    console.log('unreadable  : ' + unreadable);
    console.log('overridden  : ' + overridden + ' of ' + checked
        + ' (this plugin does NOT win these)');
    if (losers.length) {
        console.log('-- first ' + losers.length + ' --');
        for (const [what, who] of losers) console.log('  ' + what + '  ->  won by ' + who);
    }
    console.log('RESULT: OK');
}

if (require.main === module) main(process.argv);

// Exported for tests: the pure half needs no DLL, and the argument parsing is
// where a silently-sanitised typo would turn into a real, wrong record.
module.exports = { parseTarget, main, ensureTrailingSep };
