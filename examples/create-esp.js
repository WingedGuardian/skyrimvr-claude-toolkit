// Example: Create a new ESP file with xelib
// Demonstrates the dry-run convention (read-only first, write only with --commit flag)
//
// Usage:
//   node create-esp.js                   # Dry run -- shows what would be created
//   node create-esp.js --commit          # Actually creates the ESP
//
// Requires: xeditlib, pinned (docs/installing-tools.md), from the toolkit root:
//   npm ci --ignore-scripts --no-audit --no-fund   (xeditlib 1.0.2 @ 06f73ea + koffi 2.15.1, from package-lock.json)

const xelib = require('xeditlib');

const DRY_RUN = !process.argv.includes('--commit');
const ESP_NAME = 'MyNewMod.esp';

async function main() {
    console.log(`=== Create ESP: ${ESP_NAME} ===`);
    console.log(`Mode: ${DRY_RUN ? 'DRY RUN (no files written)' : 'COMMIT (will write ESP)'}\n`);

    xelib.init();
    xelib.setGameMode(xelib.GM_SSE); // Use GM_SSE for both SSE and VR

    // Load Skyrim.esm as a dependency (most mods need it)
    console.log('Loading Skyrim.esm...');
    xelib.loadPlugins('Skyrim.esm', true, false);
    await xelib.waitForLoader(60000);
    console.log('Loaded.\n');

    // Create new file
    const fileH = xelib.addFile(ESP_NAME);
    console.log(`Created: ${ESP_NAME}`);

    // Add Skyrim.esm as a master
    xelib.addMaster(fileH, 'Skyrim.esm');
    console.log('Added master: Skyrim.esm');

    // --- Example: Create a Global Variable ---
    const globGroup = xelib.addElement(fileH, 'GLOB');
    const glob = xelib.addElement(globGroup, 'GLOB');
    xelib.addElementValue(glob, 'EDID', 'MyMod_ExampleGlobal');
    xelib.addElementValue(glob, 'FNAM', 'Short');
    xelib.addElementValue(glob, 'FLTV', '0.0');
    console.log('\nAdded GLOB: MyMod_ExampleGlobal = 0.0');

    // --- Example: Create a Magic Effect ---
    const mgefGroup = xelib.addElement(fileH, 'MGEF');
    const mgef = xelib.addElement(mgefGroup, 'MGEF');
    xelib.addElementValue(mgef, 'EDID', 'MyMod_ExampleEffect');
    xelib.addElementValue(mgef, 'FULL', 'Example Effect');

    // Set magic effect data
    const mgefData = xelib.getElement(mgef, 'Magic Effect Data\\DATA');
    xelib.setIntValue(mgefData, 'Casting Type', 1);  // Fire and Forget
    xelib.setIntValue(mgefData, 'Delivery', 0);       // Self
    console.log('Added MGEF: MyMod_ExampleEffect (Fire and Forget, Self)');

    // --- Example: Create a Spell using the effect ---
    const spelGroup = xelib.addElement(fileH, 'SPEL');
    const spel = xelib.addElement(spelGroup, 'SPEL');
    xelib.addElementValue(spel, 'EDID', 'MyMod_ExampleSpell');
    xelib.addElementValue(spel, 'FULL', 'Example Spell');

    // Set spell header
    const spit = xelib.getElement(spel, 'SPIT');
    xelib.setIntValue(spit, 'Type', 0);      // Spell
    xelib.setIntValue(spit, 'Cast Type', 1);  // Fire and Forget (must match MGEF!)

    console.log('Added SPEL: MyMod_ExampleSpell (Fire and Forget)');

    // Summary
    console.log('\n--- Summary ---');
    console.log(`Records created: 3 (1 GLOB, 1 MGEF, 1 SPEL)`);
    console.log(`File: ${ESP_NAME}`);

    if (DRY_RUN) {
        console.log('\n*** DRY RUN -- no files written ***');
        console.log('Run with --commit to create the ESP file.');
    } else {
        xelib.saveFile(fileH);
        console.log(`\nSaved: ${ESP_NAME}`);
    }

    // Cleanup
    xelib.release(fileH);
    xelib.close();
    console.log('\n=== Done ===');
}

main().catch(e => {
    console.error('FATAL:', e.message);
    try { xelib.close(); } catch {}
    process.exit(1);
});
