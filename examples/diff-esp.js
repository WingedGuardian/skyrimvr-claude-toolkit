// ESP Diff Script -- Compare two ESP files and report differences
// READ-ONLY -- does not modify anything
//
// Usage: node diff-esp.js "Original.esp" "Modified.esp"
// Both ESPs must be in your Skyrim Data/ folder.
//
// Requires: xeditlib, pinned (docs/installing-tools.md), from the toolkit root:
//   npm ci --ignore-scripts --no-audit --no-fund   (xeditlib 1.0.1 @ 19bef09 + koffi 2.15.1, from package-lock.json)

const xelib = require('xeditlib');

const ORIGINAL = process.argv[2];
const MODIFIED = process.argv[3];

if (!ORIGINAL || !MODIFIED) {
    console.error('Usage: node diff-esp.js "Original.esp" "Modified.esp"');
    console.error('Both files must be in your Skyrim Data/ folder.');
    process.exit(1);
}

// Common record signatures to scan
const RECORD_TYPES = [
    'SPEL', 'MGEF', 'GLOB', 'KYWD', 'SNDR', 'SOUN', 'ARTO', 'EXPL',
    'ACTI', 'PERK', 'HAZD', 'ENCH', 'PROJ', 'IDLE', 'MISC', 'FLST',
    'NPC_', 'RACE', 'COBJ', 'BOOK', 'WEAP', 'ARMO', 'AMMO', 'LVLI',
    'QUST', 'ALCH', 'SCRL', 'INGR'
];

function tryGetValue(h, path, fallback = '') {
    try { return xelib.getValue(h, path); } catch { return fallback; }
}

function tryGetFormID(h, local = true) {
    try { return xelib.getFormID(h, local); } catch { return 0; }
}

function fmtLocalFID(fid) {
    return '0x' + (fid & 0x00FFFFFF).toString(16).toUpperCase().padStart(6, '0');
}

// Catalog a file's records into a map: localFormID -> { sig, editorID, json }
function catalogFile(fileH) {
    const catalog = {};

    for (const sig of RECORD_TYPES) {
        let recs;
        try { recs = xelib.getRecords(fileH, sig); } catch { continue; }

        for (const h of recs) {
            const localID = fmtLocalFID(tryGetFormID(h, true));
            let json = '';
            try { json = xelib.elementToJson(h); } catch { json = '(json failed)'; }

            catalog[localID] = {
                sig,
                editorID: tryGetValue(h, 'EDID'),
                json,
            };
            xelib.release(h);
        }
    }

    return catalog;
}

async function main() {
    console.log('=== ESP Diff ===\n');
    console.log(`Original: ${ORIGINAL}`);
    console.log(`Modified: ${MODIFIED}\n`);

    // --- Load and catalog ORIGINAL ---
    xelib.init();
    xelib.setGameMode(xelib.GM_SSE);

    console.log(`Loading ${ORIGINAL}...`);
    xelib.loadPlugins(ORIGINAL, true, false);
    await xelib.waitForLoader(60000);

    const origFileH = xelib.fileByName(ORIGINAL);
    const origCatalog = catalogFile(origFileH);
    console.log(`  Cataloged: ${Object.keys(origCatalog).length} records`);

    xelib.release(origFileH);
    xelib.close();

    // --- Load and catalog MODIFIED ---
    xelib.init();
    xelib.setGameMode(xelib.GM_SSE);

    console.log(`Loading ${MODIFIED}...`);
    xelib.loadPlugins(MODIFIED, true, false);
    await xelib.waitForLoader(60000);

    const modFileH = xelib.fileByName(MODIFIED);
    const modCatalog = catalogFile(modFileH);
    console.log(`  Cataloged: ${Object.keys(modCatalog).length} records\n`);

    xelib.release(modFileH);
    xelib.close();

    // === DIFF ===
    console.log('========================================');
    console.log('DIFF RESULTS');
    console.log('========================================\n');

    // Records in modified but not original (ADDED)
    const addedIDs = Object.keys(modCatalog).filter(id => !origCatalog[id]);
    if (addedIDs.length > 0) {
        console.log(`--- ADDED RECORDS (${addedIDs.length}) ---`);
        for (const id of addedIDs) {
            const r = modCatalog[id];
            console.log(`  + ${id} | ${r.sig} | ${r.editorID}`);
        }
        console.log();
    }

    // Records in original but not modified (REMOVED)
    const removedIDs = Object.keys(origCatalog).filter(id => !modCatalog[id]);
    if (removedIDs.length > 0) {
        console.log(`--- REMOVED RECORDS (${removedIDs.length}) ---`);
        for (const id of removedIDs) {
            const r = origCatalog[id];
            console.log(`  - ${id} | ${r.sig} | ${r.editorID}`);
        }
        console.log();
    }

    // Records that changed (MODIFIED)
    const changedIDs = Object.keys(modCatalog).filter(id =>
        origCatalog[id] && modCatalog[id].json !== origCatalog[id].json
    );
    if (changedIDs.length > 0) {
        console.log(`--- MODIFIED RECORDS (${changedIDs.length}) ---`);
        for (const id of changedIDs) {
            const orig = origCatalog[id];
            const mod = modCatalog[id];
            console.log(`  ~ ${id} | ${orig.sig} | ${orig.editorID}`);

            try {
                const origObj = JSON.parse(orig.json);
                const modObj = JSON.parse(mod.json);

                if (orig.sig === 'SPEL') {
                    const origEffects = origObj['Effects'] || [];
                    const modEffects = modObj['Effects'] || [];
                    if (origEffects.length !== modEffects.length) {
                        console.log(`    Effects: ${origEffects.length} -> ${modEffects.length}`);
                    }
                }

                if (orig.sig === 'MGEF') {
                    if (JSON.stringify(origObj['VMAD'] || {}) !== JSON.stringify(modObj['VMAD'] || {})) {
                        console.log(`    VMAD changed`);
                    }
                    if (JSON.stringify(origObj['Magic Effect Data'] || {}) !== JSON.stringify(modObj['Magic Effect Data'] || {})) {
                        console.log(`    Magic Effect Data changed`);
                    }
                }

                if (orig.sig === 'GLOB') {
                    const origVal = origObj['FLTV'] || origObj['FLTV - Value'];
                    const modVal = modObj['FLTV'] || modObj['FLTV - Value'];
                    if (origVal !== modVal) {
                        console.log(`    Value: ${origVal} -> ${modVal}`);
                    }
                }
            } catch {
                console.log(`    (could not parse JSON for detailed diff)`);
            }
            console.log();
        }
    }

    if (addedIDs.length === 0 && removedIDs.length === 0 && changedIDs.length === 0) {
        console.log('No differences found!');
    }

    console.log(`\nSummary: ${addedIDs.length} added, ${removedIDs.length} removed, ${changedIDs.length} modified`);
    console.log('\n=== Diff complete ===');
}

main().catch(e => {
    console.error('FATAL:', e.message);
    try { xelib.close(); } catch {}
    process.exit(1);
});
