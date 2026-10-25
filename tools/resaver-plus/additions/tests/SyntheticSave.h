// Public synthetic save-level fixture. Invented data only; never load in game.
#pragma once
#include "core/ChangeFormData.h"

namespace synthetic {
inline void Ref(ess::Writer& w,std::uint32_t id=0x400014){ess::RefID{id}.write(w);}
inline void Floats(ess::Writer& w,int count){for(int i=0;i<count;++i)w.f32(float(i)+0.25f);}
inline ess::Variable Reference(ess::EID id){ess::Variable v;v.type=ess::VType::kRef;v.refType=1;v.value=id;return v;}
inline std::unique_ptr<ess::SaveFile> Make(){
    using namespace ess;
    auto s=std::make_unique<SaveFile>();s->header.magic="TESV_SAVEGAME";s->header.version=12;
    s->header.saveNumber=1;s->header.playerName="Synthetic";s->header.location="Fixture";
    s->header.gameDate="1.0.0";s->header.race="NordRace";s->header.neededXP=1;
    s->header.shotWidth=1;s->header.shotHeight=1;s->header.screenshot={0,0,0,255};
    s->formVersion=78;s->plugins.hasLite=true;s->plugins.full={"Synthetic.esm","Other.esp"};
    s->formIDs={0x01000123};s->unknown3={4,3,2,1};s->hasCosave=true; // intentionally empty opaque companion
    GlobalData globals;globals.type=3;Writer g;g.u8(4);Ref(g,1);g.f32(12.5f);globals.raw=g.buf;s->table1.push_back(std::move(globals));
    GlobalData section;section.type=1001;section.papyrus=std::make_unique<Papyrus>();auto& p=*section.papyrus;
    p.strings={"","TestScript","OnUpdate","::bFlag_var","bool","UndefinedScript"};p.saveFileVersion=4;
    Script defined;defined.name=1;defined.members.push_back({3,4});p.scripts.push_back(defined);
    Script undefined;undefined.name=5;p.scripts.push_back(undefined);
    ScriptInstance one;one.id=0xF123456789ABCDEF;one.script=1;one.refID.data=1;one.data=InstanceData{};
    Variable boolean;boolean.type=VType::kBool;one.data->vars={boolean};p.instances.push_back(one);
    auto two=one;two.id=0xABCDEF1234567890;two.refID.data=0;
    Variable array;array.type=VType::kRefArray;array.refType=1;array.value=0x7777777777777777;two.data->vars={array};p.instances.push_back(two);
    auto three=one;three.id=0x3333333333333333;three.refID.data=0x800123;three.data->vars={Reference(one.id)};p.instances.push_back(three);
    auto four=one;four.id=0x4444444444444444;four.script=5;four.data->vars.clear();p.instances.push_back(four);
    ArrayInfo a;a.id=array.value;a.type=VType::kRef;a.refType=1;a.hasData=true;a.vars={Reference(one.id)};a.length=1;p.arrays.push_back(a);
    ess::Reference r;r.id=0x2222222222222222;r.script=1;r.data=InstanceData{};r.data->vars={Reference(one.id)};p.references.push_back(r);
    auto thread=[&](ThreadID id,TStr script){ActiveScript t;t.id=id;t.data=ActiveScriptData{};t.data->unknown5=0;StackFrame f;f.status=0;f.scriptName=script;f.baseName=script;f.event=2;f.owner=Reference(one.id);Opcode code;code.op=1;for(int i=0;i<3;++i){Param arg;arg.type=3;arg.value=i;code.params.push_back(arg);}f.code.push_back(code);t.data->frames.push_back(f);p.activeScripts.push_back(t);};
    thread(17,1);thread(23,5);
    FunctionMessageData message;message.scriptName=1;message.event=2;message.unknownVar=Reference(one.id);
    FunctionMessage m;m.flag=1;m.id=17;m.flags=1;m.data=message;p.functionMessages.push_back(m);
    SuspendedStack suspended;suspended.id=17;suspended.flag=1;suspended.data=message;p.suspended1.push_back(suspended);
    p.reindex();s->table3.push_back(std::move(section));
    auto form=[&](std::uint32_t id,ChangeFormType type,std::uint32_t flags,const Writer& body,bool compressed=false){ChangeForm f;f.refID.data=id;f.typeField=static_cast<std::uint8_t>(type);f.changeFlags=flags;f.version=1;f.length2=compressed?1:0;cf::SetBody(f,body.buf);s->changeForms.push_back(std::move(f));};
    Writer extra;extra.u8(8);extra.u8(16);extra.u8(22);extra.u8(23);extra.u8(33);Ref(extra);extra.u8(0);
    extra.u8(135);Floats(extra,3);Ref(extra);extra.u32(7);extra.u8(4);Floats(extra,3);Ref(extra);Floats(extra,3);Ref(extra);extra.u8(1);
    form(0x400055,ChangeFormType::REFR,1u<<6,extra,true);
    Writer quest;quest.u8(0);quest.u32(0);quest.u32(0);quest.u8(1);quest.u32(7);quest.f32(1.25f);quest.u32(1);quest.i32(0);Ref(quest);
    quest.u32(1);quest.u8(4);quest.u32(2);quest.u8(4);quest.u32(3);Ref(quest);quest.u8(4);Ref(quest);quest.u32(4);quest.u16(0);quest.u8(0);quest.u8(1);
    form(0x400099,ChangeFormType::QUST,(1u<<28)|(1u<<27)|(1u<<26),quest);
    Writer list;list.u32(3);Ref(list,0);Ref(list);Ref(list,0);form(0x400066,ChangeFormType::FLST,1u<<31,list);
    Writer havok;Ref(havok);Floats(havok,6);havok.u8(16);for(int i=1;i<=4;++i)havok.u8(i);form(0x400077,ChangeFormType::REFR,1u<<2,havok);
    s->reindexChangeForms();return s;
}
}
