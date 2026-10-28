"""P2 retained probe: a real, empty, synthetic Git repo; no upstream fetch or compiler."""
import os
import subprocess

import pytest
from test_resaver_plus_build import _load


@pytest.mark.parametrize("value", ["true", "input", "false", "0", None])
def test_cache_admission_reads_stored_local_autocrlf_without_overriding_it(tmp_path, monkeypatch, value):
    mod = _load()
    cache = tmp_path / "upstream"
    global_config = tmp_path / "global-git-config"
    global_config.write_text("[core]\n\tautocrlf = false\n", encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_GLOBAL=str(global_config), GIT_CONFIG_NOSYSTEM="1")
    original, calls = subprocess.run, []
    def run_git(args, **kwargs):
        args = [str(a) for a in args]
        assert args[0] == "git" and "clone" not in args and "fetch" not in args
        calls.append(args)
        kwargs["env"] = environment
        return original(args, **kwargs)
    monkeypatch.setattr(mod.subprocess, "run", run_git)
    def git(*args):
        return run_git(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    git("init", str(cache))
    git("-C", str(cache), "-c", "user.name=Synthetic", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "synthetic cache")
    head = git("-C", str(cache), "rev-parse", "HEAD")
    if value is not None:
        git("-C", str(cache), "config", "--local", "core.autocrlf", value)
    before = (cache / ".git/config").read_bytes()
    calls.clear()
    if value == "false":
        mod.fetch("synthetic-upstream", head, cache)
    else:
        with pytest.raises(mod.BuildError, match="line endings"):
            mod.fetch("synthetic-upstream", head, cache)
    read = next(args for args in calls if "config" in args)
    assert read == ["git", "-C", str(cache), "config", "--local", "--get", "core.autocrlf"]
    assert (cache / ".git/config").read_bytes() == before
    assert git("-C", str(cache), "rev-parse", "HEAD") == head


def test_failed_config_read_is_unknown_even_with_a_false_fragment(tmp_path, monkeypatch):
    mod = _load()
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(mod, "run_result", lambda *args, **kwargs: (128, "false\n"))
    monkeypatch.setattr(mod, "run", lambda *args, **kwargs: "synthetic-pin" if "rev-parse" in args[0] else "")
    with pytest.raises(mod.BuildError, match="line endings"):
        mod.fetch("synthetic-upstream", "synthetic-pin", tmp_path)
