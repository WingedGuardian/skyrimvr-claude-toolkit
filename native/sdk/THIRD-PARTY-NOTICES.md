# Third-party notices: native SDK

Nothing here is vendored and none of it ships in the end-user bundle. `sdk.py` fetches each pinned source on the
maintainer's machine and copies its licence text into the SDK prefix (`licenses/`). The receipt hashes those copies.

| Component | Pin | Licence |
|---|---|---|
| CommonLibVR (`alandtse/CommonLibVR`) | see `pins.json` | GPL-3.0-or-later with Modding Exception and GPL-3.0 Linking Exception |
| OpenVR headers (CommonLib submodule) | see `pins.json` | BSD-3-Clause |
| MinHook (`TsudaKageyu/minhook`) | see `pins.json` | BSD-2-Clause, plus BSD-2-Clause Hacker Disassembler Engine notices |
| fmt, spdlog, DirectXTK, DirectXMath, xbyak and the rest | exact versions in `pins.json` | their `share/<package>/copyright` files under `deps/` |

**What this means for a consumer DLL.** A consumer links CommonLib statically and compiles the observation bridge in.
The Modding Exception lets code link against CommonLib and the game without that code itself becoming GPL, but the
CommonLib portion stays under the GPL. If you distribute a consumer DLL, meet the GPL for that portion and carry the
MinHook and other notices. Testing locally distributes nothing. This is a reading of the licence texts, not legal advice.

The toolkit's own files in `native/` are MIT, like the rest of the toolkit.
