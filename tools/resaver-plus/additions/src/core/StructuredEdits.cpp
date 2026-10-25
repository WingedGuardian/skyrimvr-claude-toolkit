// Controlled native edits and deterministic receipts; no filesystem access
// here.
#include "ChangeFormData.h"
#include "Ops.h"
#include "Structured.h"
#include "Verify.h"
#include "Text.h"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <limits>
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
  if (!std::isfinite(n))
    return j; // preserve non-finite old values as explicit null + raw bits
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
  std::ostringstream o;
  o << std::hex << std::uppercase << std::setfill('0') << std::setw(width) << n;
  return o.str();
}
std::uint64_t Number(const std::string &s, int base) {
  if (s.empty() || s[0] == '-')
    throw std::runtime_error("invalid unsigned integer");
  std::size_t n = 0;
  auto v = std::stoull(s, &n, base);
  if (n != s.size())
    throw std::runtime_error("invalid integer");
  return v;
}
float Float(const std::string &s) {
  std::size_t n = 0;
  auto v = std::stof(s, &n);
  if (n != s.size() || !std::isfinite(v))
    throw std::runtime_error("expected a finite float");
  return v;
}
std::string Type(VType t) {
  switch (t) {
  case VType::kBool:
    return "BOOLEAN";
  case VType::kInt:
    return "INTEGER";
  case VType::kFloat:
    return "FLOAT";
  case VType::kString:
    return "STRING";
  case VType::kRef:
    return "REF";
  default:
    return VTypeName(t);
  }
}
std::string Value(const Papyrus &p, const Variable &v) {
  auto t = Type(v.type);
  if (v.type == VType::kBool)
    return t + ":" + (v.value ? "true" : "false");
  if (v.type == VType::kInt)
    return t + ":" + std::to_string(static_cast<std::int32_t>(v.value));
  if (v.type == VType::kFloat) {
    std::ostringstream o;
    o << std::setprecision(9) << v.asFloat();
    return t + ":" + o.str();
  }
  if (v.type == VType::kString)
    return t + ":\"" + p.str(static_cast<TStr>(v.value)) + "\"";
  return t + ":" + Hex(v.value);
}
struct Target {
  std::vector<Variable> *vars = nullptr;
  TStr script = 0;
  std::string type;
};
Target Variables(Papyrus &p, EID id) {
  Target out;
  std::size_t found = 0;
  for (auto &i : p.instances)
    if (i.id == id) {
      ++found;
      out = {i.data ? &i.data->vars : nullptr, i.script, "ScriptInstance"};
    }
  for (auto &r : p.references)
    if (r.id == id) {
      ++found;
      out = {r.data ? &r.data->vars : nullptr, r.script, "Reference"};
    }
  for (auto &a : p.arrays)
    if (a.id == id) {
      ++found;
      out = {a.hasData ? &a.vars : nullptr, 0, "ArrayInfo"};
    }
  if (found != 1 || !out.vars)
    throw std::runtime_error(found > 1 ? "ambiguous element id"
                                       : "target has no available variables");
  return out;
}
Variable Build(Papyrus &p, const Variable &old, const std::string &value,
               const Arguments &args) {
  auto type = args.option("--type");
  for (auto &c : type)
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  VType t = old.type;
  if (!type.empty()) {
    if (type == "bool")
      t = VType::kBool;
    else if (type == "int")
      t = VType::kInt;
    else if (type == "float")
      t = VType::kFloat;
    else if (type == "str")
      t = VType::kString;
    else
      throw std::runtime_error("unsupported primitive variable type");
  }
  Variable v;
  v.type = t;
  if (t == VType::kInt) {
    std::size_t n = 0;
    auto i = std::stoll(value, &n, 0);
    if (n != value.size() || i < std::numeric_limits<std::int32_t>::min() ||
        i > std::numeric_limits<std::int32_t>::max())
      throw std::runtime_error("integer outside signed 32-bit range");
    v.value = static_cast<std::uint32_t>(i);
  } else if (t == VType::kBool) {
    auto normalized = value;
    for (auto &c : normalized)
      c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (normalized != "true" && normalized != "false" && normalized != "0" &&
        normalized != "1")
      throw std::runtime_error("bool must be true, false, 0 or 1");
    v.value = normalized == "true" || normalized == "1";
  } else if (t == VType::kFloat) {
    auto f = Float(value);
    std::uint32_t bits;
    std::memcpy(&bits, &f, 4);
    v.value = bits;
  } else if (t == VType::kString) {
    // New strings use UTF-8. Existing strings retain their native encoding;
    // reject lossy conversion instead of silently substituting characters.
    const auto encoded = old.type == VType::kString
        ? text::FromUtf8(value, p.str(static_cast<TStr>(old.value))) : value;
    if (text::ToUtf8(encoded) != value)
      throw std::runtime_error("string cannot be represented in the original encoding");
    if (encoded.size() > 65535)
      throw std::runtime_error("string exceeds native 16-bit byte length");
    v.value = p.internString(encoded);
  } else
    throw std::runtime_error("only int/float/bool/string edits are supported");
  return v;
}
struct GlobalTarget {
  GlobalData *block = nullptr;
  std::size_t offset = 0;
  float value = 0;
  RefID id;
};
GlobalTarget Global(SaveFile &s, const std::string &target) {
  const auto colon = target.rfind(':');
  const auto plugin =
      colon == std::string::npos ? std::string() : target.substr(0, colon);
  const auto fid = Number(
      colon == std::string::npos ? target : target.substr(colon + 1), 16);
  if (fid > 0xFFFFFFFFu)
    throw std::runtime_error("form id exceeds 32 bits");
  GlobalTarget found;
  std::size_t matches = 0;
  for (auto *table : {&s.table1, &s.table2, &s.table3})
    for (auto &g : *table)
      if (g.type == 3) {
        Reader r(g.raw.data(), g.raw.size());
        auto first = r.u8();
        std::uint32_t n = first;
        if ((first & 3) != 0) {
          n |= std::uint32_t(r.u8()) << 8;
          if ((first & 3) != 1)
            n |= std::uint32_t(r.u8()) << 16;
        }
        n >>= 2;
        for (std::uint32_t k = 0; k < n; ++k) {
          auto id = RefID::read(r);
          auto at = r.pos();
          auto value = r.f32();
          auto full = s.formIDOf(id);
          if (!id.isZero() && id.type() != RefID::Type::kInvalid && full &&
              (full & 0xFFFFFF) == (fid & 0xFFFFFF) &&
              (plugin.empty() || EqualsNoCase(s.pluginOf(full), plugin))) {
            ++matches;
            found = {&g, at, value, id};
          }
        }
        if (r.remaining())
          throw std::runtime_error("global table has an unparsed tail");
      }
  if (matches != 1)
    throw std::runtime_error(matches ? "ambiguous global target"
                                     : "global target not found");
  return found;
}
void Check(const SaveFile &expected, const SaveFile &actual) {
  auto r = verify::Expected(expected, actual);
  if (!r.identical())
    throw std::runtime_error("unexpected edit delta: " + verify::Describe(r));
}
void ExpectedClean(Papyrus &p, const Arguments &args, J &report) {
  if (args.has("--unattached")) {
    std::set<EID> selected;
    for (const auto &i : p.instances)
      if (ops::IsUnattached(i))
        selected.insert(i.id);
    ops::DeleteInstances(p, selected);
  }
  if (args.has("--undefined") || args.has("--terminate-threads")) {
    std::set<ThreadID> threads;
    for (const auto &t : p.activeScripts)
      if (ops::IsThreadUndefined(p, t) && !ops::IsThreadTerminated(p, t))
        threads.insert(t.id);
    ops::TerminateThreads(p, threads);
    for (auto id : threads)
      report.o["targets"].a.push_back(S("thread:" + Hex(id, 8)));
  }
  if (args.has("--undefined")) {
    std::set<TStr> scripts;
    std::set<EID> instances, references;
    for (const auto &sc : p.scripts)
      if (ops::IsScriptUndefined(p, sc))
        scripts.insert(sc.name);
    for (const auto &i : p.instances)
      if (ops::IsInstanceUndefined(p, i.script))
        instances.insert(i.id);
    for (const auto &r : p.references)
      if (ops::IsInstanceUndefined(p, r.script))
        references.insert(r.id);
    for (auto name : scripts)
      report.o["targets"].a.push_back(S("script:" + p.str(name)));
    for (auto id : references)
      report.o["targets"].a.push_back(S("reference:" + Hex(id)));
    ops::DeleteInstances(p, instances);
    ops::DeleteReferences(p, references);
    ops::DeleteScripts(p, scripts);
  }
}
void ExpectedForms(SaveFile &before, const std::string &operation, J &report) {
  for (auto &f : before.changeForms) {
    if (operation == "reset-havok") {
      if (f.typeCode() != static_cast<unsigned>(ChangeFormType::REFR) ||
          !(f.changeFlags & (1u << 2)))
        continue;
      const auto d = cf::Decode(before, f);
      if (!d.complete || !d.hasHavok)
        continue;
      auto body = cf::Body(f);
      std::fill(body.begin() + d.havokOffset,
                body.begin() + d.havokOffset + d.havokSize, 0);
      cf::SetBody(f, body);
      report.o["targets"].a.push_back(S("changeform:" + Hex(f.refID.data, 6)));
    } else {
      if (f.typeCode() != static_cast<unsigned>(ChangeFormType::FLST))
        continue;
      const auto d = cf::Decode(before, f);
      if (!d.complete)
        continue;
      const auto zeros = std::count(d.listRaw.begin(), d.listRaw.end(), 0u);
      if (!zeros)
        continue;
      const auto body = cf::Body(f);
      const auto prefix = body.size() - 4 - 3 * d.listRaw.size();
      Writer expected;
      expected.raw(body.data(), prefix);
      expected.u32(static_cast<std::uint32_t>(d.listRaw.size() - zeros));
      for (auto id : d.listRaw)
        if (id)
          RefID{id}.write(expected);
      cf::SetBody(f, expected.buf);
      report.o["targets"].a.push_back(S("changeform:" + Hex(f.refID.data, 6)));
    }
  }
}
} // namespace
bool IsEdit(const std::string &op) {
  return op == "set-var" || op == "set-global" || op == "clean" ||
         op == "reset-havok" || op == "cleanse-formlists" ||
         op == "remove-created";
}
json::Value Edit(const std::string &op, SaveFile &s, const Arguments &args) {
  if (!s.papyrus() || !s.problems.empty())
    throw std::runtime_error("edit requires a structurally sound Papyrus save");
  auto report = Obj();
  report.o["ok"] = B(true);
  report.o["op"] = S(op);
  report.o["apply"] = B(args.has("--apply"));
  report.o["dryRun"] = B(!args.has("--apply"));
  report.o["wroteFile"] = B(false);
  report.o["targets"] = Arr();
  const auto &pos = args.positional;
  auto &p = *s.papyrus();
  if (op == "set-var" && pos.size() == 1) {
    auto target = Variables(p, Number(pos[0], 16));
    report.o["found"] = B(true);
    report.o["target"] = S(pos[0]);
    report.o["preview"] = B(true);
    report.o["targetType"] = S(target.type);
    report.o["variables"] = Arr();
    auto names = ops::ExtendedMembers(p, target.script);
    for (std::size_t i = 0; i < target.vars->size(); ++i) {
      const auto &v = target.vars->at(i);
      auto row = Obj();
      row.o["index"] = N(i);
      row.o["name"] = i < names.size() ? S(names[i]) : J{};
      row.o["type"] = S(Type(v.type));
      row.o["value"] = S(Value(p, v));
      report.o["variables"].a.push_back(row);
    }
    return report;
  }
  // Reuse the native parser for a private in-memory before-model. Nothing is
  // written by this layer; the CLI writes only with --apply and a new output.
  auto before = SaveFile::parse(s.serialize(), s.cosave, s.hasCosave);
  Check(s, *before);
  std::vector<EID> originalIDs;
  for (const auto &i : p.instances)
    originalIDs.push_back(i.id);
  if (op == "set-var") {
    const auto id = Number(pos[0], 16), index = Number(pos[1], 10);
    auto target = Variables(p, id);
    auto expectedTarget = Variables(*before->papyrus(), id);
    if (index >= target.vars->size())
      throw std::runtime_error("variable index out of range");
    report.o["target"] = S(pos[0]);
    report.o["found"] = B(true);
    report.o["targetType"] = S(target.type);
    report.o["index"] = N(index);
    report.o["oldValue"] = S(Value(p, target.vars->at(index)));
    const auto next = Build(p, target.vars->at(index), pos[2], args);
    const auto expected =
        Build(*before->papyrus(), expectedTarget.vars->at(index), pos[2], args);
    target.vars->at(index) = next;
    expectedTarget.vars->at(index) = expected;
    Check(*before, s);
    report.o["newValue"] = S(Value(p, next));
    report.o["targets"].a.push_back(
        S(Hex(id) + "/vars/" + std::to_string(index)));
  } else if (op == "set-global") {
    auto target = Global(s, pos[0]), expected = Global(*before, pos[0]);
    auto value = Float(pos[1]);
    std::memcpy(target.block->raw.data() + target.offset, &value, 4);
    std::memcpy(expected.block->raw.data() + expected.offset, &value, 4);
    Check(*before, s);
    report.o["target"] = S(pos[0]);
    report.o["found"] = B(true);
    report.o["oldValue"] = N(target.value);
    std::uint32_t oldBits;
    std::memcpy(&oldBits, &target.value, 4);
    report.o["oldValueBits"] = S(Hex(oldBits, 8));
    report.o["newValue"] = N(value);
    report.o["targets"].a.push_back(S(Hex(s.formIDOf(target.id), 8)));
  } else if (op == "clean") {
    if (!args.has("--undefined") && !args.has("--unattached") &&
        !args.has("--terminate-threads"))
      throw std::runtime_error("clean needs a selected operation");
    ExpectedClean(*before->papyrus(), args, report);
    if (args.has("--unattached"))
      report.o["removedUnattached"] = N(ops::RemoveUnattachedInstances(p));
    if (args.has("--undefined"))
      report.o["removedUndefined"] = N(ops::RemoveUndefinedElements(p));
    if (args.has("--terminate-threads"))
      report.o["terminatedThreads"] = N(ops::TerminateUndefinedThreads(p));
    Check(*before, s);
  } else if (op == "remove-created") {
    std::set<EID> selected;
    for (const auto &i : before->papyrus()->instances)
      if (ops::IsCreatedWithoutForm(*before, i))
        selected.insert(i.id);
    ops::DeleteInstances(*before->papyrus(), selected);
    report.o["removed"] = N(ops::RemoveNonexistentCreated(s));
    Check(*before, s);
  } else if (op == "reset-havok") {
    ExpectedForms(*before, op, report);
    auto result = cf::ResetHavok(s);
    report.o["success"] = N(result.reset);
    report.o["failure"] = N(result.failed);
    Check(*before, s);
  } else if (op == "cleanse-formlists") {
    ExpectedForms(*before, op, report);
    auto result = cf::PurifyFormLists(s);
    report.o["nullEntriesRemoved"] = N(result.entries);
    report.o["formListsAffected"] = N(result.forms);
    report.o["failure"] = N(result.failed);
    Check(*before, s);
  } else
    throw std::runtime_error("unknown controlled edit");
  // Record affected identities, including removals; never infer success from
  // counts alone.
  if (op == "clean" || op == "remove-created")
    for (auto id : originalIDs)
      if (!p.findInstance(id))
        report.o["targets"].a.push_back(S(Hex(id)));
  const auto outputIndex = op == "set-var" ? 3u : op == "set-global" ? 2u : 0u;
  if (pos.size() > outputIndex)
    report.o["out"] = S(pos[outputIndex]);
  report.o["deltaVerified"] = B(true);
  return report;
}
} // namespace ess::structured
