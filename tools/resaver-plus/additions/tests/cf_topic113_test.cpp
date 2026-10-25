// Synthetic type113 regression. No save, plugin or game asset is used.
#include "core/ChangeFormData.h"
#include <cstdio>
using namespace ess;
namespace {
int passed=0,failed=0;
void Check(bool good,const char* name){if(good)++passed;else{++failed;std::printf("FAIL: %s\n",name);}}
void VS(Writer& w,std::uint32_t n){if(n<64)w.u8(n<<2);else if(n<16384)w.u16((n<<2)|1);else{n=(n<<2)|2;w.u8(n);w.u8(n>>8);w.u8(n>>16);}}
void Ref(Writer& w){RefID{0x400014}.write(w);}
ChangeForm Form(const Writer& w){ChangeForm f;f.refID.data=0x400055;f.typeField=0;f.changeFlags=1u<<6;cf::SetBody(f,w.buf);return f;}
Writer Make(std::uint32_t count,bool payload=true){
    Writer w;VS(w,2);w.u8(113);Ref(w);w.u8(5);w.i32(6);Ref(w);VS(w,count);
    if(!payload)return w;
    for(std::uint32_t i=0;i<count;++i){w.wstring("synthetic");w.wstring(i%2?"":"topic");w.i32(7);w.i32(8);w.u8(9);Ref(w);Ref(w);Ref(w);w.u8(10);}
    w.u16(11);for(int i=0;i<4;++i)Ref(w);w.u8(22);return w;
}
}
int main(){
    SaveFile s;
    for(auto n:{0u,2u,64u}){
        auto w=Make(n);auto f=Form(w);const auto raw=f.data;const auto d=cf::Decode(s,f);
        Check(d.complete&&d.consumed==w.size(),"counted topic records/footer/successor consumed exactly");
        Check(f.data==raw,"topic decode preserves original bytes");
        if(n<=2)for(std::size_t end=0;end<w.size();++end){auto shortForm=f;shortForm.data.resize(end);shortForm.length1=end;auto cut=cf::Decode(s,shortForm);Check(!cut.complete&&!cut.error.empty()&&cut.consumed<=end,"truncated topic payload rejected within boundary");}
    }
    auto bad=cf::Decode(s,Form(Make(100001,false)));
    Check(!bad.complete&&bad.error.find("implausible count")!=std::string::npos,"excessive topic count rejected");
    std::printf("Topic113 regressions: %d passed, %d failed\n",passed,failed);return failed?1:0;
}
