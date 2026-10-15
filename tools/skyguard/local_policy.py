"""Restrictive installed save-policy bridge, evaluated before permanent backup.

Registration binds exact source bytes. Only the legacy guard's pure ``decide``
entry is called; its backup/approval ``main`` never runs. Qualified requirements
may add preservation scope; permission and human approval remain dispatcher-owned.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modding_harness import approval, contracts, profiles, transport
from modding_harness.dispatch import SavePlan
from skyguard import save_profile

CONFIG = ".modding-agent-harness/local-policies.json"
LEGACY = ".claude/hooks/protect-save-access.py"
KIND = "legacy-skyrim-save-guard-v1"
# Only this reviewed legacy version has qualified pure transfer helpers. A
# registration for different source bytes remains a restrictive policy only.
TRANSFER_POLICY_SHA256 = "30777ba84ec4dcf66b6be4c9b0d40ea6eb8e3075aa3561bb0493b8b9bb1351a2"
CONTROLLED_LOAD_SHA256 = "22e0b8b1c004bc4ee79946836a6ce0abb60c4ca9b06ded20b8e659e61dc8102b"


@dataclass(frozen=True)
class DeclaredSavePlan(SavePlan):
    # Only the qualified human reader supplies this field. Action provenance
    # never enters the trusted dispatcher-plan closure.
    declared_members: tuple[str, ...] = ()


def registration(project):
    root = Path(project).resolve()
    path = root / CONFIG
    legacy = root / LEGACY
    if not path.exists():
        if legacy.exists():
            raise ValueError("installed save policy needs a hash-bound local-policies.json registration")
        return None
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("local policy registration exceeds 64 KiB")
    data = transport.load(raw)
    if (type(data.get("v")) is not int or data["v"] != 1 or data.get("kind") != KIND or
            data.get("path") != LEGACY or set(data) != {"v", "kind", "path", "sha256"}):
        raise ValueError("unsupported local policy registration")
    if not isinstance(data["sha256"], str) or len(data["sha256"]) != 64:
        raise ValueError("local policy registration has no SHA-256")
    if legacy.is_symlink() or not legacy.resolve().is_relative_to(root) or not legacy.is_file():
        raise ValueError("registered local policy is not an installed regular file")
    with legacy.open("rb") as stream:
        source = stream.read(512 * 1024 + 1)
    if len(source) > 512 * 1024 or hashlib.sha256(source).hexdigest() != data["sha256"]:
        raise ValueError("registered local policy bytes changed or exceed the source budget")
    return legacy, source


def _payload(action, tool, args):
    return {"tool_name": tool, "tool_input": args, "cwd": action.cwd,
            "session_id": action.session_id}


def _runtime_action(tool, args, depth=0):
    from skyguard import mcp_rules
    if not isinstance(args, dict):
        raise ValueError("local DevBench policy requires structured arguments")
    tool = tool.casefold()
    if tool == "game" and str(args.get("action", "")).strip().casefold() in ("save", "load", "loadlast"):
        return True
    lines = []
    if tool == "console":
        lines = list(mcp_rules._strings(args.get("command", args)))
    if tool == "papyrus":
        function = str(args.get("function", "")).rsplit(".", 1)[-1].casefold()
        if function in ("savegame", "loadgame", "requestsave", "requestautosave", "quittomainmenu"):
            return True
        if function in mcp_rules._CONSOLE_FUNCTIONS:
            lines = list(mcp_rules._strings(args.get("args")))
    for line in lines:
        for statement in mcp_rules._statements(line):
            words = statement.split()
            if words and words[0].casefold() in ("save", "load", "savegame", "loadgame", "qsave", "qload"):
                return True
    if depth < 8:
        for nested_tool, nested_args in mcp_rules._steps(args):
            if _runtime_action(nested_tool, nested_args, depth + 1):
                return True
    return False


def plan_facts(plan):
    """Only the dispatcher's already computed plan supplies these facts.

    Host provenance is deliberately excluded. The closure is evaluated after
    game policy and before permission or any preservation side effect.
    """
    if plan is None:
        return None
    facts = {"mutations": [asdict(row) for row in plan.mutations],
            "wildcard": plan.wildcard, "helper": plan.helper,
            "recheck": callable(plan.recheck), "unresolved": plan.unresolved}
    if isinstance(plan, DeclaredSavePlan):
        facts["declared_members"] = list(plan.declared_members)
    return facts


def supplement_native_backups(action, project, plan, profile, *, helper=False, deadline=None):
    """Retain qualified legacy requirements that the base game parser omits.

    This only adds preservation for literal native file operations; it cannot
    clear an unresolved game plan or grant permission. The real local policy
    still evaluates before the dispatcher can copy or allow anything.
    """
    if action.surface != "files" or plan.unresolved:
        return plan
    selected = [(path, operation) for path, operation in zip(action.paths, action.operations)
                if operation in ("write", "move", "delete") and save_profile.legacy_suffix(path) is not None]
    if not selected:
        return plan
    registered = registration(project)
    if registered is None or hashlib.sha256(registered[1]).hexdigest() != TRANSFER_POLICY_SHA256:
        return plan
    from modding_harness.dispatch import Mutation
    def extra():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("legacy preservation scope budget expired")
        rows = [Mutation(path, operation) for path, operation in selected
                if any(member.is_file() for member in profile.members(path))]
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("legacy preservation scope budget expired")
        return rows
    def recheck():
        original = plan.recheck() if plan.recheck is not None else plan.mutations
        if registration(project) != registered:
            raise ValueError("registered policy changed during save preservation")
        return tuple(dict.fromkeys((*original, *extra())))
    return replace(plan, mutations=tuple(dict.fromkeys((*plan.mutations, *extra()))),
                   helper=plan.helper or helper, recheck=recheck)


def _controlled_scope(action, project):
    """Bind data consumed by the reviewed controller; never run its entrypoint."""
    from skyguard import shellparse
    parsed = shellparse.parse(action.command, action.shell)
    if not parsed.ok or parsed.unread or parsed.codes or len(parsed.execs) != 1:
        raise ValueError("controlled loader requires one literal invocation")
    call = parsed.execs[0]
    if (not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?|py", call.program) or
            len(call.argv) != 4 or call.argv[1] not in ("prepare", "load") or
            call.argv[2] != "--request" or call.redirects or call.stdin_from or call.cd_before):
        raise ValueError("controlled loader requires its exact action and request")
    helper = Path(project).resolve() / ".claude/scripts/guarded-runtime-load.py"
    candidate, request = Path(call.argv[0]), Path(call.argv[3])
    if not candidate.is_absolute() or profiles.canonical(candidate) != profiles.canonical(helper) or not request.is_absolute():
        raise ValueError("controlled loader requires the installed controller and an absolute request")
    bindings, contents = [], []
    for path, budget in ((helper, 512 * 1024), (request, 65536)):
        if path.is_symlink() or not path.is_file():
            raise ValueError("controlled dependency is not a regular file")
        with path.open("rb") as stream:
            raw = stream.read(budget + 1)
        if len(raw) > budget:
            raise ValueError("controlled dependency exceeds its byte budget")
        bindings.append((profiles.canonical(path), hashlib.sha256(raw).hexdigest()))
        contents.append(raw)
    if bindings[0][1] != CONTROLLED_LOAD_SHA256:
        raise ValueError("controlled loader code differs from the qualified version")
    data = json.loads(contents[1].decode("utf-8-sig"))
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("unsupported controlled request schema")
    selected = []
    for field in ("source", "test"):
        name = data.get(field)
        if not isinstance(name, str) or not Path(name).is_absolute() or not re.fullmatch(r"[A-Za-z0-9_-]+\.ess", Path(name).name, re.I):
            raise ValueError("controlled request needs literal absolute ESS identities")
        selected.append(name)
    source, test = map(Path, selected)
    if profiles.canonical(source) == profiles.canonical(test) or not test.stem.startswith("ShinsoObserver_Headless_"):
        raise ValueError("controlled request requires a separate named test pair")
    if not source.is_file() or call.argv[1] == "load" and not test.is_file():
        raise ValueError("controlled request source or test is absent")
    if call.argv[1] == "prepare" and (test.exists() or test.with_suffix(".skse").exists()):
        raise ValueError("controlled preparation refuses an existing test pair")
    return "controlled-" + call.argv[1], tuple(selected if call.argv[1] == "prepare" else selected[1:]), tuple(bindings)


def supplement_controlled(action, project, plan, profile, deadline=None):
    if action.surface != "shell" or plan.unresolved or "guarded-runtime-load.py" not in action.command.casefold():
        return plan
    registered = registration(project)
    if registered is None or hashlib.sha256(registered[1]).hexdigest() != TRANSFER_POLICY_SHA256:
        return plan
    report = inspect_policy(action, project, deadline)
    requirements = report["requirements"]
    if report["refusal"] is not None or not any(row["op"] in ("controlled-prepare", "controlled-load") for row in requirements):
        return plan
    scope = _controlled_scope(action, project)
    operation, selected, _bindings = scope
    if (len(requirements) != 1 or requirements[0]["kind"] != "backup" or
            requirements[0]["op"] != operation or
            sorted(map(profiles.canonical, requirements[0].get("affected", ()))) != sorted(map(profiles.canonical, selected))):
        raise ValueError("controlled requirements differ from the qualified request")
    from modding_harness.dispatch import Mutation
    def current():
        return tuple(Mutation(path, "write") for path in selected if any(member.is_file() for member in profile.members(path)))
    def recheck():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("controlled preservation budget expired")
        if (registration(project) != registered or _controlled_scope(action, project) != scope or
                inspect_policy(action, project, deadline) != report):
            raise ValueError("controlled policy, request or scope changed during preservation")
        original = plan.recheck() if plan.recheck is not None else plan.mutations
        return tuple(dict.fromkeys((*original, *current())))
    # Re-evaluate against the exact bound data, then retain that binding until
    # preservation finishes. The controller still enforces its runtime gates.
    if inspect_policy(action, project, deadline) != report or _controlled_scope(action, project) != scope:
        raise ValueError("controlled request changed during inspection")
    return replace(plan, mutations=tuple(dict.fromkeys((*plan.mutations, *current()))), helper=True, recheck=recheck)


def _declared_choice(action, transcript, operation, profile):
    """One unambiguous complete scope from qualified, command-bound grants."""
    from modding_harness.dispatch import Mutation
    grants, _why = approval.read(transcript, action.host, action.session_id)
    choices = {}
    for grant in grants:
        if grant.operation != operation:
            continue
        try:
            groups = {}
            for path in grant.paths:
                members = profile.members(path)
                if not any(member.is_file() for member in members):
                    raise ValueError("declared scope names no existing save")
                key = tuple(profiles.canonical(member) for member in members)
                groups[key] = members
            complete = tuple(sorted({member for key in groups for member in key}))
            if not groups or not grant.matches(action.session_id, operation, complete, action.command, True, len(groups)):
                continue
            mutations = tuple(Mutation(str(groups[key][0]), operation) for key in sorted(groups))
            choices[complete] = (mutations, complete)
        except (OSError, ValueError):
            continue
    # Different scopes for the same opaque command cannot be resolved by
    # guessing which one its unparsed implementation will use.
    return next(iter(choices.values())) if len(choices) == 1 else None


def supplement_declared(action, project, plan, profile, transcript="", deadline=None):
    if (action.surface != "shell" or plan.unresolved or plan.mutations or plan.wildcard or plan.helper or
            plan.recheck is not None):
        return plan
    registered = registration(project)
    if registered is None or hashlib.sha256(registered[1]).hexdigest() != TRANSFER_POLICY_SHA256:
        return plan
    report = inspect_policy(action, project, deadline)
    requirements = report["requirements"]
    if (report["refusal"] is not None or len(requirements) != 1 or
            requirements[0]["kind"] != "needs_set" or requirements[0].get("set") is not None):
        return plan
    operation = {"copy": "write", "restore": "write", "rename": "move"}.get(requirements[0]["op"], requirements[0]["op"])
    if operation not in ("write", "move", "delete"):
        return plan
    choice = _declared_choice(action, transcript, operation, profile)
    if choice is None:
        return plan
    def recheck():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("declared preservation budget expired")
        if (registration(project) != registered or inspect_policy(action, project, deadline) != report or
                _declared_choice(action, transcript, operation, profile) != choice):
            raise ValueError("declared human scope or policy changed during preservation")
        return choice[0]
    return DeclaredSavePlan(mutations=choice[0], wildcard=True, helper=True, recheck=recheck,
                            known_writer_active=plan.known_writer_active, declared_members=choice[1])


def _transfer_roles(namespace, payload):
    args = payload["tool_input"]
    text = args.get("command", args.get("cmd", "")) if isinstance(args, dict) else args
    form = namespace["transfer_form"](text, args)
    resolved = namespace["resolve_transfer"](form)
    if resolved is None:
        return ()
    sources, destinations = resolved
    return tuple((path, operation) for paths, operation in ((sources, "move"), (destinations, "write"))
                 for path in paths if namespace["is_save"](path))


def _coverage(decision, facts, roles=(), *, controlled=False, declared=False):
    """Prove the portable plan retains a legacy non-allow requirement."""
    def refuse(why):
        return contracts.Result("deny", "evaluated", reason="Installed local policy: " + why +
            ". Use a qualified save operation with explicit paths; no save bytes were preserved.")
    if not isinstance(facts, dict) or facts.get("unresolved"):
        return refuse("save preservation scope has no qualified portable plan")
    kind, operation = decision["kind"], decision.get("op")
    if controlled and operation in ("controlled-prepare", "controlled-load"):
        if facts.get("helper") is not True or facts.get("recheck") is not True:
            return refuse("controlled scope requires a dependency and membership recheck")
        operation = "write"
    operation = {"copy": "write", "restore": "write", "rename": "move"}.get(operation, operation)
    if operation not in ("write", "move", "delete"):
        return refuse("this legacy save operation has not been qualified in the portable dispatcher")
    targets = decision.get("set") if kind == "needs_set" else decision.get("affected")
    explicit = kind == "needs_set" and targets is None and declared and facts.get("helper") is True
    if explicit:
        targets = facts.get("declared_members")
    if not isinstance(targets, list):
        return refuse("the legacy helper's save membership is unresolved")
    if kind == "needs_set" and (facts.get("wildcard") is not True or facts.get("recheck") is not True):
        return refuse("a command-bound set approval and membership recheck are required")
    profile = save_profile.skyrim()
    def key(path):
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("local policy returned a nonliteral absolute save identity")
        members = profile.members(path)
        return tuple(profiles.canonical(p) for p in members), any(p.exists() for p in members)
    covered = {}
    for row in facts.get("mutations", ()):
        identity, exists = key(row["path"])
        if exists:
            covered.setdefault(identity, set()).add(row["operation"])
    if explicit and (not covered or {profiles.canonical(path) for path in targets} !=
                     {member for identity in covered for member in identity}):
        return refuse("declared helper scope must include every complete pair")
    mapped = {}
    for path, role in roles:
        if role not in ("move", "write"):
            raise ValueError("unqualified legacy transfer role")
        identity, _exists = key(path)
        mapped.setdefault(identity, set()).add(role)
    needed = {}
    for path in targets:
        identity, exists = key(path)
        if exists:
            needed[identity] = mapped.get(identity, {operation})
    if any(not required <= covered.get(identity, set()) for identity, required in needed.items()):
        return refuse("portable preservation does not cover every legacy save and operation")
    if kind == "needs_files":
        count = decision.get("count")
        if type(count) is not int or count <= 2 or len(covered) < count:
            return refuse("the legacy bulk approval requirement is not retained")
    # Dispatcher subsequently reads canonical human approval, copies every pair,
    # verifies it and rechecks bound membership. This worker never grants approval.
    return None


def evaluate(action, project, facts=None, *, requirements=None):
    registered = registration(project)
    if registered is None:
        return contracts.Result(evaluation="evaluated")
    path, source = registered
    namespace = {"__file__": str(path), "__name__": "_registered_skyrim_local_policy", "__package__": None}
    exec(compile(source, str(path), "exec"), namespace)
    decide = namespace.get("decide")
    if not callable(decide):
        raise ValueError("registered local policy has no pure decide entry")
    if action.surface == "files":
        payloads = [(_payload(action, "Write", {"file_path": path}), operation)
                    for path, operation in zip(action.paths, action.operations) if operation != "read"]
    elif action.surface == "shell":
        payloads = [(_payload(action, "PowerShell" if action.shell == "powershell" else "Bash",
                             {"command": action.command}), None)]
    elif action.surface == "mcp":
        raw = action.provenance.get("raw_payload", {})
        args = action.provenance.get("arguments", raw.get("args", raw.get("tool_input", {})))
        from skyguard import mcp_rules
        if mcp_rules.judged_server(action.tool) and _runtime_action(action.tool.rsplit("__", 1)[-1], args):
            return contracts.Result("deny", "evaluated", reason=
                "Installed local policy: generic automated runtime save/load is refused; use only the qualified controlled-load route.")
        payloads = [(_payload(action, action.tool, args), None)]
    else:
        payloads = []
    for payload, file_operation in payloads:
        result = decide(payload)
        if not isinstance(result, dict) or result.get("kind") not in (
                "allow", "deny", "backup", "needs_files", "needs_set"):
            raise ValueError("registered policy returned an unsupported decision")
        if result["kind"] == "deny":
            return contracts.Result("deny", "evaluated", reason=str(result.get("reason") or "installed local policy refused"))
        if result["kind"] != "allow":
            if file_operation is not None:
                result = dict(result, op=file_operation)
            # Inspection collects requirements, never an execution verdict.
            if requirements is not None:
                requirements.append({key: result[key] for key in ("kind", "op", "affected", "set", "count") if key in result})
                continue
            roles = ()
            if (file_operation is None and result.get("op") in ("move", "rename") and
                    hashlib.sha256(source).hexdigest() == TRANSFER_POLICY_SHA256):
                roles = _transfer_roles(namespace, payload)
            controlled = False
            if result.get("op") in ("controlled-prepare", "controlled-load") and hashlib.sha256(source).hexdigest() == TRANSFER_POLICY_SHA256:
                operation, selected, _bindings = _controlled_scope(action, project)
                controlled = (result["kind"] == "backup" and result["op"] == operation and
                              sorted(map(profiles.canonical, result.get("affected", ()))) == sorted(map(profiles.canonical, selected)))
            refused = _coverage(result, facts, roles, controlled=controlled,
                                declared=hashlib.sha256(source).hexdigest() == TRANSFER_POLICY_SHA256)
            if refused is not None:
                return refused
    # Save authorization and preservation remain the portable dispatcher's job.
    if registration(project)[1] != source:
        raise ValueError("registered policy changed during evaluation")
    return contracts.Result(evaluation="evaluated")


def restriction(project, deadline=None, plan_provider=None):
    def check(action):
        if action.surface not in ("files", "shell", "mcp") or (
                action.surface == "files" and not any(op != "read" for op in action.operations)):
            return contracts.Result(evaluation="evaluated")
        try:
            registered = registration(project)
            if registered is None:
                return contracts.Result(evaluation="evaluated")
            remaining = min(10, deadline - time.monotonic()) if deadline is not None else 10
            if remaining <= 0:
                raise TimeoutError("local policy budget expired")
            facts = plan_facts(plan_provider()) if plan_provider is not None else None
            data = json.dumps({"v": 1, "action": asdict(action), "save_plan": facts}, ensure_ascii=True).encode("ascii")
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("local policy input exceeds its byte budget")
            completed = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), str(project)],
                                       input=data, capture_output=True, timeout=remaining)
            if completed.returncode or len(completed.stdout) > 65536:
                raise ValueError("local policy worker did not return a bounded verdict")
            result = transport.load(completed.stdout)
            warnings = result.get("warnings")
            if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
                raise ValueError("local policy worker returned malformed warnings")
            result["warnings"] = tuple(warnings)
            if registration(project) != registered:
                raise ValueError("local policy registration changed during evaluation")
            return contracts.Result(**result)
        except (OSError, ValueError, TypeError, TimeoutError, subprocess.TimeoutExpired) as exc:
            return contracts.Result("deny", "failed", reason="installed local policy failed: " + str(exc)
                                    if isinstance(exc, ValueError) else "installed local policy failed: " + type(exc).__name__)
    return check


def inspect_policy(action, project, deadline=None):
    """Inspect the pure policy once; this report cannot authorize execution.

    No approval reader, backup implementation or dispatcher runs here. Only
    preservation requirements cross the worker boundary; command metadata is
    excluded. A future planner must qualify every requirement before using it.
    """
    registered = registration(project)
    if registered is None:
        return {"v": 1, "mode": "inspection-only", "evaluation": "absent", "refusal": None,
                "requirements": [], "source_sha256": None}
    remaining = min(10, deadline - time.monotonic()) if deadline is not None else 10
    if remaining <= 0:
        raise TimeoutError("local policy budget expired")
    data = json.dumps({"v": 1, "action": asdict(action), "save_plan": None, "inspection": True},
                      ensure_ascii=True).encode("ascii")
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("local policy input exceeds its byte budget")
    completed = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), str(project)],
                               input=data, capture_output=True, timeout=remaining)
    if completed.returncode or len(completed.stdout) > 65536:
        raise ValueError("local policy worker did not return a bounded inspection")
    report = transport.load(completed.stdout)
    if (set(report) != {"v", "mode", "evaluation", "refusal", "requirements"} or
            type(report["v"]) is not int or report["v"] != 1 or report["mode"] != "inspection-only" or
            report["evaluation"] != "evaluated" or
            report["refusal"] is not None and not isinstance(report["refusal"], str) or
            not isinstance(report["requirements"], list)):
        raise ValueError("local policy worker returned malformed inspection")
    for row in report["requirements"]:
        if (not isinstance(row, dict) or set(row) - {"kind", "op", "affected", "set", "count"} or
                row.get("kind") not in ("backup", "needs_files", "needs_set") or
                not isinstance(row.get("op"), str)):
            raise ValueError("local policy worker returned malformed preservation requirements")
        paths = row.get("set") if row["kind"] == "needs_set" else row.get("affected")
        if "count" in row and (type(row["count"]) is not int or row["count"] < 0):
            raise ValueError("local policy worker returned malformed logical count")
        if paths is None and row["kind"] == "needs_set":
            continue  # An explicitly unresolved helper remains unresolved.
        if not isinstance(paths, list) or not all(isinstance(path, str) and Path(path).is_absolute() for path in paths):
            raise ValueError("local policy worker returned nonliteral preservation requirements")
    if registration(project) != registered:
        raise ValueError("local policy registration changed during inspection")
    report["source_sha256"] = hashlib.sha256(registered[1]).hexdigest()
    return report


if __name__ == "__main__":
    try:
        import contextlib
        class NoPolicyOutput:
            def write(self, text):
                if text:
                    raise ValueError("local decide entry must not print output")
                return 0
            def flush(self):
                pass
        data = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("worker input too large")
        envelope = transport.load(data)
        fields = {"v", "action", "save_plan"}
        inspection = "inspection" in envelope
        if (set(envelope) != fields | ({"inspection"} if inspection else set()) or
                inspection and envelope["inspection"] is not True or
                type(envelope["v"]) is not int or envelope["v"] != 1):
            raise ValueError("unsupported internal policy envelope")
        action = contracts.Action(**envelope["action"])
        requirements = [] if inspection else None
        with contextlib.redirect_stdout(NoPolicyOutput()), contextlib.redirect_stderr(NoPolicyOutput()):
            result = evaluate(action, Path(sys.argv[1]), envelope["save_plan"], requirements=requirements)
        if len(result.reason.encode("utf-8")) > 8192:
            raise ValueError("local policy reason exceeds its byte budget")
        reply = ({"v": 1, "mode": "inspection-only", "evaluation": result.evaluation,
                  "refusal": result.reason if result.decision != "allow" else None,
                  "requirements": requirements} if inspection else result.as_dict())
        encoded = (json.dumps(reply, ensure_ascii=True) + "\n").encode("ascii")
        if len(encoded) > 65536:
            raise ValueError("local policy output exceeds its byte budget")
        sys.stdout.buffer.write(encoded)
    except Exception:
        raise SystemExit(2)
