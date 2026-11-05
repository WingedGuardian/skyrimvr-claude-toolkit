#pragma once
#include "context.hpp"
#include <intrin.h>

namespace skyrim_native {
struct CpuStateFacts {
    bool xsave{},osxsave{},avx{};
    std::uint64_t enabled{},supported{};
    std::uint32_t bytes{};
};
// This revision qualifies x87/SSE/AVX only. Broader enabled states
// refuse; there is no partial-mask or FXSAVE fallback.
inline bool qualifiedState(const CpuStateFacts& f) noexcept {
    return f.xsave && f.osxsave && (f.enabled&3)==3 && !(f.enabled&~7ull) &&
        !(f.enabled&~f.supported) && (!(f.enabled&4) || f.avx) &&
        f.bytes>=576 && f.bytes<=xstateCapacity;
}
inline CpuStateFacts cpuStateFacts() noexcept {
    int cpu[4]{};__cpuidex(cpu,0,0);
    if(cpu[0]<13)return {};
    __cpuidex(cpu,1,0);CpuStateFacts f;
    f.xsave=(cpu[2]&0x04000000)!=0;f.osxsave=(cpu[2]&0x08000000)!=0;
    f.avx=(cpu[2]&0x10000000)!=0;
    if(!f.xsave || !f.osxsave)return f;
    f.enabled=_xgetbv(0);__cpuidex(cpu,13,0);
    f.supported=static_cast<std::uint32_t>(cpu[0]) | (static_cast<std::uint64_t>(static_cast<std::uint32_t>(cpu[3]))<<32);
    f.bytes=static_cast<std::uint32_t>(cpu[1]);return f;
}
}
