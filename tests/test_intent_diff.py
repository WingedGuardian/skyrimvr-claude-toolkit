"""scripts/guard-corpus/intent_diff.py: the table a reviewer reads before the code diff."""
import importlib.util
import json
import subprocess

from conftest import REPO


def _mod():
    spec = importlib.util.spec_from_file_location("intent_diff", REPO / "scripts" / "guard-corpus" / "intent_diff.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _row(rid, decision="deny", **kw):
    return dict({"id": rid, "payload": {"tool_name": "Bash", "cwd": "<install>", "tool_input": {"command": "rm x"}},
                 "decision": decision, "rules": ["S-DEL-INSTALL"], "why": "because", "supersedes": None}, **kw)


def test_only_added_and_changed_rows_appear_and_old_is_attributed():
    m = _mod()
    legacy = [{"id": "shell-a", "decision": "allow"}]
    old = [_row("keep"), _row("edit", "ask")]
    new = [_row("keep"), _row("edit", "deny"), _row("sup", supersedes="shell-a"), _row("fresh")]
    rows = {r[0]: r for r in m.table(old, new, legacy)}
    assert set(rows) == {"edit", "sup", "fresh"}
    assert rows["edit"][2] == "ask (previous)" and rows["edit"][3] == "deny"
    assert rows["sup"][2] == "allow (legacy shell-a)"
    assert rows["fresh"][2] == "-"
    assert "rm x" in rows["fresh"][1] and "@<install>" in rows["fresh"][1]
    assert "S-DEL-INSTALL" in m.render(list(rows.values()))


def test_existing_row_uses_previous_decision_even_when_it_supersedes_legacy():
    m = _mod()
    rows = m.table([_row("existing", "allow", supersedes="legacy")],
                   [_row("existing", "deny", supersedes="legacy")],
                   [{"id": "legacy", "decision": "deny"}])
    assert rows[0][2:4] == ["allow (previous)", "deny"]


def test_a_rev_range_reads_the_file_at_each_revision(tmp_path):
    m = _mod()
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=str(tmp_path),
                       check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "tests" / "guard_corpus").mkdir(parents=True)
    f = tmp_path / "tests" / "guard_corpus" / "intended.jsonl"
    f.write_text("", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "empty")
    f.write_text(json.dumps(_row("new1")) + "\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "row")
    base = m.parse(m.git_file("HEAD~1", m.INTENDED, tmp_path))
    head = m.parse(m.git_file("HEAD", m.INTENDED, tmp_path))
    assert base == [] and [r["id"] for r in head] == ["new1"]
    assert m.git_file("HEAD", "no/such/file", tmp_path) == ""
    assert [r[0] for r in m.table(base, head, [])] == ["new1"]


def test_main_writes_utf8_whatever_the_console_encoding(monkeypatch):
    """Final review C-M1: on a Windows console (cp1252) a row holding a non-Latin-1 character (the corpus has the
    Kelvin sign) crashed the table with UnicodeEncodeError. The table is written as UTF-8 bytes."""
    import io
    import sys
    m = _mod()
    kelvin = _row("kelvin", payload={"tool_name": "Bash", "tool_input": {"command": "rm S" + chr(0x212A) + "yrim"}})
    monkeypatch.setattr(m, "git_file", lambda rev, path, repo=None: json.dumps(kelvin) + "\n" if rev == "HEAD" else "")
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    assert m.main(["base..HEAD"]) == 0
    sys.stdout.flush()
    assert ("S" + chr(0x212A) + "yrim") in raw.getvalue().decode("utf-8")
