#include "skyrim_native/context.hpp"
#include "skyrim_native/xstate.hpp"
#include <Windows.h>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <intrin.h>
#include <thread>
#include <algorithm>


extern "C" void SkyrimNativeCapture(void(*)(void*),void*,skyrim_native::Context*);
extern "C" void SkyrimNativeLeaf(void*);
extern "C" void SkyrimNativeNoOpLeaf(void*);
extern "C" void SkyrimNativeMutatingLeaf(void*);
extern "C" void SkyrimNativeClobberExtended();
#if SKYRIM_NATIVE_HAVE_BRIDGES
extern "C" void SkyrimNativeWrapper0(void*);
extern "C" void SkyrimNativeWrapper1(void*);
#endif
extern "C" {
void(*SkyrimNativeOriginal0)(void*)=SkyrimNativeLeaf;
void(*SkyrimNativeOriginal1)(void*)=SkyrimNativeLeaf;
std::uint64_t SkyrimNativeXStateMask=0;
std::uint64_t SkyrimNativeTestStateMask=0;
}
namespace {
unsigned checks{}, failures{}, observations{};
skyrim_native::Context entry{}, returned{};
bool observeFailure{},observeDisabled{},reenterRequested{};
alignas(16) unsigned char nestedHeap[128]{};
inline constexpr unsigned wrapperCount=2;
std::array<std::array<std::uintptr_t,2>,wrapperCount> wrapperRanges{};
unsigned seenSites[2]{};
unsigned unwindMisses{},alignmentMisses{};
struct Snapshot {
    skyrim_native::Context context{};
    std::array<std::uint8_t,32> alignment{};
    alignas(64) std::array<std::uint8_t,skyrim_native::xstateCapacity> state{};
    Snapshot(){context.xstate=state.data();}
};
void check(bool value,const char* name) {
    ++checks; if(!value){++failures;std::printf("FAIL %s\n",name);}
}
void negativeControl(void* arg) {
    volatile unsigned changed=GetCurrentThreadId(); (void)changed;
    SkyrimNativeLeaf(arg);
}
#if SKYRIM_NATIVE_HAVE_BRIDGES
void throwingOriginal(void*) {throw std::runtime_error("original exception");}
void originalSetsError(void* arg){++*reinterpret_cast<unsigned*>(static_cast<unsigned char*>(arg)+0x78);SetLastError(0x24681357u);}
#endif
}
extern "C" void SkyrimNativeObserveImpl(unsigned site,unsigned phase,const skyrim_native::Context* ctx) noexcept {
    // Deliberately exercise ordinary C++ and Win32 effects. Production observer
    // failures must be contained here, while original exceptions stay outside.
    try {
        if(observeDisabled)return;
        if((reinterpret_cast<std::uintptr_t>(_AddressOfReturnAddress())&15)!=8)++alignmentMisses;
        ++observations;
        if(ctx){auto& copy=phase?returned:entry;copy=*ctx;copy.xstate=nullptr;}
        volatile auto thread=GetCurrentThreadId(); (void)thread;
#if SKYRIM_NATIVE_HAVE_BRIDGES
        void* frames[16]{};const auto count=CaptureStackBackTrace(0,16,frames,nullptr);
        bool found=false;
        for(unsigned n=0;n<count;++n)for(auto range:wrapperRanges){
            const auto pc=reinterpret_cast<std::uintptr_t>(frames[n]);
            if(pc>=range[0] && pc<range[1])found=true;
        }
        if(!found)++unwindMisses;
        if(reenterRequested && phase==0){reenterRequested=false;SkyrimNativeWrapper0(nestedHeap);}
#endif
        SkyrimNativeClobberExtended();
        SetLastError(0xBEEF0000u);
        if(observeFailure)throw std::runtime_error("observer exception");
        if(phase<2)seenSites[phase]=site;
    }catch(...){}
}
int main(int argc,char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX);
    int cpu[4]{};const auto facts=skyrim_native::cpuStateFacts();
    if(!skyrim_native::qualifiedState(facts)){
        std::puts("unsupported XCR0 mask/size");return 2;
    }
    SkyrimNativeTestStateMask=SkyrimNativeXStateMask=facts.enabled;
    std::printf("XCR0=%llx xstate_bytes=%u\n",SkyrimNativeXStateMask,facts.bytes);
    check(skyrim_native::qualifiedState({true,true,true,7,7,832}),"known AVX state configuration admitted");
    check(skyrim_native::qualifiedState({true,true,false,3,3,576}),"legacy-only CPU admitted without narrowing its enabled mask");
    check(!skyrim_native::qualifiedState({false,true,true,7,7,832}),"missing hardware XSAVE refuses");
    check(!skyrim_native::qualifiedState({true,false,true,7,7,832}),"missing OS XSAVE refuses");
    check(!skyrim_native::qualifiedState({true,true,false,7,7,832}),"AVX state without CPU AVX refuses");
    check(!skyrim_native::qualifiedState({true,true,true,7,3,832}),"unsupported enabled component refuses");
    check(!skyrim_native::qualifiedState({true,true,true,0xE7,0xE7,2688}),"unqualified AVX512 configuration refuses");
    check(!skyrim_native::qualifiedState({true,true,true,0x207,0x207,2696}),"unqualified PKRU configuration refuses");
    check(!skyrim_native::qualifiedState({true,true,true,7,7,16385}),"oversized state refuses");
    check(!skyrim_native::qualifiedState({true,true,true,7,7,512}),"missing extended header refuses");
    check(!skyrim_native::qualifiedState({true,true,true,6,7,832}),"missing x87 state refuses");
    const bool control=argc==2 && std::strcmp(argv[1],"--negative-control")==0;
    alignas(16) unsigned char heap[128]{};
    auto* counter=reinterpret_cast<unsigned*>(heap+0x78);
    Snapshot baselineData, actualData;
    auto& baseline=baselineData.context;auto& actual=actualData.context;
    SetLastError(0x13572468u);
    SkyrimNativeCapture(SkyrimNativeLeaf,heap,&baseline);
    check(GetLastError()==0x13572468u,"baseline LastError untouched");
    check(*counter==1,"baseline exact increment");
    if(control) {
        SkyrimNativeCapture(negativeControl,heap,&actual);
        bool equal=true;
        for(unsigned n=0;n<16;++n)if(actual.gpr[n]!=baseline.gpr[n])equal=false;
        check(equal,"ordinary C++ preserves leaf GPR contract (negative control)");
        check((actual.flags&0x8D5)==(baseline.flags&0x8D5),"ordinary C++ preserves leaf arithmetic flags (negative control)");
    } else {
#if SKYRIM_NATIVE_HAVE_BRIDGES
        void(*wrappers[])(void*)={SkyrimNativeWrapper0,SkyrimNativeWrapper1
        };
        for(unsigned n=0;n<wrapperCount;++n){
            DWORD64 base=0;const auto fn=RtlLookupFunctionEntry(reinterpret_cast<DWORD64>(wrappers[n]),&base,nullptr);
            check(fn!=nullptr,"native bridge has unwind metadata");
            if(!fn)return 1;
            wrapperRanges[n]={base+fn->BeginAddress,base+fn->EndAddress};
        }
        for(auto wrapper:wrappers) {
            for(bool failure:{false,true}) {
                *counter=0;observations=0;observeFailure=failure;
                SetLastError(0x13572468u);
                SkyrimNativeCapture(wrapper,heap,&actual);
                check(GetLastError()==0x13572468u,"original LastError preserved across observers");
                check(*counter==1,"wrapped original increments exactly once");
                check(observations==2,"paired observation");
                for(unsigned n=0;n<16;++n)check(actual.gpr[n]==baseline.gpr[n],"caller GPR contract");
                check((actual.flags&0x8D5)==(baseline.flags&0x8D5),"caller arithmetic flags");
                check(std::memcmp(actual.xstate+160,baseline.xstate+160,256)==0,"all XMM0..15");
                check(std::memcmp(actual.xstate,baseline.xstate,5)==0,"x87 control/status/tag");
                check(std::memcmp(actual.xstate+24,baseline.xstate+24,8)==0,"MXCSR and mask");
                for(unsigned component=2;component<=9;++component)if(SkyrimNativeTestStateMask&(1ull<<component)){
                    __cpuidex(cpu,13,static_cast<int>(component));
                    check(std::memcmp(actual.xstate+cpu[1],baseline.xstate+cpu[1],static_cast<unsigned>(cpu[0]))==0,"enabled extended-state component");
                }
                check(entry.gpr[1]==reinterpret_cast<std::uintptr_t>(heap),"original RCX captured");
                check((entry.gpr[7]&0xFF)==0x71,"DIL captured before C++ observer");
            }
        }
        observeFailure=false;
        observeDisabled=true;*counter=0;observations=0;
        SkyrimNativeCapture(SkyrimNativeWrapper0,heap,&actual);
        check(*counter==1 && observations==0,"disabled recorder still forwards original");
        for(unsigned n=0;n<16;++n)check(actual.gpr[n]==baseline.gpr[n],"disabled observer caller GPR");
        observeDisabled=false;
        *counter=0;observations=0;reenterRequested=true;
        SkyrimNativeWrapper0(heap);
        check(*counter==1 && *reinterpret_cast<unsigned*>(nestedHeap+0x78)==1 && observations==4,
              "nested invocation owns independent stack storage and forwards both once");
        *counter=0;observations=0;
        std::thread foreign([&]{SkyrimNativeWrapper1(heap);});foreign.join();
        check(*counter==1 && observations==2,"foreign thread forwards without shared bridge scratch");
        if(SkyrimNativeTestStateMask&4){
            SkyrimNativeXStateMask=3;*counter=0;
            SkyrimNativeCapture(SkyrimNativeWrapper0,heap,&actual);
            __cpuidex(cpu,13,2);
            check(std::memcmp(actual.xstate+cpu[1],baseline.xstate+cpu[1],static_cast<unsigned>(cpu[0]))!=0,
                  "legacy-only mask mutation is caught by independent AVX snapshot");
            SkyrimNativeXStateMask=SkyrimNativeTestStateMask;
        }
        void(**originals[])(void*)={&SkyrimNativeOriginal0,&SkyrimNativeOriginal1
        };
        for(unsigned n=0;n<wrapperCount;++n){
            *originals[n]=throwingOriginal;observations=0;bool escaped=false;
            try{wrappers[n](heap);}catch(const std::runtime_error&){escaped=true;}
            check(escaped,"original exception crosses native unwind metadata");
            check(observations==1,"no after event on original unwind");
            *originals[n]=SkyrimNativeLeaf;
        }
        for(unsigned n=0;n<wrapperCount;++n){
            *originals[n]=SkyrimNativeMutatingLeaf;*counter=0;
            SkyrimNativeCapture(SkyrimNativeMutatingLeaf,heap,&baseline);
            *counter=0;SkyrimNativeCapture(wrappers[n],heap,&actual);
            check(*counter==1,"mutating original executes once");
            for(unsigned i=0;i<16;++i)check(actual.gpr[i]==baseline.gpr[i],"original return GPR state survives after observer");
            check((actual.flags&0x8D5)==(baseline.flags&0x8D5),"original return flags survive after observer");
            check(std::memcmp(actual.xstate+160,baseline.xstate+160,256)==0,"original changed vector state survives after observer");
            *originals[n]=originalSetsError;*counter=0;SetLastError(0x13572468u);
            wrappers[n](heap);
            check(*counter==1 && GetLastError()==0x24681357u,"after observer preserves original's changed LastError");
            *originals[n]=SkyrimNativeLeaf;
        }
        for(unsigned n=0;n<wrapperCount;++n){
            *originals[n]=SkyrimNativeNoOpLeaf;
            for(bool failure:{false,true}){
                *counter=17;observeFailure=failure;observations=0;SetLastError(0x13572468u);
                SkyrimNativeCapture(SkyrimNativeNoOpLeaf,heap,&baseline);
                SkyrimNativeCapture(wrappers[n],heap,&actual);
                check(*counter==17,"allocator RET stub never resurrects the increment");
                check(observations==2 && GetLastError()==0x13572468u,"allocator stub keeps paired observations and original LastError");
                for(unsigned i=0;i<16;++i)check(actual.gpr[i]==baseline.gpr[i],"allocator stub preserves caller GPR contract");
                check((actual.flags&0x8D5)==(baseline.flags&0x8D5),"allocator stub preserves original flags");
                check(std::memcmp(actual.xstate+160,baseline.xstate+160,256)==0,"allocator stub preserves XMM state");
                check(std::memcmp(actual.xstate,baseline.xstate,5)==0,"allocator stub preserves x87 state");
                check(std::memcmp(actual.xstate+24,baseline.xstate+24,8)==0,"allocator stub preserves MXCSR");
                for(unsigned component=2;component<=9;++component)if(SkyrimNativeTestStateMask&(1ull<<component)){
                    __cpuidex(cpu,13,static_cast<int>(component));
                    check(std::memcmp(actual.xstate+cpu[1],baseline.xstate+cpu[1],static_cast<unsigned>(cpu[0]))==0,"allocator stub preserves enabled extended state");
                }
            }
            observeFailure=false;observations=0;
            SkyrimNativeCapture(SkyrimNativeNoOpLeaf,nullptr,&baseline);SkyrimNativeCapture(wrappers[n],nullptr,&actual);
            check(observations==2 && entry.gpr[1]==0 && actual.gpr[1]==0,"allocator stub permits null RCX without argument traversal");
            check(*counter==17,"null allocator stub call does not touch the fixture heap");
            *originals[n]=SkyrimNativeLeaf;
        }
        observeFailure=false;
        check(unwindMisses==0,"Windows stack walker traverses every ordinary/nested/foreign/exception observation");
        check(alignmentMisses==0,"Windows x64 call alignment and writable home slots");
#else
        check(false,"production native bridges are missing");
#endif
    }
    std::printf("checks=%u failures=%u game_code_executed=false\n",checks,failures);
    return failures?1:0;
}
