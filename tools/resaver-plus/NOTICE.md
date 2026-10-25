# ReSaver Plus (ReSaver++): notices

This folder holds a patch set for **ReSaver++** ("ReSaver Plus"), which `build.py` applies to the
upstream source it fetches. It ships **no upstream source and no binary**.

## Upstream NOTICE (Tonycubed2/ResaverPP, `NOTICE.md` at commit `0d48f60cb8a69b367abfbd78d58418e64c507da5`)

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
| `CMakeLists.txt` | 0005, 0007, 0010, 0017, 0018, 0019, 0020 |
| `src/cli/main.cpp` | 0009, 0017 |
| `src/core/ChangeFormData.cpp` | 0002, 0003, 0004, 0008, 0018 |
| `src/core/ChangeFormData.h` | 0008 |
| `src/core/Ops.cpp` | 0015, 0020 |
| `src/core/Ops.h` | 0015, 0020 |
| `src/core/Papyrus.cpp` | 0013 |
| `src/core/Save.cpp` | 0006, 0012, 0014, 0016 |
| `src/core/Save.h` | 0006, 0012, 0014, 0016 |
| `src/gui/Platform.cpp` | 0001 |

What the patches change:

| Patch | Change |
|---|---|
| 0001 | `#include <shellapi.h>` in the GUI platform file (an MSVC build fix; GUI only) |
| 0002, 0003, 0004, 0018 | Decoder corrections: extra-data type 16, FollowerSwimBreadcrumbs (135), QUST instances / run-data 0, counted SayToTopicInfo (113) |
| 0006 | Full expected-result verification (header, body, globals, Papyrus, co-save) before a written save is published |
| 0008 | Per-decode diagnostics in ChangeFormData |
| 0009, 0017 | Structured JSON command-line entry; UTF-8 wide-argument dispatch and its shell32 link |
| 0012 | In-memory parser entry (verify-roundtrip writes no temporary file) |
| 0013 | Papyrus counts are bounds-checked before allocation |
| 0014 | Co-save presence tracking |
| 0015 | Batched reverse-reference lookup |
| 0016 | Optional write-stage observer used by the fault probes |
| 0020 | Combined advisory analysis |
| 0005, 0007, 0010, 0018, 0019 | CTest and probe targets |

The 15 files under `additions/` (7 source, 8 test) are first-party work of the Skyrim AI Assistant Toolkit project,
licensed under the Apache License 2.0 like the files they extend. The patches themselves are also licensed under the
Apache License 2.0. The rest of the toolkit is MIT (the repository's top-level `LICENSE`); MIT does not cover this
folder.
