// Exact raw/structured changeform comparisons on the native decoded trees.
#include "ChangeFormData.h"
#include "Structured.h"
#include "Text.h"
#include <algorithm>
#include <cctype>
#include <iomanip>
#include <set>
#include <sstream>

namespace ess::structured {
namespace {
using J = json::Value;
J Obj() {
  J j;
  j.type = J::Type::kObject;
  return j;
}
J Arr() {
  J j;
  j.type = J::Type::kArray;
  return j;
}
J S(std::string s) {
  J j;
  j.type = J::Type::kString;
  j.s = text::ToUtf8(s);
  return j;
}
J N(double n) {
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
std::string Hex(std::uint64_t n, int width = 6) {
  std::ostringstream o;
  o << std::uppercase << std::hex << std::setw(width) << std::setfill('0') << n;
  return o.str();
}
std::string Ref(const SaveFile &s, RefID id) {
  auto form = s.formIDOf(id);
  const auto plugin = id.isZero() || id.type() == RefID::Type::kInvalid || !form
                          ? "?"
                          : s.pluginOf(form);
  return plugin + ":" + Hex(form & 0xFFFFFF);
}
std::string Window(const std::vector<std::uint8_t> &b, std::size_t at,
                   std::size_t count = 48) {
  std::string out;
  for (std::size_t i = at; i < b.size() && i - at < count; ++i) {
    if (!out.empty())
      out += ' ';
    out += Hex(b[i], 2);
  }
  return out;
}
bool Noise(std::string p) {
  for (auto &c : p)
    c = char(std::tolower(static_cast<unsigned char>(c)));
  for (const auto *part : {"havok", "not decoded", "unknown_bytes",
                           "/initial/pos", "/initial/rot", "/scale"})
    if (p.find(part) != std::string::npos)
      return true;
  return false;
}
struct Field {
  std::string display;
  std::vector<std::uint8_t> raw;
};
void Flatten(const cf::Node &n, const std::vector<std::uint8_t> &b,
             const std::string &prefix, std::map<std::string, Field> &out) {
  const auto path = prefix + "/" + n.label;
  if (n.kids.empty()) {
    Field f;
    f.display = n.isRef ? Hex(n.refRaw) : n.value;
    if (n.offset <= b.size() && n.size <= b.size() - n.offset)
      f.raw = {b.begin() + n.offset, b.begin() + n.offset + n.size};
    out[path] = std::move(f);
    return;
  }
  for (const auto &k : n.kids)
    Flatten(k, b, path, out);
}
std::map<std::string, std::int64_t> Inventory(const SaveFile &s,
                                              const cf::Decoded &d) {
  std::map<std::string, std::int64_t> out;
  for (const auto &top : d.root.kids)
    if (top.label.rfind("INVENTORY", 0) == 0)
      for (const auto &item : top.kids) {
        const cf::Node *id = nullptr;
        const cf::Node *count = nullptr;
        for (const auto &f : item.kids) {
          if (f.label == "ITEM" && f.isRef)
            id = &f;
          if (f.label == "COUNT")
            count = &f;
        }
        if (id && count)
          out[Ref(s, RefID{id->refRaw})] += std::stoll(count->value);
      }
  return out;
}
J InventoryDelta(const std::map<std::string, std::int64_t> &a,
                 const std::map<std::string, std::int64_t> &b) {
  auto j = Obj();
  for (auto key : {"added", "removed", "countChanged"})
    j.o[key] = Arr();
  std::set<std::string> all;
  for (const auto &x : a)
    all.insert(x.first);
  for (const auto &x : b)
    all.insert(x.first);
  for (const auto &key : all) {
    auto x = a.find(key), y = b.find(key);
    if (x != a.end() && y != b.end() && x->second == y->second)
      continue;
    auto row = Obj();
    row.o["item"] = S(key);
    if (x == a.end()) {
      row.o["count"] = N(y->second);
      j.o["added"].a.push_back(row);
    } else if (y == b.end()) {
      row.o["count"] = N(x->second);
      j.o["removed"].a.push_back(row);
    } else {
      row.o["a"] = N(x->second);
      row.o["b"] = N(y->second);
      j.o["countChanged"].a.push_back(row);
    }
  }
  j.o["itemsA"] = N(a.size());
  j.o["itemsB"] = N(b.size());
  return j;
}
J One(const SaveFile &a, const ChangeForm &fa, const SaveFile &b,
      const ChangeForm &fb) {
  const auto x = cf::Body(fa), y = cf::Body(fb);
  const auto da = cf::Decode(a, fa), db = cf::Decode(b, fb);
  auto j = Obj();
  j.o["refid"] = S(Ref(a, fa.refID));
  j.o["type"] = S(ChangeFormTypeName(fa.typeCode()));
  j.o["lenA"] = N(x.size());
  j.o["lenB"] = N(y.size());
  j.o["rawIdentical"] = B(x == y);
  j.o["flagsA"] = S(Hex(fa.changeFlags, 8));
  j.o["flagsB"] = S(Hex(fb.changeFlags, 8));
  j.o["completeA"] = B(da.complete);
  j.o["completeB"] = B(db.complete);
  std::size_t at = 0;
  while (at < std::min(x.size(), y.size()) && x[at] == y[at])
    ++at;
  if (x != y) {
    std::size_t suffix = 0;
    while (suffix < std::min(x.size(), y.size()) - at &&
           x[x.size() - 1 - suffix] == y[y.size() - 1 - suffix])
      ++suffix;
    j.o["firstDivergeOffset"] = N(at);
    j.o["commonSuffix"] = N(suffix);
    j.o["windowA"] = S(Window(x, at));
    j.o["windowB"] = S(Window(y, at));
    j.o["refIdCandidates"] = Arr();
    for (auto off = at;
         off < y.size() && off - at < 24 && j.o["refIdCandidates"].a.size() < 8;
         ++off) {
      if (y.size() - off < 3)
        break;
      Reader r(y.data() + off, y.size() - off);
      auto id = RefID::read(r);
      auto full = b.formIDOf(id);
      if (!full || b.pluginOf(full) == "?" ||
          id.type() == RefID::Type::kCreated ||
          id.type() == RefID::Type::kInvalid)
        continue;
      auto row = Obj();
      row.o["offset"] = N(off);
      row.o["ref"] = S(Ref(b, id));
      row.o["heuristic"] = B(true);
      j.o["refIdCandidates"].a.push_back(row);
    }
  }
  std::map<std::string, Field> ma, mb;
  for (const auto &n : da.root.kids)
    Flatten(n, x, "", ma);
  for (const auto &n : db.root.kids)
    Flatten(n, y, "", mb);
  std::set<std::string> keys;
  for (const auto &v : ma)
    keys.insert(v.first);
  for (const auto &v : mb)
    keys.insert(v.first);
  auto changes = Obj();
  changes.o["changed"] = Arr();
  std::size_t total = 0, signal = 0;
  for (const auto &key : keys) {
    auto va = ma.find(key), vb = mb.find(key);
    if (va != ma.end() && vb != mb.end() && va->second.raw == vb->second.raw &&
        va->second.display == vb->second.display)
      continue;
    ++total;
    const bool noise = Noise(key);
    signal += !noise;
    if (changes.o["changed"].a.size() < 80) {
      auto row = Obj();
      row.o["path"] = S(key);
      row.o["noise"] = B(noise);
      row.o["rawChanged"] = B(va == ma.end() || vb == mb.end() ||
                              va->second.raw != vb->second.raw);
      row.o["a"] = va == ma.end() ? J{} : S(va->second.display.substr(0, 180));
      row.o["b"] = vb == mb.end() ? J{} : S(vb->second.display.substr(0, 180));
      changes.o["changed"].a.push_back(row);
    }
  }
  changes.o["changedCount"] = N(total);
  changes.o["signalCount"] = N(signal);
  changes.o["truncated"] = B(total > 80);
  j.o["structured"] = changes;
  j.o["inventoryDiff"] = InventoryDelta(Inventory(a, da), Inventory(b, db));
  return j;
}
const ChangeForm *Pick(const SaveFile &s, const std::string &q) {
  std::size_t used = 0;
  const auto id = std::stoull(q, &used, 16);
  if (used != q.size() || id > 0xFFFFFFFFu)
    throw std::runtime_error("invalid form id");
  const ChangeForm *found = nullptr;
  for (const auto &f : s.changeForms)
    if ((s.formIDOf(f.refID) & 0xFFFFFF) == (id & 0xFFFFFF)) {
      if (!found)
        found = &f;
      if (f.typeCode() == static_cast<unsigned>(ChangeFormType::ACHR))
        return &f;
    }
  return found;
}
} // namespace
json::Value ChangeDifferences(const SaveFile &before, const SaveFile &after,
                              const Arguments &args) {
  auto j = Obj();
  if (args.has("--quests")) {
    j.o["mode"] = S("quests");
    j.o["changed"] = Arr();
    std::map<std::uint32_t, const ChangeForm *> quests;
    for (const auto &f : after.changeForms) {
      if (f.typeCode() == static_cast<unsigned>(ChangeFormType::QUST) &&
          !quests.emplace(f.refID.data, &f).second)
        throw std::runtime_error("ambiguous duplicate quest changeform");
    }
    for (const auto &f : before.changeForms)
      if (f.typeCode() == static_cast<unsigned>(ChangeFormType::QUST)) {
        const auto found = quests.find(f.refID.data);
        const auto *match = found == quests.end() ? nullptr : found->second;
        if (match && (f.changeFlags != match->changeFlags ||
                      cf::Body(f) != cf::Body(*match)))
          j.o["changed"].a.push_back(One(before, f, after, *match));
      }
    j.o["changedCount"] = N(j.o["changed"].a.size());
  } else {
    const auto id =
        args.positional.size() > 1 ? args.positional[1] : "00000014";
    auto *a = Pick(before, id);
    auto *b = Pick(after, id);
    j.o["mode"] = S("single");
    j.o["refid"] = S(id);
    j.o["found"] = B(a && b);
    if (a && b)
      j.o["diff"] = One(before, *a, after, *b);
  }
  return j;
}
} // namespace ess::structured
