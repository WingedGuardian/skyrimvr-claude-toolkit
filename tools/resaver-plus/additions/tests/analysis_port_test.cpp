// Invented native analyzer regressions. No disk saves or game assets.
#include "core/Ops.h"
#include "core/Text.h"
#include <cstdio>
#include <set>
using namespace ess;
namespace {
int passed=0,failed=0;
void Check(bool value,const char* name){if(value)++passed;else{++failed;std::printf("FAIL: %s\n",name);}}
SaveFile Make(){
    SaveFile s;s.header.magic="TESV_SAVEGAME";s.header.version=12;s.formVersion=78;s.plugins.hasLite=true;
    GlobalData section;section.type=1001;section.papyrus=std::make_unique<Papyrus>();auto& p=*section.papyrus;
    p.strings={"","TestScript","OnUpdate","::bFlag_var","bool","Form"};p.saveFileVersion=4;
    Script base;base.name=5;Script child;child.name=1;child.parent=5;child.members.push_back({3,4});p.scripts={base,child};
    ScriptInstance i;i.id=0xF123456789ABCDEF;i.script=1;i.refID.data=0x400014;i.data=InstanceData{};
    Variable b;b.type=VType::kBool;i.data->vars.push_back(b);p.instances.push_back(i);p.reindex();s.table3.push_back(std::move(section));return s;
}
const ops::Finding* Find(const ops::Report& r,const char* code){for(const auto& f:r.findings)if(f.code==code)return &f;return nullptr;}
ops::Report Analyze(SaveFile& s){
    const auto before=s.serialize();auto r=ops::Analyze(s);
    Check(before==s.serialize(),"analysis preserves serialized model bytes");
    std::set<std::string> codes;
    Check(r.findings.size()==r.warnings.size(),"every legacy warning has structured evidence");
    for(const auto& f:r.findings){Check(codes.insert(f.code).second||f.code=="papyrus.wait-loops","stable code groups; repeated loop findings identify different scripts");Check(f.samples.size()<=8,"evidence sample is bounded");Check(!f.blocksSaving||f.code=="save.papyrus-missing","only structural finding blocks saving");}
    return r;
}
void Threads(SaveFile& s,unsigned count,unsigned depth=1){
    auto& p=*s.papyrus();p.activeScripts.clear();
    StackFrame f;f.scriptName=f.baseName=1;f.event=2;f.status=0;
    for(unsigned n=0;n<count;++n){ActiveScript t;t.id=100+n;t.data=ActiveScriptData{};t.data->unknown5=0;t.data->frames.assign(depth,f);p.activeScripts.push_back(t);}p.reindex();
}
}
int main(){
    {auto s=Make();Check(Analyze(s).findings.empty(),"clean control remains quiet");}
    {auto s=Make();auto& p=*s.papyrus();p.instances[0].refID.data=0;auto r=Analyze(s);auto f=Find(r,"papyrus.unattached-instances");Check(f&&f->count==1&&!f->blocksSaving,"one unattached instance is advisory");Check(f&&f->samples[0]=="instance:F123456789ABCDEF","full 64-bit sample identity");}
    {auto s=Make();auto& p=*s.papyrus();auto instance=p.instances[0];p.instances.clear();for(unsigned i=0;i<12;++i){auto v=instance;v.id+=i;v.refID.data=0;p.instances.push_back(v);}p.reindex();auto r=Analyze(s);auto f=Find(r,"papyrus.unattached-instances");Check(f&&f->count==12&&f->samples.size()==8,"total count retained with bounded identities");}
    {auto s=Make();s.papyrus()->instances[0].data->vars.clear();auto r=Analyze(s);Check(Find(r,"papyrus.member-data-missing")&&!Find(r,"papyrus.member-data-mismatch"),"empty values distinguished from mismatched values");}
    {auto s=Make();auto& p=*s.papyrus();p.instances[0].data->vars.push_back(p.instances[0].data->vars.front());Check(Find(Analyze(s),"papyrus.member-data-mismatch"),"more saved values than members");}
    {auto s=Make();auto& p=*s.papyrus();p.scripts[1].members.push_back({3,4});Check(Find(Analyze(s),"papyrus.member-data-mismatch"),"fewer saved values than members");}
    {auto s=Make();auto& p=*s.papyrus();p.scripts[1].members.clear();Check(Find(Analyze(s),"papyrus.member-data-mismatch"),"saved value with zero declared members");p.instances[0].data->vars.clear();Check(Analyze(s).findings.empty(),"zero members and zero values are sound");}
    {auto s=Make();auto& p=*s.papyrus();p.scripts[0].members.push_back({3,4});p.instances[0].data->vars.resize(2);Check(Analyze(s).findings.empty(),"inherited members are included");p.instances[0].data->vars.resize(1);Check(Find(Analyze(s),"papyrus.member-data-mismatch"),"inherited mismatch found");}
    {auto s=Make();auto& p=*s.papyrus();TStr parent=5;for(unsigned i=0;i<1024;++i){Script sc;sc.name=p.internString("Ancestor"+std::to_string(i));sc.parent=parent;sc.members.push_back({3,4});parent=sc.name;p.scripts.push_back(sc);}p.instances[0].script=parent;p.instances[0].data->vars.resize(1024);p.reindex();Check(Analyze(s).findings.empty(),"1024-level inheritance has no arbitrary 32-parent cutoff or recursive overflow");p.instances[0].data->vars.pop_back();Check(Find(Analyze(s),"papyrus.member-data-mismatch"),"deep inherited mismatch found");}
    {auto s=Make();auto& p=*s.papyrus();p.scripts[1].parent=p.internString("AbsentParent");p.reindex();auto r=Analyze(s);Check(Find(r,"papyrus.missing-parents")&&Find(r,"papyrus.unresolved-member-layout"),"missing parent is explicit");Check(!Find(r,"papyrus.member-data-mismatch"),"unknown layout is not diagnosed as a mismatch");}
    {auto s=Make();auto& p=*s.papyrus();Script sc;sc.name=p.internString("Cycle");sc.parent=1;p.scripts[1].parent=sc.name;p.scripts.push_back(sc);p.reindex();auto r=Analyze(s);auto f=Find(r,"papyrus.unresolved-member-layout");Check(f&&f->count==2&&!Find(r,"papyrus.member-data-mismatch"),"inheritance cycle terminates with unknown layout");}
    {auto s=Make();auto& p=*s.papyrus();p.scripts[1].parent=0;Reference ref;ref.id=42;ref.script=1;p.references.push_back(ref);Threads(s,1);auto r=Analyze(s);Check(Find(r,"papyrus.undefined-definitions")&&Find(r,"papyrus.undefined-references")&&Find(r,"papyrus.undefined-execution"),"undefined definitions, references and execution covered");}
    {auto s=Make();auto& p=*s.papyrus();Reference ref;ref.id=42;ref.script=p.internString("AbsentScript");p.references.push_back(ref);p.reindex();Check(Find(Analyze(s),"papyrus.missing-script-definitions"),"reference to absent definition is explicit");}
    {auto s=Make();auto& p=*s.papyrus();p.instances[0].script=p.internString("AbsentScript");p.reindex();auto r=Analyze(s);Check(Find(r,"papyrus.missing-script-definitions")&&!Find(r,"papyrus.member-data-mismatch"),"absent instance definition does not fabricate member mismatch");}
    for(unsigned flags:{0u,1u}){
        auto s=Make();Threads(s,1);auto& p=*s.papyrus();auto external=p.internString("SyntheticExternalGlobalAPI");auto& frame=p.activeScripts[0].data->frames[0];frame.scriptName=frame.baseName=external;frame.fnFlags=static_cast<std::uint8_t>(flags);p.reindex();
        Check(Analyze(s).findings.empty(),"frame-only external global definition is not a missing-instance dependency");
        Reference ref;ref.id=44;ref.script=external;p.references.push_back(ref);p.reindex();Check(Find(Analyze(s),"papyrus.missing-script-definitions"),"same absent name on a reference remains explicit");
    }
    for(unsigned n:{50u,51u,299u,300u}){auto s=Make();Threads(s,n);auto r=Analyze(s);Check(bool(Find(r,"papyrus.stack-load"))==(n>50),"active-thread advisory boundary");Check(bool(Find(r,"papyrus.thread-count-high"))==(n>=300),"native high-thread finding retained");Check(r.frames==n,"total frames counted");}
    for(unsigned n:{99u,100u,101u}){auto s=Make();Threads(s,1,n);auto r=Analyze(s);Check(bool(Find(r,"papyrus.stack-depth"))==(n>=100),"deep-stack boundary");Check(r.maxStackDepth==n&&r.framesByScript[0].n==n,"depth and frame frequency evidence");}
    for(unsigned n:{150u,151u}){auto s=Make();Threads(s,50,3);if(n==151)s.papyrus()->activeScripts[0].data->frames.push_back(s.papyrus()->activeScripts[1].data->frames[0]);auto r=Analyze(s);Check(bool(Find(r,"papyrus.stack-load"))==(n>150),"150-frame advisory boundary");}
    for(unsigned n:{49u,50u}){auto s=Make();s.papyrus()->suspended1.resize(n);Check(bool(Find(Analyze(s),"papyrus.suspended-queue"))==(n>=50),"native suspended-queue boundary retained");}
    for(unsigned n:{19u,20u}){auto s=Make();Threads(s,n);auto& p=*s.papyrus();auto utility=p.internString("Utility");auto wait=p.internString("Wait");for(auto& t:p.activeScripts){auto f=t.data->frames[0];f.baseName=f.scriptName=utility;f.event=wait;f.fnFlags=2;t.data->frames.push_back(f);}auto r=Analyze(s);Check(bool(Find(r,"papyrus.wait-loops"))==(n>=20),"native wait-loop boundary retained");}
    {auto s=Make();s.table3.clear();auto r=Analyze(s);auto f=Find(r,"save.papyrus-missing");Check(f&&f->blocksSaving,"missing Papyrus is a structural error, not a clean report");}
    {
        const auto cp=text::CodePage();text::SetCodePage(1252);
        auto s=Make();auto& p=*s.papyrus();p.strings[1]=std::string(1,char(0xC9))+"Script";p.scripts[1].parent=0;
        Script sc;sc.name=p.internString("Unicode_"+std::string("\xE9\x9B\xAA"));sc.parent=5;p.scripts.push_back(sc);p.reindex();auto r=Analyze(s);
        Check(text::IsUtf8(ops::FormatReport(r)),"mixed original script encodings produce UTF-8 report text");
        for(const auto& f:r.findings)for(const auto& sample:f.samples)Check(text::IsUtf8(sample),"finding identities use UTF-8");
        text::SetCodePage(cp);
    }
    std::printf("%d passed; %d failed\n",passed,failed);return failed?1:0;
}
