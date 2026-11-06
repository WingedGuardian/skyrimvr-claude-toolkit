# SKSE plugins: register a DevBench tool, and fingerprint your build

Two small techniques for SKSE plugins you write or debug alongside this toolkit. Neither is a toolkit component.
**Status:** written from DevBench 1.32.0's own `DevBenchAPI.h` and its upstream vcpkg port README. The toolkit has not
yet run a plugin that registers a tool on Skyrim VR. The build-number gates below are the header's own.

## 1. Let your plugin answer through DevBench instead of a log file

DevBench already runs a localhost MCP and REST server inside the game (see the `devbench` skill). Since DevBench 1.x,
another SKSE plugin can add its own tool to that server. An agent then asks the plugin directly
(`POST http://127.0.0.1:8921/api/tool/<name>` on VR, or the MCP tool of the same name) instead of tailing a log
the plugin writes. That reuses a supported server rather than building your own pipe, socket or polling log.

**Get the API.** `DevBenchAPI.h` and `DevBenchAPI.cpp` are MIT-licensed (the DevBench plugin itself is GPL-3.0), so any
plugin may compile them in. DevBench's download installs them under `Data/Developer/`. Upstream also ships a vcpkg port
(`cmake/ports/devbench-api/` in `alandtse/devbench`), and its README says to prefer it over copying the files, because
copies drift. It is not in the official vcpkg registry, so use it as an overlay port, and check its `portfile.cmake` for
a published `REF` before relying on it.

```cmake
find_package(devbench-api CONFIG REQUIRED)
target_link_libraries(YourPlugin PRIVATE DevBench::API)
```

**Register after `kPostLoad`.** The interface is fetched through an SKSE message to the `devbench` plugin, so ask for it
only once every plugin is loaded. It returns `nullptr` when DevBench is not installed: treat that as "no tool", never as
an error.

```cpp
#include <DevBenchAPI.h>

// Runs on DevBench's LISTENER thread, not the game's main thread. Plain C function: no std::function, no captures.
static void StatusTool(void* /*ctx*/, const char* /*argsJson*/, void* sink, DevBenchAPI::WriteFn write)
{
    const auto json = MyMod::Snapshot();    // read a thread-safe copy the main thread keeps current
    write(sink, json.c_str());              // exactly once, before returning
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    if (m->type != SKSE::MessagingInterface::kPostLoad) return;
    if (auto* dvb = DevBenchAPI::GetDevBenchInterface001()) {
        dvb->RegisterTool("mymod.status",
            R"({"description":"MyMod's live state","inputSchema":{"type":"object","properties":{}},"readOnly":true})",
            &StatusTool, nullptr);
    }
}
```

**The rules the header sets:**
- **Threading.** The handler runs on DevBench's listener thread. Touch no game object there. Keep a snapshot the main
  thread updates (or queue main-thread work and answer from its result), and guard the snapshot with a lock or an atomic
  swap. Reading game state from the listener thread is a race that only shows up under load.
- **One answer.** Call `write(sink, json)` exactly once before returning. The host owns the buffer, so no string
  crosses the DLL boundary.
- **Descriptor:** `{"description": str, "inputSchema": obj, "readOnly": bool}`. Mark a tool that changes nothing
  `readOnly: true`. The toolkit's guards treat every non-read-only DevBench call as a change.
- **Names.** A name already registered is replaced, and `RegisterTool` returns `false`. Prefix names with your mod
  (`mymod.status`).
- **Build gates.** `GetBuildNumber()` returns `MAJOR*10000 + MINOR*100 + PATCH`. `RegisterMenuHandler` exists only on
  builds of at least `10400` (1.4.0), and `RegisterToolExtension` only on builds of at least `10500` (1.5.0). Calling
  either below its gate calls past the end of the interface. `RegisterToolExtension("inspect", "<key>", ...)` adds an
  `inspect kind=<key>` instead of a new top-level tool. DevBench 1.30's `inspect kind=draws` is provided that way by a
  renderer plugin.
- **Events.** `EmitEvent(topic, payloadJson)` publishes to MCP notifications and `GET /api/events?since=N`.

**Check it live.** Start the game with a save loaded, then:
```bash
curl -s http://127.0.0.1:8921/api/tools | grep mymod
curl -s -X POST http://127.0.0.1:8921/api/tool/mymod.status -H "Content-Type: application/json" -d '{}'
```

## 2. Fingerprint the build your plugin was compiled as

CommonLibSSE-NG's `static_assert`s prove the headers agree with themselves at compile time. They cannot tell you that
the DLL in `Data/SKSE/Plugins/` is the one you think. It might be the SE/AE build copied onto VR, a build against an
older CommonLib, or a stale DLL a mod manager left in place. A plugin that reads engine structs can log, once at
`kDataLoaded`, the runtime it runs on and the `sizeof`/`offsetof` values baked into it:

```cpp
SKSE::log::info("runtime {} | Actor {} | PlayerCharacter {} | TESObjectREFR::loadedData +{:#x}",
    REL::Module::IsVR() ? "VR" : "SE/AE", sizeof(RE::Actor), sizeof(RE::PlayerCharacter),
    offsetof(RE::TESObjectREFR, loadedData));
```

Compare that line with the values you expect for the runtime. A mismatch is a wrong-runtime or wrong-header build, found
in one log line instead of a crash.
- **VR differs.** Its node and geometry classes are larger: CommonLib asserts `BSGeometry` at 416 bytes on VR against
  344 on AE.
- **Only log what you read.** Log the members your plugin actually reads, not a fixed list.
- **Credit.** The idea comes from the `LayoutValidator` plugin in ByteBard97/skyrim-re-toolkit (MIT), whose runtime
  checks were run on AE 1.6.1170.
