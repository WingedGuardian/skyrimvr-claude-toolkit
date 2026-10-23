"""Synchronous policy/local-restriction/preservation ordering for any host adapter.

Profiles resolve game operations. This module never parses a game command, trusts
agent text as approval, or infers that a host actually intercepted an operation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from . import approval, profiles, workspace
from .contracts import Result
from .preservation import PreservationFailed, protects


@dataclass(frozen=True)
class Mutation:
    path: str
    operation: str


@dataclass(frozen=True)
class SavePlan:
    mutations: tuple[Mutation, ...] = ()
    wildcard: bool = False
    helper: bool = False
    unresolved: str = ""
    # Wildcard/helper producers must recompute their resolved set after copying.
    recheck: Callable[[], tuple[Mutation, ...]] | None = None
    known_writer_active: bool = False


@dataclass(frozen=True)
class Evaluation:
    result: Result
    saves: SavePlan = SavePlan()


@dataclass(frozen=True)
class Outcome:
    result: Result
    transactions: tuple[str, ...] = ()


class Dispatcher:
    def __init__(self, root, profile, policy, local_restrictions=()):
        self.root = Path(root).resolve()
        self.profile = profile
        self.policy = policy
        self.local_restrictions = tuple(local_restrictions)

    def pre(self, action, transcript="", deadline=None, preserve=True):
        """`preserve=False` is explicitly a policy preview, never execution evidence."""
        try:
            evaluation = self.policy(action)
            if not isinstance(evaluation, Evaluation):
                raise ValueError("profile returned no versioned evaluation")
            result, plan = evaluation.result, evaluation.saves
            # A deny returns at once because nothing will run. An ask can run on a
            # human "yes", so it is held and still passes save scope, approval and
            # preservation exactly like an allow before it is returned.
            asking = None
            for local in self.local_restrictions:
                restriction = local(action)
                if not isinstance(restriction, Result):
                    raise ValueError("local restriction returned no verdict")
                if restriction.decision == "deny":
                    return Outcome(restriction)
                if restriction.evaluation in ("failed", "partial"):
                    return Outcome(Result("deny", "failed", reason="local restriction could not be completely evaluated"))
                result = replace(result, warnings=result.warnings + restriction.warnings)
                if restriction.decision == "ask":
                    asking = asking or restriction
            if result.evaluation == "unguarded":
                # An unknown surface may warn and allow only without declared save work.
                # A local permission prompt cannot qualify an unresolved profile contract.
                if (plan.mutations or plan.unresolved or plan.wildcard or plan.helper or
                        plan.recheck is not None or plan.known_writer_active):
                    return Outcome(Result("deny", "partial", "failed", reason=
                        "unguarded profile declared save obligations; complete evaluation is required"))
                if asking is not None and result.decision != "deny":
                    result = replace(result, decision="ask", reason=asking.reason)
                warnings = result.warnings
                if not any("WARN AND ALLOW" in warning for warning in warnings):
                    warnings += ("WARN AND ALLOW: this surface was not evaluated",)
                return Outcome(replace(result, warnings=warnings))
            if action.api_version != 1 or len(action.paths) != len(action.operations):
                raise ValueError("affected paths and operations are not a complete version 1 mapping")
            if result.evaluation == "failed":
                return Outcome(replace(result, decision="deny"))
            if result.decision == "deny":
                return Outcome(result)
            if result.decision == "ask" and asking is None:
                asking = result
            if asking is not None:
                result = asking
            if plan.unresolved:
                return Outcome(Result("deny", "partial", "failed", reason="save scope unresolved: " + plan.unresolved))
            target = workspace.save_store(self.root, self.profile)
            for path, operation in zip(action.paths, action.operations):
                if operation in ("write", "move", "delete") and protects(
                        path, [target.root], ancestor=operation in ("move", "delete")):
                    return Outcome(Result("deny", "evaluated", reason="permanent save store is protected"))
            groups = {}
            operations = {}
            for mutation in plan.mutations:
                if mutation.operation not in ("write", "move", "delete"):
                    raise ValueError("unrecognized save mutation")
                members = self.profile.members(mutation.path)
                if any(protects(path, [target.root]) for path in members):
                    raise ValueError("save source is inside its preservation store")
                if not any(path.exists() for path in members):
                    continue
                key = tuple(profiles.canonical(path) for path in members)
                groups[key] = members
                operations.setdefault(mutation.operation, set()).add(key)
            bound = plan.wildcard or plan.helper
            if bound and plan.recheck is None:
                return Outcome(Result("deny", "partial", "failed", reason="resolved helper/wildcard scope has no membership recheck"))
            if approval.needs_approval(len(groups), plan.wildcard):
                grants, why = approval.read(transcript, action.host, action.session_id)
                missing = []
                for operation, keys in sorted(operations.items()):
                    members = [p for key in keys for p in groups[key]]
                    if not any(g.matches(action.session_id, operation, members, action.command, bound,
                                         len(keys)) for g in grants):
                        missing.append(approval.example(action.session_id, operation, members,
                                       action.command, bound, len(keys)))
                if not operations or missing:
                    return Outcome(Result("deny", "evaluated", "required", reason=
                        "typed canonical human approval required: " + (why or "scope mismatch") + "\n" + "\n".join(missing)))
            if not groups:
                return Outcome(result)
            if not preserve:
                return Outcome(replace(result, preservation="required", warnings=result.warnings +
                                       ("PREVIEW ONLY: no save bytes were preserved",)))
            transaction = target.backup(list(groups.values()), {
                "profile": self.profile.name, "host": action.host, "host_version": action.host_version,
                "session_id": action.session_id, "tool_call_id": action.tool_call_id,
                "surface": action.surface}, deadline, plan.known_writer_active)
            if transaction is None:
                raise PreservationFailed("all selected saves disappeared before preservation")
            if plan.recheck is not None and tuple(plan.recheck()) != plan.mutations:
                raise PreservationFailed("resolved save membership changed during preservation")
            for original_members in groups.values():
                now = self.profile.members(original_members[0])
                if tuple(profiles.canonical(p) for p in now) != tuple(profiles.canonical(p) for p in original_members):
                    raise PreservationFailed("logical save aliases changed during preservation")
            return Outcome(replace(result, preservation="verified"), (transaction["id"],))
        except (OSError, ValueError) as exc:
            return Outcome(Result("deny", "failed", "failed", reason="preservation/policy failed: " + str(exc)))
        except Exception as exc:
            return Outcome(Result("deny", "failed", "failed", reason="policy engine failed: " + type(exc).__name__))
