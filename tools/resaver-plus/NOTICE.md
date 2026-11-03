# ReSaver Plus (ReSaver++): notices

This folder holds a patch set for **ReSaver++** ("ReSaver Plus"), which `build.py` applies to the
upstream source it fetches. It ships **no upstream source and no binary**.

## Upstream NOTICE (Tonycubed2/ResaverPP, `NOTICE.md` at commit `098eb8c1c3a23552c7662a2083f4aca9a2ff41f4`)

> # ReSaver++
>
> ReSaver++ is a C++ port of **ReSaver**, part of FallrimTools by **Mark Fairchild**
> (https://github.com/mdfairch/FallrimTools), licensed under the Apache License 2.0.
> The save-format knowledge and the cleaning rules come from ReSaver; this port re-implements
> them in C++. ReSaver++ is distributed under the same Apache License 2.0 (see LICENSE).
>
> Third-party code in `third_party/`:
> - Dear ImGui - MIT License (Omar Cornut)
> - GLFW - zlib/libpng License
> - LZ4 - BSD 2-Clause License (Yann Collet)
> - miniz - MIT License (Rich Geldreich, Tenacious Software)
> - ImGuiColorTextEdit - MIT License (BalazsJako), modified for the script viewer
>
> The script viewer's colours and keyword classes follow the npp-papyrus Notepad++ plugin
> (https://github.com/blu3mania/npp-papyrus); no code from it is included.

`LICENSE` in this folder is the upstream Apache License 2.0 text, byte for byte.

## Credits

- **Tonycubed2**: ReSaver++ / ReSaver Plus (https://github.com/Tonycubed2/ResaverPP), Apache-2.0.
- **Mark Fairchild**: ReSaver and FallrimTools (https://github.com/mdfairch/FallrimTools), Apache-2.0, the origin
  of the save-format knowledge and the cleaning rules.
- The third-party libraries above. The toolkit's build is the command-line tool only (`-DRESAVERPP_GUI=OFF`), which
  statically links **LZ4** (BSD 2-Clause) and **miniz** (MIT); their notices are in `THIRD-PARTY-NOTICES.md`. Dear
  ImGui, GLFW and ImGuiColorTextEdit are GUI-only and are not compiled into it.

## Modifications by the Skyrim AI Assistant Toolkit (Apache-2.0 §4(b))

The files below are CHANGED by this folder's patches when `build.py` applies them. Upstream's own files carry no
change marker because the patches are applied unchanged and verified byte for byte against `SOURCE-SHA256`; this
section is the prominent notice for every one of them.

| Upstream file changed | By patch |
|---|---|
| `CMakeLists.txt` | 0001 |
| `src/cli/main.cpp` | 0002 |
| `src/core/ChangeFormData.cpp` | 0003 |
| `src/core/ChangeFormData.h` | 0004 |
| `src/core/Ops.cpp` | 0005 |
| `src/core/Ops.h` | 0006 |
| `src/core/Papyrus.cpp` | 0007 |
| `src/core/Save.cpp` | 0008 |
| `src/core/Save.h` | 0009 |
| `tests/unit_test.cpp` | 0010 |

What the patches change:

| Patch | Change |
|---|---|
| 0001 | CTest and probe targets; shell32 link for the command-line tool's UTF-8 arguments |
| 0002 | Structured JSON command-line entry; UTF-8 wide-argument dispatch |
| 0003 | Decoder corrections: extra-data type 16, FollowerSwimBreadcrumbs (135), QUST instances / run-data 0, counted SayToTopicInfo (113); a limit of 16 on extra-data nesting; per-decode diagnostics |
| 0004 | Per-decode diagnostics |
| 0005, 0006 | Batched reverse-reference lookup; combined advisory analysis |
| 0007 | Papyrus counts are bounds-checked before allocation, with the widths of the save's own game edition |
| 0008, 0009 | Full expected-result verification (header, body, globals, Papyrus, co-save) before a written save is published; in-memory parser entry (verify-roundtrip writes no temporary file); co-save presence tracking; optional write-stage observer used by the fault probes |
| 0010 | A unit test no longer reads a quest through a snapshot that has already been destroyed |

The 16 files under `additions/` (7 source, 9 test) are first-party work of the Skyrim AI Assistant Toolkit project,
licensed under the Apache License 2.0 like the files they extend. The patches themselves are also licensed under the
Apache License 2.0. The rest of the toolkit is MIT (the repository's top-level `LICENSE`); MIT does not cover this
folder.
