// Example consumer: a passive observer built only from the SDK's exported targets. It reads nothing from the observed
// call but its register packet, changes nothing, and writes counts to its own log.
//
// With no SkyrimNativeExample.site file it installs no hook. It never removes a live hook or unloads after publishing one.
#include <SKSE/Impl/PCH.h>
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/basic_file_sink.h>
#include <spdlog/spdlog.h>

#include "site_config.hpp"
#include "skyrim_native/capture_gate.hpp"
#include "skyrim_native/context.hpp"
#include "skyrim_native/xstate.hpp"

#include <atomic>
#include <chrono>
#include <fstream>
#include <sstream>
#include <thread>

#if !defined(EXCLUSIVE_SKYRIM_VR)
#error "this SDK builds VR-only plugins"
#endif

extern "C" {
void SkyrimNativeExampleWrapper();
std::uintptr_t SkyrimNativeExampleOriginal{};
std::uint64_t SkyrimNativeXStateMask{};
}

namespace {
std::atomic<std::uint64_t> before{}, after{};
skyrim_native::EntryHook hook;
std::atomic<bool> hookLive{};

bool readMemory(std::uintptr_t at, void* out, std::size_t size) noexcept {
    SIZE_T copied = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(at), out, size, &copied) && copied == size;
}

void openLog() {
    auto path = SKSE::log::log_directory();
    if (!path) return;
    *path /= "SkyrimNativeExample.log";
    spdlog::set_default_logger(std::make_shared<spdlog::logger>("example", std::make_shared<spdlog::sinks::basic_file_sink_mt>(path->string(), true)));
    spdlog::flush_on(spdlog::level::info);
}

// Tested separately from the hook decision so that an inert run still says why it is inert.
void installHook() {
    const auto path = std::filesystem::path("Data/SKSE/Plugins/SkyrimNativeExample.site");
    std::ifstream file(path, std::ios::binary);
    if (!file) { spdlog::info("no site file: inert"); return; }
    std::stringstream text;
    text << file.rdbuf();
    const auto contents = text.str();
    const auto config = example::parseConfig(contents);
    if (!config) { spdlog::error("site file refused: inert"); return; }
    const auto facts = skyrim_native::cpuStateFacts();
    if (!skyrim_native::qualifiedState(facts)) { spdlog::error("unqualified CPU state XCR0={:x}: inert", facts.enabled); return; }
    SkyrimNativeXStateMask = facts.enabled;
    const auto site = REL::Module::get().base() + config->rva;
    if (!hook.prepareBridge(site, reinterpret_cast<std::uintptr_t>(&SkyrimNativeExampleWrapper), config->descriptor, SkyrimNativeXStateMask, readMemory)) {
        spdlog::error("prepare refused, status {}: inert", hook.status);
        return;
    }
    SkyrimNativeExampleOriginal = hook.trampoline;
    const bool enabled = hook.enable(readMemory);
    hookLive.store(hook.enabled, std::memory_order_release);   // live even if the retained check then failed
    if (!enabled) { spdlog::error("enable reported status {}; patched={}", hook.status, hook.enabled); return; }
    spdlog::info("hook live at rva {:x}; retained={}", config->rva, hook.retained(readMemory));
}

void report(const char* why) {
    spdlog::info("{}: before={} after={} live={} retained={}", why, before.load(), after.load(), hookLive.load(),
                 hookLive.load() && hook.retained(readMemory));
}

void onMessage(SKSE::MessagingInterface::Message* message) {
    if (!message) return;
    switch (message->type) {
    case SKSE::MessagingInterface::kInputLoaded:
        // A throw must not cross into SKSE's dispatcher, and this DLL can no longer unload: stay inert instead.
        try { installHook(); } catch (...) { spdlog::error("hook installation threw: inert"); }
        break;
    case SKSE::MessagingInterface::kPreLoadGame: report("preload"); break;
    case SKSE::MessagingInterface::kPostLoadGame:
        report(skyrim_native::loadSucceeded(reinterpret_cast<std::uintptr_t>(message->data), message->dataLen) ? "postload-ok" : "postload-failed");
        break;
    default: break;
    }
}
}

// Hot path: counts only. No allocation, logging or locks; the packet's state pointer is never kept.
extern "C" void SkyrimNativeObserve(std::uint32_t, std::uint32_t phase, skyrim_native::Context*) noexcept {
    (phase ? after : before).fetch_add(1, std::memory_order_relaxed);
}

SKSE_PLUGIN_QUERY(const SKSE::QueryInterface* query, SKSE::PluginInfo* info) {
    info->infoVersion = SKSE::PluginInfo::kVersion;
    info->name = "SkyrimNativeExample";
    info->version = 1;
    return !query->IsEditor();
}

SKSE_PLUGIN_LOAD(const SKSE::LoadInterface* skse) {
    try { openLog(); } catch (...) {}   // a locked or odd log path costs the log, not the game. Before Init, which is told not to replace it.
    SKSE::Init(skse, false);
    spdlog::info("loaded; game {} VR={}", REL::Module::get().version().string(), REL::Module::IsVR());
    const auto* messaging = SKSE::GetMessagingInterface();
    return skyrim_native::lifetimeStart(
        [&] { return messaging && messaging->RegisterListener(onMessage); },
        [] {
            std::thread([] {
                for (;;) {
                    std::this_thread::sleep_for(std::chrono::seconds(5));
                    report("tick");
                }
            }).detach();
        },
        [] { spdlog::error("report thread did not start; counts appear only at load messages"); });
}
