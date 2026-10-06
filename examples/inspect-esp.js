// ESP Inspection Script -- READ-ONLY
// Loads an ESP file and catalogs all records by type.
// Usage: node inspect-esp.js "MyMod.esp"
//
// Requires: xeditlib, pinned (docs/installing-tools.md), from the toolkit root:
//   npm install --no-save --ignore-scripts github:WingedGuardian/xeditlib#19bef0976738a9db0760d45975263e2d7eb76062 koffi@2.15.1
// The ESP must be in your Skyrim Data/ folder.

const xelib = require('xeditlib');

const ESP_NAME = process.argv[2];
if (!ESP_NAME) {
    console.error('Usage: node inspect-esp.js "PluginName.esp"');
    process.exit(1);
}

// Common record signatures to scan
const RECORD_TYPES = [
    'SPEL', 'MGEF', 'GLOB', 'KYWD', 'SNDR', 'SOUN', 'ARTO', 'EXPL',
    'ACTI', 'IDLE', 'PERK', 'HAZD', 'PROJ', 'ENCH', 'WEAP', 'ARMO',
    'NPC_', 'RACE', 'QUST', 'FLST', 'COBJ', 'BOOK', 'AMMO', 'LVLI',
    'MISC', 'ALCH', 'SCRL', 'INGR'
];

// Safe value getters
function tryGetValue(h, path, fallback = '(error)') {
    try { return xelib.getValue(h, path); } catch { return fallback; }
}

function tryGetElements(h, path) {
    try { return xelib.getElements(h, path); } catch { return []; }
}

function tryHasElement(h, path) {
    try { return xelib.hasElement(h, path); } catch { return false; }
}

function tryGetFormID(h, local = true) {
    try { return xelib.getFormID(h, local); } catch { return 0; }
}

function fmtFID(fid) {
    return '0x' + fid.toString(16).toUpperCase().padStart(8, '0');
}

function fmtLocalFID(fid) {
    return '0x' + (fid & 0x00FFFFFF).toString(16).toUpperCase().padStart(6, '0');
}

function editorID(h) {
    return tryGetValue(h, 'EDID');
}

// Inspect a MGEF record
function inspectMGEF(h) {
    const info = {
        formID: fmtFID(tryGetFormID(h, false)),
        localID: fmtLocalFID(tryGetFormID(h, true)),
        editorID: editorID(h),
        fullName: tryGetValue(h, 'FULL'),
        castingType: tryGetValue(h, 'Magic Effect Data\\DATA\\Casting Type'),
        delivery: tryGetValue(h, 'Magic Effect Data\\DATA\\Delivery'),
        archetype: tryGetValue(h, 'Magic Effect Data\\DATA\\Archetype'),
    };

    if (tryHasElement(h, 'VMAD')) {
        const scripts = tryGetElements(h, 'VMAD\\Scripts');
        info.scripts = scripts.map(s => {
            const scriptName = tryGetValue(s, 'scriptName');
            const props = tryGetElements(s, 'Properties');
            return {
                name: scriptName,
                properties: props.map(p => ({
                    name: tryGetValue(p, 'propertyName'),
                    type: tryGetValue(p, 'Type'),
                    value: tryGetValue(p, 'Value'),
                }))
            };
        });
    }

    return info;
}

// Inspect a SPEL record
function inspectSPEL(h) {
    const info = {
        formID: fmtFID(tryGetFormID(h, false)),
        localID: fmtLocalFID(tryGetFormID(h, true)),
        editorID: editorID(h),
        fullName: tryGetValue(h, 'FULL'),
        spellType: tryGetValue(h, 'SPIT\\Type'),
        castType: tryGetValue(h, 'SPIT\\Cast Type'),
        effects: [],
    };

    const effects = tryGetElements(h, 'Effects');
    for (const eff of effects) {
        const effInfo = {
            mgefFormID: tryGetValue(eff, 'EFID'),
            magnitude: tryGetValue(eff, 'EFIT\\Magnitude'),
            area: tryGetValue(eff, 'EFIT\\Area'),
            duration: tryGetValue(eff, 'EFIT\\Duration'),
        };

        try {
            const mgefLink = xelib.getLinksTo(eff, 'EFID');
            effInfo.mgefEditorID = editorID(mgefLink);
            effInfo.mgefName = tryGetValue(mgefLink, 'FULL');
            xelib.release(mgefLink);
        } catch {
            effInfo.mgefEditorID = '(unresolved)';
        }

        info.effects.push(effInfo);
    }

    return info;
}

