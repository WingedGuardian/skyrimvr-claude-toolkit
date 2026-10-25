#include "Structured.h"
#include "ChangeFormData.h"
#include "Ops.h"
#include "Text.h"
#include "Verify.h"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <iomanip>
#include <limits>
#include <set>
#include <sstream>

namespace ess::structured {
namespace {
using J = json::Value;
J Object() {
  J j;
  j.type = J::Type::kObject;
  return j;
}
J Array() {
  J j;
  j.type = J::Type::kArray;
  return j;
}
J S(const std::string &s) {
  J j;
  j.type = J::Type::kString;
  j.s = text::ToUtf8(s);
  return j;
}
J N(double n) {
  if (!std::isfinite(n))
    throw std::runtime_error("non-finite JSON number");
  J j;
  j.type = J::Type::kNumber;
  j.n = n;
  return j;
}
J B(bool b) {
  J j;
  j.type = J::Type::kBool;
  j.b = b;
  return j;
}
std::string Hex(std::uint64_t n, int width = 16) {
  std::ostringstream out;
  out << std::uppercase << std::hex << std::setw(width) << std::setfill('0')
      << n;
  return out.str();
}
std::string Low(std::string s) {
  for (auto &c : s)
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}
std::uint64_t Unsigned(const std::string &s, int base = 16) {
  if (s.empty() || s[0] == '-')
    throw std::runtime_error("expected an unsigned integer");
  std::size_t end = 0;
  auto n = std::stoull(s, &end, base);
  if (end != s.size())
    throw std::runtime_error("invalid integer: " + s);
  return n;
}
std::size_t Limit(const Arguments &a) {
  return a.has("--limit") ? Unsigned(a.option("--limit"), 10)
                          : std::numeric_limits<std::size_t>::max();
}
std::string Ref(const SaveFile &s, const RefID &id) {
  const auto full = s.formIDOf(id);
  const auto plugin = id.isZero() || id.type() == RefID::Type::kInvalid || !full
                          ? "?"
                          : s.pluginOf(full);
  return plugin + ":" + Hex(full & 0xFFFFFF, 6);
}
std::vector<const GlobalData *> Globals(const SaveFile &s) {
  std::vector<const GlobalData *> out;
  for (const auto *t : {&s.table1, &s.table2, &s.table3})
    for (const auto &g : *t)
      out.push_back(&g);
  return out;
}
std::vector<std::uint8_t> Bytes(const GlobalData &g) {
  if (!g.papyrus)
    return g.raw;
  Writer w;
  g.papyrus->write(w);
  return w.buf;
}
std::string
HexBytes(const std::vector<std::uint8_t> &bytes, std::size_t at = 0,
         std::size_t length = std::numeric_limits<std::size_t>::max()) {
  std::string s;
  const auto end =
      at + std::min(length, bytes.size() - std::min(at, bytes.size()));
  for (auto i = at; i < end; ++i)
    s += Hex(bytes[i], 2);
  return s;
}
std::string Base64(const std::vector<std::uint8_t> &b) {
  constexpr char alphabet[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string s;
  for (std::size_t i = 0; i < b.size(); i += 3) {
    unsigned n = unsigned(b[i]) << 16;
    if (i + 1 < b.size())
      n |= unsigned(b[i + 1]) << 8;
    if (i + 2 < b.size())
      n |= b[i + 2];
    s += alphabet[(n >> 18) & 63];
    s += alphabet[(n >> 12) & 63];
    s += i + 1 < b.size() ? alphabet[(n >> 6) & 63] : '=';
    s += i + 2 < b.size() ? alphabet[n & 63] : '=';
  }
  return s;
}
bool Match(const SaveFile &s, const RefID &id, const std::string &query) {
  const auto colon = query.rfind(':');
  const auto plug =
      colon == std::string::npos ? std::string() : query.substr(0, colon);
  const auto n =
      Unsigned(colon == std::string::npos ? query : query.substr(colon + 1));
  if (n > 0xFFFFFFFFu)
    throw std::runtime_error("form id exceeds 32 bits");
  const auto full = s.formIDOf(id);
  return (full & 0xFFFFFF) == (n & 0xFFFFFF) &&
         (plug.empty() || EqualsNoCase(s.pluginOf(full), plug));
}
J Instance(const SaveFile &s, const Papyrus &p, const ScriptInstance &i) {
  auto j = Object();
  j.o["id"] = S(Hex(i.id));
  j.o["script"] = S(text::ToUtf8(p.str(i.script)));
  j.o["refid"] = S(Ref(s, i.refID));
  j.o["undefined"] = B(ops::IsInstanceUndefined(p, i.script));
  j.o["unattached"] = B(ops::IsUnattached(i));
  j.o["vars"] = N(i.data ? i.data->vars.size() : 0);
  return j;
}
J Info(SaveFile &s) {
  const auto &p = *s.papyrus();
  const auto r = ops::Analyze(s);
  auto j = Object();
  // The native header does not distinguish SE and VR reliably. The local
  // compatibility adapter may label its known VR corpus; the reusable API must
  // not claim a runtime based only on matching save-format versions.
  j.o["game"] = S("SKYRIM_SE");
  j.o["changeForms"] = N(s.changeForms.size());
  j.o["scriptInstances"] = N(p.instances.size());
  j.o["scripts"] = N(p.scripts.size());
  j.o["references"] = N(p.references.size());
  j.o["structInstances"] = N(0);
  j.o["arrays"] = N(p.arrays.size());
  j.o["activeScripts"] = N(p.activeScripts.size());
  j.o["suspendedStacks"] = N(p.suspended1.size() + p.suspended2.size());
  j.o["unbinds"] = N(p.unbinds.size());
  j.o["functionMessages"] = N(p.functionMessages.size());
  std::size_t undef = r.undefinedScripts + r.undefinedInstances;
  for (const auto &ref : p.references)
    undef += ops::IsInstanceUndefined(p, ref.script);
  j.o["undefinedElements"] = N(undef);
  j.o["undefinedThreads"] = N(r.undefinedThreads);
  j.o["unattachedInstances"] = N(r.unattached);
  return j;
}
J Dump(SaveFile &s, const Arguments &a) {
  auto &p = *s.papyrus();
  auto j = Object();
  const auto sub =
      Low(a.positional.empty() ? "scriptinstances" : a.positional[0]);
  j.o["subsystem"] = S(sub);
  j.o["items"] = Array();
  auto &rows = j.o["items"].a;
  const auto limit = Limit(a);
  const auto filter = Low(a.option("--script"));
  bool more = false;
  auto emit = [&](J row) {
    if (rows.size() < limit)
      rows.push_back(std::move(row));
    else
      more = true;
  };
  if (sub == "scriptinstances")
    for (const auto &i : p.instances) {
      auto row = Instance(s, p, i);
      if (a.has("--undefined-only") && !row.o["undefined"].b)
        continue;
      if (Low(p.str(i.script)).find(filter) == std::string::npos)
        continue;
      emit(std::move(row));
    }
  else if (sub == "references")
    for (const auto &r : p.references) {
      bool u = ops::IsInstanceUndefined(p, r.script);
      if (a.has("--undefined-only") && !u)
        continue;
      if (Low(p.str(r.script)).find(filter) == std::string::npos)
        continue;
      auto row = Object();
      row.o["id"] = S(Hex(r.id));
      row.o["script"] = S(text::ToUtf8(p.str(r.script)));
      row.o["undefined"] = B(u);
      row.o["vars"] = N(r.data ? r.data->vars.size() : 0);
      emit(row);
    }
  else if (sub == "scripts")
    for (const auto &sc : p.scripts) {
      bool u = ops::IsScriptUndefined(p, sc);
      if (a.has("--undefined-only") && !u)
        continue;
      if (Low(p.str(sc.name)).find(filter) == std::string::npos)
        continue;
      auto row = Object();
      row.o["name"] = S(text::ToUtf8(p.str(sc.name)));
      row.o["undefined"] = B(u);
      row.o["members"] = N(sc.members.size());
      emit(row);
    }
  else if (sub == "activescripts")
    for (const auto &t : p.activeScripts) {
      bool u = ops::IsThreadUndefined(p, t);
      if (a.has("--undefined-only") && !u)
        continue;
      auto row = Object();
      row.o["id"] = S(Hex(t.id, 8));
      row.o["undefined"] = B(u);
      row.o["terminated"] = B(ops::IsThreadTerminated(p, t));
      row.o["attached"] = J{};
      row.o["instance"] = J{};
      if (t.data && !t.data->frames.empty()) {
        const auto &owner = t.data->frames.front().owner;
        if (owner.type == VType::kRef) {
          const auto *instance = p.findInstance(owner.value);
          const auto *reference = p.findReference(owner.value);
          if (instance || reference) {
            const auto name =
                p.str(instance ? instance->script : reference->script);
            row.o["instance"] = S(text::ToUtf8(name) + " " + Hex(owner.value));
            row.o["instanceId"] = S(Hex(owner.value));
            row.o["instanceScript"] = S(text::ToUtf8(name));
          }
        }
      }
      emit(row);
    }
  else if (sub == "changeforms")
    for (const auto &f : s.changeForms) {
      const auto type = std::string(ChangeFormTypeName(f.typeCode()));
      if (a.has("--type") && !EqualsNoCase(type, a.option("--type")))
        continue;
      auto row = Object();
      row.o["refid"] = S(Ref(s, f.refID));
      row.o["type"] = S(type);
      row.o["version"] = N(f.version);
      row.o["compressed"] = B(f.length2 > 0);
      emit(row);
    }
  else if (sub == "structinstances") {
  } // Skyrim has no Fallout4 struct-instance table.
  else if (sub == "globals") {
    for (const auto *g : Globals(s))
      if (g->type == 3) {
        Reader r(g->raw.data(), g->raw.size());
        std::uint32_t count = 0;
        auto first = r.u8();
        count = first;
        if ((first & 3) != 0) {
          count |= unsigned(r.u8()) << 8;
          if ((first & 3) != 1)
            count |= unsigned(r.u8()) << 16;
        }
        count >>= 2;
        for (std::uint32_t n = 0; n < count; ++n) {
          auto id = RefID::read(r);
          auto v = r.f32();
          auto row = Object();
          row.o["global"] = S(Ref(s, id) + " = " + std::to_string(v));
          row.o["value"] = std::isfinite(v) ? N(v) : J{};
          emit(row);
        }
        if (r.remaining())
          throw std::runtime_error("global-variable table has unparsed bytes");
      }
  } else {
    throw std::runtime_error("unknown dump subsystem: " + sub);
  }
  j.o["count"] = N(rows.size());
  if (more)
    j.o["truncated"] = B(true);
  return j;
}
J Tree(const cf::Node &n, std::size_t depth) {
  auto j = Object();
  j.o["label"] = S(n.label);
  j.o["offset"] = N(n.offset);
  j.o["size"] = N(n.size);
  j.o["value"] = S(n.value);
  if (n.isRef) {
    j.o["refRaw"] = S(Hex(n.refRaw, 6));
    j.o["formID"] = S(Hex(n.formID, 8));
  }
  j.o["children"] = Array();
  if (depth)
    for (const auto &c : n.kids)
      j.o["children"].a.push_back(Tree(c, depth - 1));
  else if (!n.kids.empty())
    j.o["truncated"] = B(true);
  return j;
}
J Change(SaveFile &s, const Arguments &a) {
  const auto query = a.positional.empty() ? "00000014" : a.positional[0];
  std::vector<const ChangeForm *> matches;
  for (const auto &f : s.changeForms)
    if (Match(s, f.refID, query))
      matches.push_back(&f);
  auto j = Object();
  j.o["refid"] = S(query);
  j.o["found"] = B(!matches.empty());
  if (matches.empty())
    return j;
  const auto *f = matches[0];
  for (auto *m : matches)
    if (m->typeCode() == static_cast<unsigned>(ChangeFormType::ACHR)) {
      f = m;
      break;
    }
  const auto d = cf::Decode(s, *f);
  j.o["actualRefid"] = S(Ref(s, f->refID));
  j.o["type"] = S(ChangeFormTypeName(f->typeCode()));
  j.o["matchCount"] = N(matches.size());
  j.o["compressed"] = B(f->length2 > 0);
  j.o["parsed"] = B(d.supported);
  j.o["complete"] = B(d.complete);
  j.o["hasUnparsed"] = B(!d.complete);
  j.o["consumed"] = N(d.consumed);
  j.o["bodyLength"] = N(d.bodySize);
  j.o["parseError"] = d.error.empty() ? J{} : S(d.error);
  j.o["failureCategory"] = S(d.failureCategory);
  j.o["failurePath"] = S(d.failurePath);
  auto depth = Unsigned(a.option("--depth", "8"), 10);
  if (depth > 256)
    throw std::runtime_error("depth exceeds 256");
  j.o["tree"] = Tree(d.root, depth);
  if (a.has("--raw")) {
    const auto b = cf::Body(*f);
    j.o["changeFlags"] = S(Hex(f->changeFlags, 8));
    j.o["bodyHex"] = S(HexBytes(b));
    j.o["bodyBase64"] = S(Base64(b));
  }
  return j;
}
J Recon(SaveFile &s, const Arguments &a, bool scan) {
  auto j = Object();
  j.o["overlayLoaded"] = B(false);
  j.o["decoderCorrections"] = B(true);
  j.o["perType"] = Object();
  j.o["failureCategories"] = Object();
  j.o["unknownTypes"] = Array();
  j.o["unknownBySource"] = Object();
  j.o["nonExtraLoc"] = Object();
  j.o["unsupportedTypes"] = Object();
  j.o["failures"] = Array();
  std::map<int, std::pair<std::size_t, std::map<int, std::size_t>>> unknown;
  std::map<int, std::vector<std::string>> examples;
  std::size_t examined = 0;
  for (const auto &f : s.changeForms) {
    const auto type = std::string(ChangeFormTypeName(f.typeCode()));
    if (scan) {
      if (type != "REFR" && type != "ACHR")
        continue;
      if (a.has("--type") && !EqualsNoCase(type, a.option("--type")))
        continue;
      if (examined >= Limit(a))
        break;
    }
    ++examined;
    const auto d = cf::Decode(s, f);
    if (d.failureCategory == "unsupported") {
      auto &count = j.o["unsupportedTypes"].o[type];
      if (count.type == J::Type::kNull)
        count = N(0);
      count.n++;
      continue;
    }
    auto &t = j.o["perType"].o[type];
    if (t.type == J::Type::kNull) {
      t = Object();
      t.o["ok"] = N(0);
      t.o["fail"] = N(0);
      t.o["unsupported"] = N(0);
    }
    t.o[d.complete ? "ok" : "fail"].n++;
    if (!d.supported)
      t.o["unsupported"].n++;
    if (d.complete)
      continue;
    auto &count = j.o["failureCategories"].o[d.failureCategory];
    if (count.type == J::Type::kNull)
      count = N(0);
    count.n++;
    if (j.o["failures"].a.size() < 100) {
      auto e = Object();
      e.o["refid"] = S(Ref(s, f.refID));
      e.o["type"] = S(type);
      e.o["category"] = S(d.failureCategory);
      e.o["consumed"] = N(d.consumed);
      e.o["bodySize"] = N(d.bodySize);
      e.o["error"] = S(d.error);
      e.o["path"] = S(d.failurePath);
      j.o["failures"].a.push_back(e);
    }
    if (d.failureCategory == "extra-unknown-type" && !d.extraTrace.empty()) {
      const auto &last = d.extraTrace.back();
      auto &u = unknown[last.type];
      ++u.first;
      ++u.second[last.predecessor];
      auto &sample = examples[last.type];
      if (sample.size() < 5)
        sample.push_back(Ref(s, f.refID));
      auto &source =
          j.o["unknownBySource"].o[type + ":" + std::to_string(last.type)];
      if (source.type == J::Type::kNull)
        source = N(0);
      source.n++;
    } else {
      std::string location = "?";
      for (const auto *part :
           {"ANIMATIONS", "HAVOK", "INVENTORY", "EXTRADATA", "BASE_OBJECT",
            "INITIAL", "NPC_", "QUST", "FLST", "RELA"})
        if (d.failurePath.find(part) != std::string::npos) {
          location = part;
          break;
        }
      auto &count = j.o["nonExtraLoc"].o[location];
      if (count.type == J::Type::kNull)
        count = N(0);
      count.n++;
    }
  }
  for (const auto &[type, u] : unknown) {
    auto x = Object();
    x.o["type"] = N(type);
    x.o["count"] = N(u.first);
    x.o["preds"] = Object();
    x.o["examples"] = Array();
    for (const auto &sample : examples[type])
      x.o["examples"].a.push_back(S(sample));
    for (const auto &[pred, n] : u.second)
      x.o["preds"].o[std::to_string(pred)] = N(n);
    j.o["unknownTypes"].a.push_back(x);
  }
  j.o["examined"] = N(examined);
  j.o["syncLimit"] = S("types after the first failure are not interpreted");
  return j;
}
bool Pending(const std::string &script, const std::string &fn) {
  const auto s = Low(script), f = Low(fn);
  return (s.find("ssso") != std::string::npos && f == "safesave") ||
         (s == "game" &&
          (f == "savegame" || f == "requestsave" || f == "requestautosave"));
}
J Writers(const Papyrus &p) {
  auto j = Object();
  j.o["pending_writer_calls"] = Array();
  auto add = [&](std::string id, const FunctionMessageData &m) {
    if (Pending(p.str(m.scriptName), p.str(m.event)))
      j.o["pending_writer_calls"].a.push_back(
          S(id + ":" + p.str(m.scriptName) + "." + p.str(m.event)));
  };
  for (const auto &t : p.activeScripts) {
    if (ops::IsThreadTerminated(p, t))
      continue;
    if (!t.data)
      throw std::runtime_error("active thread has unknown frames");
    for (const auto &f : t.data->frames)
      if (Pending(p.str(f.scriptName), p.str(f.event)))
        j.o["pending_writer_calls"].a.push_back(
            S(Hex(t.id, 8) + ":" + p.str(f.scriptName) + "." + p.str(f.event)));
  }
  for (const auto *ss : {&p.suspended1, &p.suspended2})
    for (const auto &x : *ss)
      if (x.data)
        add("suspended:" + Hex(x.id, 8), *x.data);
  for (const auto &m : p.functionMessages)
    if (m.data)
      add("message:" + (m.id ? Hex(*m.id, 8) : "none"), *m.data);
  return j;
}
J Find(SaveFile &s, const Arguments &a) {
  const auto q = a.positional.at(0);
  auto &p = *s.papyrus();
  bool form = q.find(':') != std::string::npos;
  try {
    if (Unsigned(q) <= 0xFFFFFFFFu)
      form = true;
  } catch (const std::exception &) {
  }
  auto j = Object();
  j.o["query"] = S(q);
  j.o["mode"] =
      S(form ? (q.find(':') != std::string::npos ? "plugin:formid"
                                                 : "formid(any-plugin)")
             : "scriptName");
  j.o["hits"] = Array();
  auto &hits = j.o["hits"].a;
  for (const auto &i : p.instances)
    if (form ? Match(s, i.refID, q)
             : Low(p.str(i.script)).find(Low(q)) != std::string::npos) {
      auto x = Instance(s, p, i);
      x.o["kind"] = S("scriptInstance");
      hits.push_back(x);
    }
  if (form) {
    for (const auto &f : s.changeForms)
      if (Match(s, f.refID, q)) {
        auto x = Object();
        x.o["kind"] = S("changeForm");
        x.o["refid"] = S(Ref(s, f.refID));
        x.o["type"] = S(ChangeFormTypeName(f.typeCode()));
        hits.push_back(x);
      }
  } else {
    for (const auto &r : p.references)
      if (Low(p.str(r.script)).find(Low(q)) != std::string::npos) {
        auto x = Object();
        x.o["kind"] = S("reference");
        x.o["id"] = S(Hex(r.id));
        x.o["script"] = S(text::ToUtf8(p.str(r.script)));
        hits.push_back(x);
      }
    for (const auto &sc : p.scripts)
      if (Low(p.str(sc.name)).find(Low(q)) != std::string::npos) {
        auto x = Object();
        x.o["kind"] = S("script");
        x.o["name"] = S(text::ToUtf8(p.str(sc.name)));
        x.o["undefined"] = B(ops::IsScriptUndefined(p, sc));
        hits.push_back(x);
      }
  }
  j.o["hitCount"] = N(hits.size());
  return j;
}
J Referrers(Papyrus &p, const Arguments &a) {
  auto id = Unsigned(a.positional.at(0));
  auto j = Object();
  j.o["target"] = S(a.positional[0]);
  const bool found =
      p.findInstance(id) || p.findReference(id) || p.findArray(id) ||
      (id <= 0xFFFFFFFFu && p.findActive(static_cast<ThreadID>(id)));
  j.o["found"] = B(found);
  if (!found)
    return j;
  j.o["targetType"] = S(p.findInstance(id)    ? "ScriptInstance"
                        : p.findReference(id) ? "Reference"
                        : p.findArray(id)     ? "ArrayInfo"
                                              : "ActiveScript");
  j.o["targetDesc"] = S(j.o["targetType"].s + " " + Hex(id));
  j.o["referees"] = Array();
  j.o["nativeReferrers"] = Array();
  std::set<std::pair<int, std::uint64_t>> seen;
  const auto direct = ops::FindReferrers(p, std::set<EID>{id}, true);
  std::size_t nDirect = 0;
  auto add = [&](const ops::Referrer &r, const char *relation) {
    auto row = Object();
    constexpr const char *names[] = {"ScriptInstance", "Reference",
                                     "ArrayInfo",      "ActiveScript",
                                     "SuspendedStack", "FunctionMessage"};
    row.o["type"] = S(names[int(r.kind)]);
    row.o["id"] = S(Hex(r.id));
    row.o["desc"] = S(text::ToUtf8(r.where));
    row.o["relation"] = S(relation);
    j.o["nativeReferrers"].a.push_back(row);
    // FallrimTools findReferees returns DefinedElements. Arrays remain graph
    // intermediates; threads/messages remain separately visible native
    // evidence.
    if (r.kind != ops::Referrer::Kind::kInstance &&
        r.kind != ops::Referrer::Kind::kReference)
      return;
    auto key = std::pair{int(r.kind), r.id};
    if (!seen.insert(key).second)
      return;
    j.o["referees"].a.push_back(row);
  };
  for (const auto &r : direct)
    add(r, "direct");
  nDirect = seen.size();
  std::set<EID> intermediates;
  for (const auto &r : direct)
    if (r.kind == ops::Referrer::Kind::kInstance ||
        r.kind == ops::Referrer::Kind::kReference ||
        r.kind == ops::Referrer::Kind::kArray)
      intermediates.insert(r.id);
  for (const auto &secondary : ops::FindReferrers(p, intermediates, true))
    add(secondary, "secondary");
  j.o["refereeCount"] = N(seen.size());
  j.o["directCount"] = N(nDirect);
  j.o["secondaryCount"] = N(seen.size() - nDirect);
  return j;
}
J GlobalData(SaveFile &s, SaveFile *second) {
  auto j = Object();
  j.o["reflectionOk"] = B(true);
  j.o[second ? "changed" : "blocks"] = Array();
  auto &rows = j.o[second ? "changed" : "blocks"].a;
  const auto a = Globals(s);
  const auto b =
      second ? Globals(*second) : std::vector<const ess::GlobalData *>{};
  for (std::size_t i = 0; i < std::max(a.size(), b.size()); ++i) {
    auto x = Object();
    const auto *ga = i < a.size() ? a[i] : nullptr;
    const auto *gb = i < b.size() ? b[i] : nullptr;
    auto ba = ga ? Bytes(*ga) : std::vector<std::uint8_t>{};
    auto bb = gb ? Bytes(*gb) : std::vector<std::uint8_t>{};
    if (second && ga && gb && ga->type == gb->type && ba == bb)
      continue;
    x.o["index"] = N(i);
    x.o["type"] = N(ga ? ga->type : gb->type);
    if (second) {
      x.o["inA"] = B(ga);
      x.o["inB"] = B(gb);
      x.o["sizeA"] = N(ga ? double(ba.size()) : -1);
      x.o["sizeB"] = N(gb ? double(bb.size()) : -1);
      std::size_t at = 0;
      while (at < std::min(ba.size(), bb.size()) && ba[at] == bb[at])
        ++at;
      x.o["firstDivergeOffset"] = N(at);
      x.o["windowA"] = S(HexBytes(ba, at, 48));
      x.o["windowB"] = S(HexBytes(bb, at, 48));
    } else {
      x.o["size"] = N(ba.size());
      x.o["sha"] = J{};
      // Binary data is supplied by the native model; an adapter may hash/encode
      // this without reparsing the save. Papyrus remains size-only in this
      // view.
      if (ga && !ga->papyrus)
        x.o["rawBase64"] = S(Base64(ba));
    }
    rows.push_back(x);
  }
  j.o[second ? "changedCount" : "count"] = N(rows.size());
  return j;
}
J Freeze(SaveFile &s) {
  auto j = Object();
  auto &p = *s.papyrus();
  auto info = Info(s);
  auto r = ops::Analyze(s);
  std::map<std::string, std::size_t> active, suspended;
  for (const auto &t : p.activeScripts)
    if (t.data && !t.data->frames.empty())
      active[p.str(t.data->frames[0].scriptName)]++;
  for (auto *list : {&p.suspended1, &p.suspended2})
    for (const auto &ss : *list)
      if (ss.data)
        suspended[p.str(ss.data->scriptName)]++;
  const auto susp = p.suspended1.size() + p.suspended2.size();
  const bool high = r.undefinedThreads > 0 || susp > 50,
             moderate = susp > 0 || info.o["undefinedElements"].n > 20 ||
                        p.functionMessages.size() > 20 || r.unattached ||
                        r.terminatedThreads;
  j.o["riskLevel"] = S(high ? "HIGH" : moderate ? "MODERATE" : "LOW");
  j.o["counts"] = info;
  j.o["counts"].o["terminated"] = N(r.terminatedThreads);
  j.o["counts"].o["undefinedActiveScripts"] = N(r.undefinedThreads);
  j.o["counts"].o["unattached"] = N(r.unattached);
  j.o["signals"] = Array();
  j.o["activeByScript"] = Object();
  j.o["suspendedByScript"] = Object();
  for (const auto &[name, count] : active)
    j.o["activeByScript"].o[text::ToUtf8(name)] = N(count);
  for (const auto &[name, count] : suspended)
    j.o["suspendedByScript"].o[text::ToUtf8(name)] = N(count);
  for (auto pair : {std::pair{"topActiveByScript", active},
                    std::pair{"topSuspendedByScript", suspended}}) {
    j.o[pair.first] = Array();
    std::vector<std::pair<std::string, std::size_t>> sorted(pair.second.begin(),
                                                            pair.second.end());
    std::sort(sorted.begin(), sorted.end(), [](auto &a, auto &b) {
      return a.second != b.second ? a.second > b.second : a.first < b.first;
    });
    for (std::size_t i = 0; i < std::min<std::size_t>(12, sorted.size()); ++i) {
      auto row = Object();
      row.o["script"] = S(text::ToUtf8(sorted[i].first));
      row.o["count"] = N(sorted[i].second);
      j.o[pair.first].a.push_back(row);
    }
  }
  j.o["note"] =
      S("heuristic; a save inspection does not establish live game behavior");
  return j;
}
} // namespace
std::string Arguments::option(const std::string &flag,
                              const std::string &fallback) const {
  auto i = options.find(flag);
  return i == options.end() ? fallback : i->second;
}
Arguments Parse(const std::string &op, const std::vector<std::string> &args) {
  static const std::map<std::string, std::set<std::string>> allowed = {
      {"info", {}},
      {"dump", {"--limit", "--undefined-only", "--script", "--type"}},
      {"find", {}},
      {"find-refs", {}},
      {"worries", {}},
      {"changeform", {"--raw", "--depth"}},
      {"recon", {}},
      {"extradata-scan", {"--limit", "--type"}},
      {"changeform-diff", {"--quests"}},
      {"globaldata", {}},
      {"globaldata-diff", {}},
      {"freeze-report", {}},
      {"writer-scan", {}},
      {"verify-roundtrip", {}},
      {"set-var", {"--apply", "--type"}},
      {"set-global", {"--apply"}},
      {"clean",
       {"--apply", "--undefined", "--unattached", "--terminate-threads"}},
      {"reset-havok", {"--apply"}},
      {"cleanse-formlists", {"--apply"}},
      {"remove-created", {"--apply"}}};
  const auto found = allowed.find(op);
  if (found == allowed.end())
    throw std::runtime_error("unsupported structured operation: " + op);
  Arguments a;
  const std::set<std::string> valued = {"--limit", "--script", "--type",
                                        "--depth"};
  for (std::size_t i = 0; i < args.size(); ++i) {
    const auto &v = args[i];
    if (v.rfind("--", 0) == 0) {
      if (!found->second.contains(v) || a.options.contains(v))
        throw std::runtime_error("unknown or repeated option: " + v);
      if (valued.contains(v)) {
        if (++i >= args.size())
          throw std::runtime_error("missing option value");
        a.options[v] = args[i];
      } else
        a.options[v] = "true";
    } else
      a.positional.push_back(v);
  }
  if (IsEdit(op)) {
    const auto n = a.positional.size();
    const bool variable = op == "set-var";
    if (variable && n != 1 && n != 3 && n != 4)
      throw std::runtime_error("set-var needs id or id/index/value/[output]");
    if (op == "set-global" && n != 2 && n != 3)
      throw std::runtime_error("set-global needs target/value/[output]");
    if (!variable && op != "set-global" && n > 1)
      throw std::runtime_error("at most one output permitted");
    const auto required = variable ? 4u : op == "set-global" ? 3u : 1u;
    if (a.has("--apply") && n != required)
      throw std::runtime_error("apply requires one explicit new output");
    return a;
  }
  const bool needs = op == "find" || op == "find-refs" ||
                     op == "globaldata-diff" || op == "changeform-diff";
  const auto max =
      op == "changeform-diff"
          ? 2u
          : (needs || op == "dump" || op == "changeform" ? 1u : 0u);
  if (a.positional.size() > (max) || (needs && a.positional.empty()))
    throw std::runtime_error("wrong positional argument count");
  if (a.has("--limit"))
    Limit(a);
  return a;
}
json::Value Query(const std::string &op, SaveFile &s, const Arguments &a,
                  SaveFile *second) {
  if (!s.papyrus() && op != "worries")
    throw std::runtime_error("save has no Papyrus section");
  J j;
  if (op == "info")
    j = Info(s);
  else if (op == "dump")
    j = Dump(s, a);
  else if (op == "find")
    j = Find(s, a);
  else if (op == "find-refs")
    j = Referrers(*s.papyrus(), a);
  else if (op == "changeform")
    j = Change(s, a);
  else if (op == "recon" || op == "extradata-scan")
    j = Recon(s, a, op == "extradata-scan");
  else if (op == "writer-scan")
    j = Writers(*s.papyrus());
  else if (op == "verify-roundtrip") {
    auto reopened = SaveFile::parse(s.serialize(), s.cosave, s.hasCosave);
    const auto result = verify::Expected(s, *reopened);
    j = Object();
    j.o["identical"] = B(result.identical());
    if (!result.identical())
      throw std::runtime_error(verify::Describe(result));
    j.o["changeForms"] = N(s.changeForms.size());
    j.o["method"] = S("native serialization and same-parser reopen in memory; "
                      "no temporary save deletion");
  } else if (op == "changeform-diff") {
    if (!second)
      throw std::runtime_error("second save required");
    j = ChangeDifferences(s, *second, a);
  } else if (op == "globaldata" || op == "globaldata-diff") {
    if (op == "globaldata-diff" && !second)
      throw std::runtime_error("second save required");
    j = GlobalData(s, second);
  } else if (op == "freeze-report")
    j = Freeze(s);
  else if (op == "worries") {
    auto r = ops::Analyze(s);
    j = Object();
    bool blocks = !s.problems.empty();
    j.o["findings"] = Array();
    auto finding = [&](const std::string& code, const std::string& message,
                       std::size_t count, const std::vector<std::string>& samples, bool blocked) {
      auto item = Object();
      item.o["code"] = S(code);
      item.o["message"] = S(text::ToUtf8(message));
      item.o["count"] = N(count);
      item.o["severity"] = S(blocked ? "error" : "advisory");
      item.o["blocksSaving"] = B(blocked);
      item.o["samples"] = Array();
      for (std::size_t i = 0; i < samples.size() && i < 8; ++i)
        item.o["samples"].a.push_back(S(text::ToUtf8(samples[i])));
      item.o["samplesTruncated"] = B(count > item.o["samples"].a.size());
      j.o["findings"].a.push_back(std::move(item));
      blocks = blocks || blocked;
    };
    for (const auto& f : r.findings)
      finding(f.code, f.message, f.count, f.samples, f.blocksSaving);
    if (!s.problems.empty())
      finding("save.structural-problem", "Save reader reported structural problems.",
              s.problems.size(), s.problems, true);
    j.o["analysisContractVersion"] = N(2);
    j.o["diagnosticsAvailable"] = B(s.papyrus() != nullptr);
    j.o["shouldDisableSaving"] = B(blocks);
    j.o["shouldWorry"] = B(blocks || !r.warnings.empty());
    j.o["totalFrames"] = N(r.frames);
    j.o["maxStackDepth"] = N(r.maxStackDepth);
    j.o["framesByScript"] = Array();
    for (std::size_t i = 0; i < r.framesByScript.size() && i < 20; ++i) {
      auto item = Object();
      item.o["script"] = S(text::ToUtf8(r.framesByScript[i].name));
      item.o["count"] = N(r.framesByScript[i].n);
      j.o["framesByScript"].a.push_back(std::move(item));
    }
    j.o["reportText"] = S(ops::FormatReport(r));
    j.o["reportHtml"] = J{};
    j.o["warnings"] = Array();
    for (const auto &w : s.problems)
      j.o["warnings"].a.push_back(S(w));
    for (const auto &w : r.warnings)
      j.o["warnings"].a.push_back(S(w));
  } else
    throw std::runtime_error("structured query not yet qualified: " + op);
  j.o["ok"] = B(true);
  j.o["op"] = S(op);
  j.o["schemaVersion"] = N(1);
  return j;
}
std::string Serialize(const json::Value &j) {
  switch (j.type) {
  case J::Type::kNull:
    return "null";
  case J::Type::kBool:
    return j.b ? "true" : "false";
  case J::Type::kNumber: {
    if (!std::isfinite(j.n))
      throw std::runtime_error("non-finite JSON number");
    std::ostringstream o;
    o << std::setprecision(17) << j.n;
    return o.str();
  }
  case J::Type::kString: {
    std::string out = "\"";
    for (unsigned char c : j.s) {
      if (c == '"')
        out += "\\\"";
      else if (c == '\\')
        out += "\\\\";
      else if (c < 32)
        out += "\\u00" + Hex(c, 2);
      else
        out += char(c);
    }
    return out + '"';
  }
  case J::Type::kArray: {
    std::string out = "[";
    for (const auto &v : j.a) {
      if (out.size() > 1)
        out += ',';
      out += Serialize(v);
    }
    return out + ']';
  }
  case J::Type::kObject: {
    std::string out = "{";
    for (const auto &[key, v] : j.o) {
      if (out.size() > 1)
        out += ',';
      out += Serialize(S(key)) + ':' + Serialize(v);
    }
    return out + '}';
  }
  }
  throw std::runtime_error("unknown JSON value");
}
int Main(int argc, char **argv) {
  try {
    if (argc < 4)
      throw std::runtime_error(
          "usage: resaverpp-cli --json <operation> <save.ess> [arguments]");
    std::string op = argv[2];
    std::vector<std::string> args;
    for (int i = 4; i < argc; ++i)
      args.push_back(argv[i]);
    const auto a = Parse(op, args);
    auto s = SaveFile::load(std::filesystem::u8path(argv[3]));
    std::unique_ptr<SaveFile> second;
    if (op == "globaldata-diff" || op == "changeform-diff")
      second = SaveFile::load(std::filesystem::u8path(a.positional[0]));
    auto result = IsEdit(op) ? Edit(op, *s, a) : Query(op, *s, a, second.get());
    if (IsEdit(op) && a.has("--apply")) {
      const auto output = std::filesystem::u8path(result.str("out"));
      if (Low(output.extension().string()) != ".ess")
        throw std::runtime_error("output must name a new ESS pair");
      s->write(output, false);
      auto reopened = SaveFile::load(output);
      const auto check = verify::Expected(*s, *reopened);
      if (!check.identical())
        throw std::runtime_error(verify::Describe(check));
      result.o["verified"] = B(true);
      result.o["wroteFile"] = B(true);
    }
    std::puts(Serialize(result).c_str());
    return 0;
  } catch (const std::exception &e) {
    auto j = Object();
    j.o["ok"] = B(false);
    j.o["error"] = S(e.what());
    std::puts(Serialize(j).c_str());
    return 2;
  }
}
} // namespace ess::structured
