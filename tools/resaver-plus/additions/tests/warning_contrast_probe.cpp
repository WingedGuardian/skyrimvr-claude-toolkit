// In-memory diagnostic study. All data invented; no save files are written.
#include "SyntheticSave.h"
#include "core/Ops.h"
#include "core/Structured.h"
#include "core/Verify.h"
#include <algorithm>
#include <cstdio>

using namespace ess;
namespace {
using J=json::Value;
J Obj(){J j;j.type=J::Type::kObject;return j;}
J Arr(){J j;j.type=J::Type::kArray;return j;}
J Text(std::string s){J j;j.type=J::Type::kString;j.s=std::move(s);return j;}
J Num(double n){J j;j.type=J::Type::kNumber;j.n=n;return j;}
J Bool(bool b){J j;j.type=J::Type::kBool;j.b=b;return j;}
std::string Base64(const std::vector<std::uint8_t>& bytes){
    static constexpr char alphabet[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string s;s.reserve((bytes.size()+2)/3*4);
    for(std::size_t i=0;i<bytes.size();i+=3){const auto left=bytes.size()-i;std::uint32_t v=std::uint32_t(bytes[i])<<16;if(left>1)v|=std::uint32_t(bytes[i+1])<<8;if(left>2)v|=bytes[i+2];s+=alphabet[v>>18];s+=alphabet[(v>>12)&63];s+=left>1?alphabet[(v>>6)&63]:'=';s+=left>2?alphabet[v&63]:'=';}return s;
}
std::unique_ptr<SaveFile> Make(const std::string& kind,unsigned n){
    auto s=synthetic::Make();auto& p=*s->papyrus();
    const auto instance=p.instances.front();const auto reference=p.references.front();
    const auto thread=p.activeScripts.front();const auto suspended=p.suspended1.front();
    const auto form=p.internString("Form");Script base;base.name=form;
    Script defined;defined.name=1;defined.parent=form;defined.members.push_back({3,4});
    p.scripts={base,defined};p.instances={instance};p.references.clear();p.arrays.clear();
    p.activeScripts.clear();p.suspended1.clear();p.suspended2.clear();p.functionMessages.clear();p.unbinds.clear();
    s->changeForms.clear();s->reindexChangeForms();s->header.compression=Compression::kLZ4;
    if(kind=="unattached"){
        p.instances.clear();for(unsigned i=0;i<n;++i){auto v=instance;v.id=0x100000000ull+i;v.refID.data=0;p.instances.push_back(v);}
    }else if(kind=="undefined-script"){
        Script undefined;undefined.name=5;p.scripts.push_back(undefined);
    }else if(kind=="undefined-instance"){
        p.scripts.back().parent=0;
    }else if(kind=="undefined-reference"){
        auto v=reference;v.script=p.internString("MissingReferenceScript");p.references.push_back(v);
    }else if(kind=="missing-parent"){
        p.scripts.back().parent=p.internString("MissingParentScript");
    }else if(kind=="memberless"){
        p.instances[0].data->vars.clear();
    }else if(kind=="mismatched-members"){
        p.instances[0].data->vars.push_back(p.instances[0].data->vars.front());
    }else if(kind=="added-member"){
        p.scripts.back().members.push_back({p.internString("::SecondFlag_var"),4});
    }else if(kind=="threads"||kind=="depth"||kind=="total-frames"||kind=="few-frames"||kind=="loop"){
        const auto count=kind=="depth"?1u:kind=="total-frames"?200u:kind=="few-frames"?50u:n;
        StackFrame wait=thread.data->frames.front();
        if(kind=="loop"){
            const auto utility=p.internString("Utility");Script native;native.name=utility;p.scripts.push_back(native);
            wait.scriptName=wait.baseName=utility;wait.event=p.internString("Wait");wait.fnFlags|=2;wait.code.clear();
        }
        for(unsigned i=0;i<count;++i){auto v=thread;v.id=1000+i;
            const auto frame=v.data->frames.front(); // assign() may invalidate an aliased source element
            if(kind=="depth")v.data->frames.assign(n,frame);
            if(kind=="total-frames")v.data->frames.assign(5+(i==0?n-1000:0),frame);
            if(kind=="few-frames")v.data->frames.assign(3+(i==0?n-150:0),frame);
            if(kind=="loop")v.data->frames.push_back(wait);
            p.activeScripts.push_back(std::move(v));
        }
    }else if(kind=="suspended"){
        for(unsigned i=0;i<n;++i){auto v=suspended;v.id=1000+i;p.suspended1.push_back(v);auto active=thread;active.id=v.id;p.activeScripts.push_back(active);}
    }else if(kind=="instances"){
        p.instances.clear();for(unsigned i=0;i<n;++i){auto v=instance;v.id=0x100000000ull+i;v.refID.data=0x400001+i;p.instances.push_back(v);}
    }else if(kind=="created-orphan"){
        p.instances[0].refID.data=0x800123;
    }else if(kind=="zero-formid"){
        s->formIDs[0]=0;
    }else if(kind=="no-papyrus"){
        s->table3.clear();return s;
    }else if(kind!="clean"&&kind!="bad-header"&&kind!="truncated"){
        throw std::runtime_error("unknown warning scenario");
    }
    p.reindex();return s;
}
}
int main(int argc,char** argv){
    try{
        if(argc!=2)throw std::runtime_error("one named synthetic scenario required");
        const std::string scenario=argv[1];const auto colon=scenario.find(':');
        const auto kind=scenario.substr(0,colon);unsigned n=0;
        if(colon!=std::string::npos){std::size_t used;const auto value=std::stoul(scenario.substr(colon+1),&used);if(used!=scenario.size()-colon-1||value>150001)throw std::runtime_error("scenario count outside study bounds");n=static_cast<unsigned>(value);}
        if(kind=="total-frames"&&n!=1000&&n!=1001)throw std::runtime_error("unsupported total-frame contrast");
        if(kind=="few-frames"&&n!=150&&n!=151)throw std::runtime_error("unsupported low-frame contrast");
        auto model=Make(kind,n);auto bytes=model->serialize();
        if(kind=="bad-header")bytes[0]='X';if(kind=="truncated")bytes.pop_back();
        auto result=Obj();result.o["case"]=Text(scenario);result.o["essBase64"]=Text(Base64(bytes));
        result.o["emptyCompanionPresent"]=Bool(true);
        std::unique_ptr<SaveFile> parsed;
        try{
            parsed=SaveFile::parse(bytes,{},true);result.o["parseOk"]=Bool(true);
            auto core=ops::Analyze(*parsed);result.o["coreWarnings"]=Arr();for(const auto& warning:core.warnings)result.o["coreWarnings"].a.push_back(Text(warning));
            result.o["structuralProblems"]=Arr();for(const auto& issue:parsed->problems)result.o["structuralProblems"].a.push_back(Text(issue));
            auto counts=Obj();counts.o["hasPapyrus"]=Bool(parsed->papyrus()!=nullptr);
            if(const auto* p=parsed->papyrus()){
                counts.o["scripts"]=Num(p->scripts.size());counts.o["instances"]=Num(p->instances.size());counts.o["references"]=Num(p->references.size());counts.o["threads"]=Num(p->activeScripts.size());counts.o["suspended"]=Num(p->suspended1.size()+p->suspended2.size());counts.o["unattached"]=Num(core.unattached);counts.o["undefinedThreads"]=Num(core.undefinedThreads);
                std::size_t frames=0,depth=0,vars=0,members=0,undefined=core.undefinedScripts+core.undefinedInstances;for(const auto& r:p->references)undefined+=ops::IsInstanceUndefined(*p,r.script);for(const auto& t:p->activeScripts)if(t.data){frames+=t.data->frames.size();depth=std::max(depth,t.data->frames.size());}for(const auto& i:p->instances)if(i.data)vars+=i.data->vars.size();for(const auto& script:p->scripts)members+=script.members.size();
                counts.o["frames"]=Num(frames);counts.o["maxDepth"]=Num(depth);counts.o["instanceVariables"]=Num(vars);counts.o["declaredMembers"]=Num(members);counts.o["undefinedElements"]=Num(undefined);
            }
            result.o["counts"]=counts;
            result.o["modelVerified"]=Bool(verify::Expected(*model,*parsed).identical());
            try{result.o["assessment"]=structured::Query("worries",*parsed,structured::Parse("worries",{}));}
            catch(const std::exception& e){result.o["assessmentError"]=Text(e.what());}
        }catch(const std::exception& e){result.o["parseOk"]=Bool(parsed!=nullptr);result.o[parsed?"analysisError":"parseError"]=Text(e.what());}
        std::puts(structured::Serialize(result).c_str());return 0;
    }catch(const std::exception& e){std::fprintf(stderr,"%s\n",e.what());return 2;}
}
