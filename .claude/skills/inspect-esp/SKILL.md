---
name: inspect-esp
description: Inspect an ESP/ESM plugin file and show a summary of all records
disable-model-invocation: true
argument-hint: <PluginName.esp>
---

# Inspect ESP Plugin

Inspect the ESP/ESM file specified by `$ARGUMENTS` and provide a comprehensive summary.

## Workflow

### Option A: Spriggit (preferred — human-readable output)

1. Serialize the ESP to YAML:
   ```bash
   spriggit serialize --InputPath "Data/$ARGUMENTS" --OutputPath "/tmp/esp-inspect" --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion "0.40.0"
   ```
2. Read the generated YAML files to understand the record structure
3. Summarize: record types, counts, editor IDs, masters, notable records

### Option B: xeditlib (programmatic — for deeper analysis)

If xeditlib is installed (`tools/xelib/` exists), run:
```bash
node examples/inspect-esp.js "$ARGUMENTS"
```

Or write a targeted inspection script if the example doesn't cover what's needed.

## Output Format

Provide a structured summary:
- **File info**: name, masters, record count, flags (ESM/ESL)
- **Record type breakdown**: count per signature (SPEL, MGEF, WEAP, etc.)
- **Notable records**: list editor IDs with brief descriptions
- **Scripts**: any VMAD-attached scripts and their properties
- **Potential issues**: casting type mismatches, missing ONAM, ESL FormID range violations
