// Synthetic behavioral contracts for native structured queries. No disk writes.
#include "core/Structured.h"
#include "core/ChangeFormData.h"
#include "core/Verify.h"
#include "SyntheticSave.h"
#include <cstdio>
#include <functional>
using namespace ess;
using namespace ess::structured;
namespace {
int pass = 0, fail = 0;
void Check(bool ok, const char* message) { if(ok) ++pass; else {++fail;std::printf("FAIL: %s\n",message);} }
void Ref(Writer& w,std::uint32_t id) {RefID{id}.write(w);}
SaveFile Make() {
    SaveFile s;s.header.magic="TESV_SAVEGAME";s.header.version=12;s.formVersion=78;s.plugins.hasLite=true;
    s.plugins.full={"Synthetic.esm","Other.esp"};s.formIDs={0x01000123};
    GlobalData g;g.type=3;Writer w;w.u8(4);Ref(w,1);w.f32(12.5f);g.raw=w.buf;s.table1.push_back(std::move(g));
    GlobalData p;p.type=1001;p.papyrus=std::make_unique<Papyrus>();auto& pap=*p.papyrus;
    pap.strings={"","TestScript","OnUpdate","Game","RequestSave","SSSO3Monitor","SafeSave","::bFlag_var","bool"};pap.saveFileVersion=4;
    Script sc;sc.name=1;sc.members.push_back({7,8});pap.scripts.push_back(sc);
    ScriptInstance i;i.id=0xF123456789ABCDEF;i.script=1;i.refID.data=1;i.data=InstanceData{};Variable b;b.type=VType::kBool;b.value=0;i.data->vars.push_back(b);pap.instances.push_back(i);
    auto second=i;second.id=0xABCDEF1234567890;second.refID.data=0;Variable v;v.type=VType::kRef;v.refType=1;v.value=i.id;second.data->vars={v};pap.instances.push_back(second);
    ActiveScript t;t.id=17;t.data=ActiveScriptData{};t.data->unknown5=0;StackFrame f;f.status=0;f.scriptName=1;f.event=2;f.owner=v;t.data->frames.push_back(f);pap.activeScripts.push_back(t);
    pap.reindex();s.table3.push_back(std::move(p));
    ChangeForm cf;cf.refID.data=0x400014;cf.typeField=8;cf.changeFlags=1u<<27;Writer q;q.u32(0);q.u8(0);cf::SetBody(cf,q.buf);s.changeForms.push_back(cf);return s;
}
json::Value Run(const std::string& op,SaveFile& s,const std::vector<std::string>& args={},SaveFile* second=nullptr){return Query(op,s,Parse(op,args),second);}
void Reject(const std::string& op,const std::vector<std::string>& args,const char* name){try{Parse(op,args);Check(false,name);}catch(const std::exception&){Check(true,name);}}
}
int main(){
    {
        auto rich=synthetic::Make();auto re=SaveFile::parse(rich->serialize(),rich->cosave,rich->hasCosave);
        Check(verify::Expected(*rich,*re).identical(),"public save-level synthetic fixture roundtrips");
        Check(Run("recon",*rich).o.at("perType").o.at("REFR").o.at("fail").n==0,"synthetic grouped extras and breadcrumbs fully decode");
        Check(Run("recon",*rich).o.at("perType").o.at("QUST").o.at("ok").n==1,"synthetic quest instances and type0 fully decode");
        Check(re->hasCosave&&re->cosave.empty(),"explicit empty co-save survives in-memory native parser");
    }
    {
        auto missing=Make();missing.table3.clear();auto result=Run("worries",missing);
        Check(result.o.at("shouldWorry").b&&result.o.at("shouldDisableSaving").b&&!result.o.at("diagnosticsAvailable").b,"missing Papyrus returns explicit blocked diagnostics");
        Check(result.o.at("findings").a[0].o.at("code").s=="save.papyrus-missing","missing section has stable native code");
        auto sample=Make();auto report=Run("worries",sample);
        Check(report.o.at("analysisContractVersion").n==2,"combined analyzer contract is explicit");
        Check(!report.o.at("shouldDisableSaving").b,"member and count findings remain advisory");
        for(const auto& f:report.o.at("findings").a) Check(f.o.at("severity").s=="advisory"&&!f.o.at("blocksSaving").b,"structured advisory severity is consistent");
    }
    {
        auto sample=synthetic::Make();auto& p=*sample->papyrus();p.scripts.clear();p.instances.resize(1);p.references.clear();p.activeScripts.clear();p.suspended1.clear();p.suspended2.clear();p.functionMessages.clear();
        TStr parent=p.internString("Form");Script base;base.name=parent;p.scripts.push_back(base);
        for(unsigned i=0;i<40;++i){Script sc;sc.name=p.internString("RoundtripParent"+std::to_string(i));sc.parent=parent;sc.members.push_back({3,4});parent=sc.name;p.scripts.push_back(sc);}
        p.instances[0].script=parent;p.instances[0].data->vars.resize(40);p.reindex();auto reopened=SaveFile::parse(sample->serialize());
        Check(Run("worries",*reopened).o.at("findings").a.empty(),"deep inheritance survives native serialization/parser");
        p.scripts.back().parent=parent;p.reindex();reopened=SaveFile::parse(sample->serialize());auto result=Run("worries",*reopened);bool unknown=false,mismatch=false;
        for(const auto& f:result.o.at("findings").a){unknown=unknown||f.o.at("code").s=="papyrus.unresolved-member-layout";mismatch=mismatch||f.o.at("code").s=="papyrus.member-data-mismatch";}
        Check(unknown&&!mismatch,"cyclic inheritance reopens and remains explicit unknown");
    }
    auto s=Make();auto j=Run("info",s);Check(j.o.at("scriptInstances").n==2&&j.o.at("unattachedInstances").n==1,"native model counts");
    j=Run("dump",s,{"scriptinstances","--limit","1"});Check(j.o.at("count").n==1&&j.o.at("truncated").b,"filtered/limited dump");
    Check(j.o.at("items").a[0].o.at("id").s=="F123456789ABCDEF","64-bit identifier retained exactly as hex");
    Check(Run("dump",s,{"globals"}).o.at("items").a[0].o.at("value").n==12.5,"native global table values");
    try{Run("dump",s,{"nonsense"});Check(false,"unknown subsystem rejected");}catch(const std::exception&){Check(true,"unknown subsystem rejected");}
    Check(Run("dump",s,{"references"}).o.at("count").n==0,"empty subsystem remains valid");
    Check(Run("dump",s,{"scripts","--script","absent"}).o.at("count").n==0,"unmatched filter empty");
    Check(Run("find",s,{"Other.esp:000123"}).o.at("hitCount").n==1,"plugin-form lookup");
    Check(Run("find",s,{"testscript"}).o.at("hitCount").n==3,"script-name lookup covers instances and definitions");
    Check(Run("find-refs",s,{"F123456789ABCDEF"}).o.at("directCount").n>=1,"native reference query");
    Check(!Run("find-refs",s,{"1"}).o.at("found").b,"missing reference target");
    {
        auto graph=Make();auto& p=*graph.papyrus();Variable self;self.type=VType::kRef;self.refType=1;self.value=p.instances[0].id;
        p.instances[0].data->vars={self};
        auto result=Run("find-refs",graph,{"F123456789ABCDEF"});
        Check(result.o.at("directCount").n==2,"self and incoming references retain legacy direct semantics");
        auto cycle=self;cycle.value=p.instances[1].id;p.instances[0].data->vars={cycle};
        result=Run("find-refs",graph,{"F123456789ABCDEF"});
        Check(result.o.at("directCount").n==1&&result.o.at("secondaryCount").n==1,"two-hop cycle can include original target as secondary");
    }
    j=Run("changeform",s,{"14","--raw"});Check(j.o.at("complete").b&&j.o.at("bodyLength").n==5,"structured decoder and raw body");
    Check(j.o.at("bodyBase64").s=="AAAAAAA=","base64 encoding");
    Check(!Run("changeform",s,{"DEAD"}).o.at("found").b,"missing changeform");
    Check(Run("recon",s).o.at("perType").o.at("QUST").o.at("ok").n==1,"coverage uses native byte-complete decode");
    Check(Run("writer-scan",s).o.at("pending_writer_calls").a.empty(),"ordinary activity is not a writer");
    auto& t=s.papyrus()->activeScripts[0];t.data->frames[0].scriptName=3;t.data->frames[0].event=4;
    Check(Run("writer-scan",s).o.at("pending_writer_calls").a.size()==1,"known native writer found");
    SuspendedStack ss;ss.id=91;ss.data=FunctionMessageData{};ss.data->scriptName=5;ss.data->event=6;s.papyrus()->suspended2.push_back(ss);
    Check(Run("writer-scan",s).o.at("pending_writer_calls").a.size()==2,"second suspended table included");
    auto other=Make();Check(Run("globaldata-diff",other,{"second"},&other).o.at("changedCount").n==0,"identical global tables");
    Check(Run("globaldata",s).o.at("count").n==2,"all global tables exposed");
    auto text=Serialize(Run("freeze-report",s));Check(json::Parse(text).str("op")=="freeze-report","structured output parses through existing native JSON reader");
    Check(Run("worries",s).o.contains("reportText"),"analysis report available");
    Check(Run("worries",s).o.contains("shouldWorry") && !Run("worries",s).o.at("shouldDisableSaving").b,"native diagnostic booleans exposed on sound model");
    Reject("dump",{"--apply"},"query cannot accept apply");Reject("info",{"extra"},"extra positional rejected");Reject("find",{},"missing query rejected");Reject("dump",{"--limit","-1"},"negative limit rejected");Reject("dump",{"--limit","1","--limit","2"},"duplicate option rejected");
    {
        auto sample=Make();auto original=sample.serialize();
        auto preview=Edit("set-var",sample,Parse("set-var",{"F123456789ABCDEF"}));
        Check(preview.o.at("variables").a[0].o.at("value").s=="BOOLEAN:false","legacy boolean preview contract");
        Check(sample.serialize()==original,"variable preview has no model side effects");
        auto result=Edit("set-var",sample,Parse("set-var",{"F123456789ABCDEF","0","true"}));
        Check(result.o.at("deltaVerified").b&&!result.o.at("wroteFile").b&&sample.papyrus()->instances[0].data->vars[0].value==1,"variable dry-run simulates exact targeted value without disk write");
        Check(SaveFile::parse(sample.serialize(),sample.cosave)->papyrus()->instances[0].data->vars[0].value==1,"edited primitive survives native serialization/parser");
    }
    for(auto type:{"int","float","str"}){
        auto sample=Make();const auto value=std::string(type)=="int"?"-42":std::string(type)=="float"?"1.25":"synthetic string";
        Edit("set-var",sample,Parse("set-var",{"F123456789ABCDEF","0",value,"--type",type}));
        auto reopened=SaveFile::parse(sample.serialize(),sample.cosave);
        Check(verify::Expected(sample,*reopened).identical(),"every primitive variable type roundtrips");
    }
    {
        auto sample=Make();const std::string value="Grüße 雪";
        const auto result=Edit("set-var",sample,Parse("set-var",{"F123456789ABCDEF","0",value,"--type","str"}));
        Check(result.o.at("newValue").s=="STRING:\""+value+"\"","Unicode string retained in structured edit receipt");
        auto reopened=SaveFile::parse(sample.serialize(),sample.cosave);
        auto preview=Edit("set-var",*reopened,Parse("set-var",{"F123456789ABCDEF"}));
        Check(preview.o.at("variables").a[0].o.at("value").s=="STRING:\""+value+"\"","Unicode string survives native serialization/parser");
        try {Edit("set-var",sample,Parse("set-var",{"F123456789ABCDEF","0",std::string(65536,'x'),"--type","str"}));Check(false,"oversized string rejected");}
        catch(const std::exception&){Check(true,"oversized string rejected");}
        sample.problems.push_back("synthetic structural failure");
        Check(Run("worries",sample).o.at("shouldDisableSaving").b,"structural failure disables native saving diagnostic");
    }
    {
        auto sample=Make();auto result=Edit("set-global",sample,Parse("set-global",{"Other.esp:000123","99.5"}));
        Check(result.o.at("oldValue").n==12.5&&result.o.at("newValue").n==99.5,"global receipt records exact intended delta");
        Check(Run("dump",sample,{"globals"}).o.at("items").a[0].o.at("value").n==99.5,"targeted global edit is observable through native read route");
        auto reopened=SaveFile::parse(sample.serialize(),sample.cosave);Check(verify::Expected(sample,*reopened).identical(),"global edit roundtrips");
    }
    {
        auto sample=Make();auto result=Edit("clean",sample,Parse("clean",{"--unattached"}));
        Check(result.o.at("removedUnattached").n==1&&sample.papyrus()->instances.size()==1,"native cleanup removes exactly unattached instance");
        Check(result.o.at("targets").a.size()==1,"cleanup receipt contains affected identity");
        auto re=SaveFile::parse(sample.serialize());Check(verify::Expected(sample,*re).identical(),"cleanup roundtrips");
    }
    {
        auto sample=Make();auto i=sample.papyrus()->instances[0];i.id=707;i.refID.data=0x800123;sample.papyrus()->instances.push_back(i);sample.papyrus()->reindex();
        auto result=Edit("remove-created",sample,Parse("remove-created",{}));
        Check(result.o.at("removed").n==1&&!sample.papyrus()->findInstance(707),"missing created-form instance removed through native operation");
    }
    {
        auto sample=Make();Writer w;Ref(w,0x400014);for(int i=0;i<6;++i)w.f32(float(i));w.u8(12);w.u8(7);w.u8(8);w.u8(9);
        ChangeForm h;h.refID.data=0x400055;h.typeField=0;h.changeFlags=1u<<2;cf::SetBody(h,w.buf);sample.changeForms.push_back(h);
        auto result=Edit("reset-havok",sample,Parse("reset-havok",{}));auto bytes=cf::Body(sample.changeForms.back());
        Check(result.o.at("success").n==1&&bytes[28]==0&&bytes[29]==0&&bytes[30]==0,"native Havok reset zeros only physics payload");
        Check(std::equal(w.buf.begin(),w.buf.begin()+28,bytes.begin()),"Havok prefix preserved");
    }
    {
        auto sample=Make();Writer w;w.u32(3);Ref(w,0);Ref(w,0x400014);Ref(w,0);ChangeForm f;f.refID.data=0x400066;f.typeField=43;f.changeFlags=1u<<31;cf::SetBody(f,w.buf);sample.changeForms.push_back(f);
        auto result=Edit("cleanse-formlists",sample,Parse("cleanse-formlists",{}));auto bytes=cf::Body(sample.changeForms.back());
        Check(result.o.at("nullEntriesRemoved").n==2&&bytes.size()==7&&bytes[0]==1,"native form-list null removal retains ordered non-null ref");
    }
    {
        auto sample=Make();Check(Run("verify-roundtrip",sample).o.at("identical").b,"roundtrip operation uses actual native parser in memory");
        auto second=Make();Check(Run("changeform-diff",sample,{"second","--quests"},&second).o.at("changedCount").n==0,"native quest diff unchanged");
        second.changeForms[0].changeFlags|=1u<<26;second.changeForms[0].data.push_back(1);second.changeForms[0].length1++;
        Check(Run("changeform-diff",sample,{"second","--quests"},&second).o.at("changedCount").n==1,"native quest diff detects added flag and payload");
    }
    Reject("set-var",{"1","0","true","--apply"},"apply requires explicit output");
    Reject("clean",{"a","b"},"multiple outputs rejected");
    Reject("verify-roundtrip",{"--apply"},"roundtrip query cannot publish");
    std::printf("Structured regressions: %d passed, %d failed\n",pass,fail);return fail?1:0;
}