// Main
async function main() {
    console.log(`=== ESP Inspection: ${ESP_NAME} ===\n`);

    xelib.init();
    xelib.setGameMode(xelib.GM_SSE); // Use GM_SSE for both SSE and VR

    console.log('Game path:', xelib.getGamePath(xelib.GM_SSE));
    console.log(`Loading ${ESP_NAME}...`);
    xelib.loadPlugins(ESP_NAME, true, false);
    await xelib.waitForLoader(60000);
    console.log('Loaded.\n');

    const fileH = xelib.fileByName(ESP_NAME);
    const recordCount = xelib.getRecordCount(fileH);
    console.log(`Record count: ${recordCount}`);
    console.log(`Masters: ${xelib.getMasterNames(fileH).join(', ')}\n`);

    // === Record Type Summary ===
    console.log('========================================');
    console.log('RECORD TYPE SUMMARY');
    console.log('========================================\n');

    const foundTypes = {};
    for (const sig of RECORD_TYPES) {
        try {
            const recs = xelib.getRecords(fileH, sig);
            if (recs.length > 0) {
                foundTypes[sig] = recs.length;
                console.log(`  ${sig}: ${recs.length} records`);
                recs.forEach(r => xelib.release(r));
            }
        } catch { /* skip */ }
    }
    console.log();

    // === Detailed MGEF Records ===
    if (foundTypes['MGEF']) {
        console.log('========================================');
        console.log('MAGIC EFFECTS (MGEF)');
        console.log('========================================\n');

        const mgefs = xelib.getRecords(fileH, 'MGEF');
        for (const h of mgefs) {
            const info = inspectMGEF(h);
            console.log(`  ${info.localID} | ${info.editorID}`);
            console.log(`    Name: ${info.fullName}`);
            console.log(`    Casting: ${info.castingType} | Delivery: ${info.delivery} | Archetype: ${info.archetype}`);
            if (info.scripts && info.scripts.length > 0) {
                for (const s of info.scripts) {
                    console.log(`    Script: ${s.name}`);
                    for (const p of s.properties) {
                        console.log(`      - ${p.name}: ${p.type} = ${p.value}`);
                    }
                }
            }
            console.log();
            xelib.release(h);
        }
    }

    // === Detailed SPEL Records ===
    if (foundTypes['SPEL']) {
        console.log('========================================');
        console.log('SPELLS (SPEL)');
        console.log('========================================\n');

        const spels = xelib.getRecords(fileH, 'SPEL');
        for (const h of spels) {
            const info = inspectSPEL(h);
            console.log(`  ${info.localID} | ${info.editorID}`);
            console.log(`    Name: ${info.fullName}`);
            console.log(`    Type: ${info.spellType} | Cast: ${info.castType}`);
            if (info.effects.length > 0) {
                console.log(`    Effects (${info.effects.length}):`);
                for (const e of info.effects) {
                    console.log(`      - MGEF: ${e.mgefEditorID} (${e.mgefName})`);
                    console.log(`        Mag: ${e.magnitude} | Area: ${e.area} | Dur: ${e.duration}`);
                }
            }
            console.log();
            xelib.release(h);
        }
    }

    // === GLOB Records ===
    if (foundTypes['GLOB']) {
        console.log('========================================');
        console.log('GLOBALS (GLOB)');
        console.log('========================================\n');

        const globs = xelib.getRecords(fileH, 'GLOB');
        for (const h of globs) {
            const localID = fmtLocalFID(tryGetFormID(h, true));
            const eid = editorID(h);
            const val = tryGetValue(h, 'FLTV');
            const type = tryGetValue(h, 'FNAM');
            console.log(`  ${localID} | ${eid} = ${val} (${type})`);
            xelib.release(h);
        }
        console.log();
    }

    // Cleanup
    xelib.release(fileH);
    xelib.close();
    console.log('=== Inspection complete ===');
}

main().catch(e => {
    console.error('FATAL:', e.message);
    try { xelib.close(); } catch {}
    process.exit(1);
});
