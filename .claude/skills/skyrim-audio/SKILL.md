---
name: skyrim-audio
description: Process Skyrim voice and sound files (FUZ, XWM, WAV) using AutoMod CLI.
paths: "**/*.fuz,**/*.xwm,**/*.wav,Data/Sound/**"
---

# Audio File Operations

Use the AutoMod CLI for voice/sound file processing. Always use `--json` for output.

```bash
bash tools/automod-cli.sh audio <command> [args] --json
```

## Commands

- `audio info <path>` — display file details
- `audio extract-fuz <fuz-path> --output <dir>` — extract FUZ into XWM + LIP components
- `audio create-fuz <xwm-path> [--lip <lip-path>] --output <fuz-path>` — pack XWM (+LIP) into FUZ
- `audio wav-to-xwm <wav-path> --output <xwm-path>` — convert WAV to XWM format

## FUZ File Structure

Skyrim voice files are `.fuz` — a container holding:
- **XWM**: the compressed audio (XWMA format)
- **LIP**: lip-sync data for NPC facial animation

To replace a voice line: WAV → XWM → FUZ (with matching LIP if lip-sync needed).
