"""Consumer wiring preserves readiness gates and the heavy-job budget."""
import re
import yaml
from conftest import REPO


def _workflow(name):
    return yaml.safe_load((REPO / ".github/workflows" / name).read_text())


def test_draft_and_ready_events_use_the_pinned_shared_policy_and_fail_closed_gate():
    data = _workflow("ci.yml")
    triggers = data.get(True, data.get("on"))
    assert set(triggers["pull_request"]["types"]) >= {"opened", "synchronize", "reopened", "ready_for_review"}
    assert triggers["push"]["branches"] == ["master", "main"]
    jobs = data["jobs"]
    call = jobs["qualification"]
    assert call["uses"] == "./.github/workflows/full-qualification.yml"
    assert set(call["needs"]) == {"policy","shell","python","node","toolkit","setup-smoke"}
    assert call["if"] == "${{ needs.policy.outputs.full_required == 'true' }}"
    assert call["concurrency"] == {"group":"full-qualification-${{ github.repository }}","queue":"max","cancel-in-progress":False}
    assert jobs["required"]["if"] == "${{ always() }}"
    assert set(jobs["required"]["needs"]) == set(call["needs"]) | {"qualification"}
    assert jobs["required"]["permissions"] == {"contents":"read","actions":"read"}
    refs = [step["uses"] for job in jobs.values() for step in job.get("steps",[]) if "/ci-policy@" in step.get("uses","")]
    assert refs and len(set(refs)) == 1
    assert re.fullmatch(r"WingedGuardian/modding-agent-harness/\.github/actions/ci-policy@[0-9a-f]{40}", refs[0])


def test_build_stages_keep_heavy_concurrency_within_the_runner_budget():
    # The behavior matrix runs EVERY leg at once (user ruling 2026-10-31: a run is as long as its slowest job, not
    # its queue); the cap is the shared core contract's MAX_PARALLEL (20, the account's hosted-runner limit), not
    # the old 4. The two build stages before it stay small: they gate the matrix and run before it.
    jobs = _workflow("full-qualification.yml")["jobs"]
    assert set(jobs["release-build"]["needs"]) == {"dotnet","resaver-plus"}
    assert jobs["behavior"]["needs"] == ["release-build"]
    assert "needs" not in jobs["dotnet"] and "needs" not in jobs["resaver-plus"]
    initial = len(jobs["dotnet"]["strategy"]["matrix"]["os"]) + 1
    release = len(jobs["release-build"]["strategy"]["matrix"]["os"])
    behavior = jobs["behavior"]["strategy"]["max-parallel"]
    assert max(initial, release) <= 4
    legs = len(jobs["behavior"]["strategy"]["matrix"]["os"]) * len(jobs["behavior"]["strategy"]["matrix"]["python"]) \
        + len(jobs["behavior"]["strategy"]["matrix"]["include"])
    assert legs <= behavior <= 20, (legs, behavior)
    assert jobs["full-required"]["if"] == "${{ always() }}"
    assert set(jobs["full-required"]["needs"]) == {"behavior","dotnet","resaver-plus","release-build","mutations-total"}
