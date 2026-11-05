#include "skyrim_native/capture_gate.hpp"
#include <cstdio>
#include <thread>
#include <vector>

namespace {
unsigned checks{}, failures{};
void check(bool pass, const char* name) { ++checks; if (!pass) { ++failures; std::printf("FAIL %s\n", name); } }
}
using namespace skyrim_native;

int main() {
    check(loadSucceeded(1, 1), "successful load is pointer value 1, not dereferenced");
    check(!loadSucceeded(0, 1), "failed load does not arm");
    check(!loadSucceeded(0x10000, 1) && !loadSucceeded(1, 0), "unexpected payload does not arm");

    CaptureGate gate;
    check(!gate.admits(1), "unloaded capture cannot admit");
    gate.loaded();
    const auto stale = gate.ticket();
    gate.preload();
    check(!gate.arm(stale, 100, 30), "preload between request read and commit invalidates the arm ticket");
    gate.loaded();
    check(!gate.arm(gate.ticket(), 100, 0), "zero-length capture refused");
    check(!gate.arm(gate.ticket(), UINT64_MAX - 5, 30), "deadline overflow refused");
    check(gate.arm(gate.ticket(), 100, 30), "fresh loaded generation can arm");
    check(!gate.arm(gate.ticket(), 100, 30), "an armed gate does not re-arm");
    check(gate.admits(129) && !gate.admits(130), "deadline holds on the hot path");
    gate.disarm();
    check(!gate.admits(101), "stop removes capture authority");

    gate.loaded();
    check(gate.arm(gate.ticket(), 100, 30), "re-arm after disarm");
    check(gate.enter(101) && !gate.drained(), "admitted producer is counted");
    gate.disarm();
    check(!gate.drained(), "disarm does not claim a drain while a producer is inside");
    check(!gate.enter(101) && !gate.drained(), "no new producer after disarm, and refused entry leaves the count alone");
    gate.leave();
    check(gate.drained(), "drain completes when the admitted producer leaves");

    bool published = false, failed = false;
    const bool retained = lifetimeStart([&] { published = true; return true; }, [] { throw 7; }, [&] { failed = true; });
    check(retained && published && failed, "start failure keeps the DLL loaded after callback publication");
    bool started = false;
    check(!lifetimeStart([] { return false; }, [&] { started = true; }, [] {}) && !started,
          "no worker starts after failed publication");
    check(!lifetimeStart([]() -> bool { throw 1; }, [&] { started = true; }, [] {}) && !started,
          "a throwing publication is a failed publication");

    // Racing stops and preloads: an admitted producer must always be seen by drained(), and no arm from an old ticket
    // may land after a preload.
    for (unsigned round = 0; round < 2000; ++round) {
        CaptureGate race;
        race.loaded();
        const auto ticket = race.ticket();
        std::atomic<bool> go{false};
        std::atomic<unsigned> admitted{0};
        std::thread armer([&] { while (!go) {} (void)race.arm(ticket, 10, 1000); });
        std::thread stopper([&] { while (!go) {} race.preload(); });
        std::vector<std::thread> producers;
        for (unsigned p = 0; p < 3; ++p)
            producers.emplace_back([&] {
                while (!go) {}
                if (race.enter(20)) { admitted.fetch_add(1); race.leave(); }
            });
        go = true;
        armer.join(); stopper.join();
        for (auto& p : producers) p.join();
        const bool settled = (race.ticket() & 1) == 0;
        check(settled, "preload leaves no save loaded");
        check(race.drained(), "every producer that entered also left");
        check(!race.admits(20) || (race.ticket() & 3) == 3, "admission only ever follows an armed state");
        if ((race.ticket() & 3) == 3) check(false, "an old-ticket arm survived the preload");
    }
    std::printf("checks=%u failures=%u game_code_executed=false\n", checks, failures);
    return failures ? 1 : 0;
}
