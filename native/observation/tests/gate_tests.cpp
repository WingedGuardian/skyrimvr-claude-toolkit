#include "skyrim_native/capture_gate.hpp"
#include <chrono>
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
    check(!gate.arm(gate.ticket(), CaptureGate::kMaxTick - 5, 30), "deadline overflow refused");
    check(!gate.arm(gate.ticket(), CaptureGate::kMaxTick + 1, 30), "a start time past the deadline field refused");
    const auto loser = gate.ticket();
    check(gate.arm(loser, 100, 30), "fresh loaded generation can arm");
    check(!gate.arm(gate.ticket(), 100, 30), "an armed gate does not re-arm");
    check(!gate.arm(loser, 100, 5000), "a second request from the same ticket loses");
    check(gate.admits(129) && !gate.admits(130), "deadline holds on the hot path");
    gate.preload();
    gate.loaded();
    const auto next = gate.ticket();
    check(!gate.arm(stale, 100, 9000), "an arm from an older generation cannot land");
    check(gate.arm(next, 200, 50), "the current generation arms");
    check(!gate.arm(stale, 200, 9000) && !gate.admits(250) && gate.admits(249), "a stale arm leaves a live capture's deadline alone");
    gate.disarm();
    check(!gate.admits(201), "stop removes capture authority");

    gate.loaded();
    check(gate.arm(gate.ticket(), 100, 30), "re-arm after disarm");
    check(gate.enter(101) && !gate.drained(), "admitted producer is counted");
    gate.disarm();
    check(!gate.drained(), "disarm does not claim a drain while a producer is inside");
    check(!gate.enter(101) && !gate.drained(), "no new producer after disarm, and refused entry leaves the count alone");
    check(!gate.arm(gate.ticket(), 100, 30), "no new capture while an earlier producer is still inside");
    gate.leave();
    check(gate.drained(), "drain completes when the admitted producer leaves");
    check(gate.arm(gate.ticket(), 100, 30), "a new capture arms once drained");
    gate.disarm();

    bool published = false, failed = false;
    const bool retained = lifetimeStart([&] { published = true; return true; }, [] { throw 7; }, [&] { failed = true; });
    check(retained && published && failed, "start failure keeps the DLL loaded after callback publication");
    bool started = false;
    check(!lifetimeStart([] { return false; }, [&] { started = true; }, [] {}) && !started,
          "no worker starts after failed publication");
    check(!lifetimeStart([]() -> bool { throw 1; }, [&] { started = true; }, [] {}) && !started,
          "a throwing publication is a failed publication");

    // Racing stops: once a stopper sees drained(), nobody admitted may still be inside. Several producers hammer enter()
    // while the stopper disarms mid-flight, so the admit-then-count window is hit if the order is ever wrong.
    unsigned late = 0, admissions = 0;
    for (unsigned round = 0; round < 3000; ++round) {
        CaptureGate race;
        race.loaded();
        check(race.arm(race.ticket(), 10, 1000000), "race round arms");
        std::atomic<bool> stop{false};
        std::atomic<int> inside{0};
        std::atomic<unsigned> admitted{0};
        std::vector<std::thread> producers;
        for (unsigned p = 0; p < 3; ++p)
            producers.emplace_back([&] {
                while (!stop.load(std::memory_order_relaxed)) {
                    if (race.enter(20)) {
                        inside.fetch_add(1, std::memory_order_seq_cst);
                        admitted.fetch_add(1, std::memory_order_relaxed);
                        inside.fetch_sub(1, std::memory_order_seq_cst);
                        race.leave();
                    }
                }
            });
        while (admitted.load(std::memory_order_relaxed) < 8) {}
        race.disarm();
        const auto giveUp = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (!race.drained() && std::chrono::steady_clock::now() < giveUp) {}
        if (!race.drained()) {
            check(false, "a stopped capture drains");
            stop = true;
            for (auto& p : producers) p.join();
            break;
        }
        // From here no producer may be admitted, so nothing may be inside or arrive.
        const auto seen = admitted.load(std::memory_order_seq_cst);
        for (unsigned spin = 0; spin < 200; ++spin)
            if (inside.load(std::memory_order_seq_cst) != 0) { ++late; break; }
        std::this_thread::yield();
        if (admitted.load(std::memory_order_seq_cst) != seen) ++late;
        stop = true;
        for (auto& p : producers) p.join();
        check(race.drained(), "every producer that entered also left");
        check(!race.admits(20), "no admission survives the stop");
        admissions += seen;
    }
    check(late == 0, "a drained gate has no admitted producer left");
    check(admissions > 0, "the race really admitted producers");

    // Arm against preload: whichever wins, no armed state may survive once the preload has landed.
    for (unsigned round = 0; round < 2000; ++round) {
        CaptureGate race;
        race.loaded();
        const auto ticket = race.ticket();
        std::atomic<bool> go{false};
        std::thread armer([&] { while (!go) {} (void)race.arm(ticket, 10, 1000); });
        std::thread stopper([&] { while (!go) {} race.preload(); });
        go = true;
        armer.join();
        stopper.join();
        check((race.ticket() & (CaptureGate::kLoaded | CaptureGate::kArmed)) == 0, "an old-ticket arm cannot survive the preload");
    }
    std::printf("checks=%u failures=%u game_code_executed=false\n", checks, failures);
    return failures ? 1 : 0;
}
