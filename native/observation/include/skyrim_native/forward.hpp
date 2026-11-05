#pragma once
#include <Windows.h>
#include <atomic>
#include <cstdint>

namespace skyrim_native {
struct ForwardCounts {
    std::atomic<std::uint64_t> entered{},forwarded{},returned{},unwound{},observerFaults{};
};
// Void(Main*) contract, preserving LastError across our bookkeeping. The original
// is outside every observer catch; its exception is neither swallowed nor replayed.
template<class Original,class Before,class After>
void forwardOne(ForwardCounts& counts,void* argument,Original original,Before before,After after) {
    const auto incomingError=GetLastError();counts.entered.fetch_add(1);
    try{before();}catch(...){counts.observerFaults.fetch_add(1);}
    SetLastError(incomingError);
    struct Exit {
        ForwardCounts& counts;bool complete{};
        ~Exit() noexcept{if(!complete)counts.unwound.fetch_add(1);}
    } exit{counts};
    counts.forwarded.fetch_add(1);original(argument);
    const auto nativeError=GetLastError();exit.complete=true;counts.returned.fetch_add(1);
    try{after();}catch(...){counts.observerFaults.fetch_add(1);}
    SetLastError(nativeError);
}
}
