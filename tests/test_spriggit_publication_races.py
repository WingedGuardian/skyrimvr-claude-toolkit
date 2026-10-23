"""Publication must preserve unrelated writers' bytes; synthetic artifacts only."""
import importlib.util
from pathlib import Path

import pytest

from conftest import REPO


@pytest.fixture
def publisher():
    spec = importlib.util.spec_from_file_location("race_publisher", REPO / "tools/spriggit_publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def artifacts(tmp_path, directory, existing):
    source, target = tmp_path / "candidate", tmp_path / "destination"
    if directory:
        source.mkdir()
        (source / "fresh.yaml").write_bytes(b"fresh")
        if existing:
            target.mkdir()
            (target / "old.yaml").write_bytes(b"old")
    else:
        source.write_bytes(b"fresh")
        if existing:
            target.write_bytes(b"old")
    return source, target


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_destination_changed_during_copy_is_retained(publisher, tmp_path, monkeypatch, directory, existing):
    source, target = artifacts(tmp_path, directory, existing)
    name = "copytree" if directory else "copy2"
    copy = getattr(publisher.shutil, name)

    def changing_copy(*args, **kwargs):
        result = copy(*args, **kwargs)
        if directory:
            target.mkdir(exist_ok=True)
            (target / "notes.txt").write_bytes(b"foreign writer")
        else:
            target.write_bytes(b"foreign writer")
        return result

    monkeypatch.setattr(publisher.shutil, name, changing_copy)
    with pytest.raises((OSError, ValueError), match="changed|exists|destination"):
        publisher.publish(source, target)
    assert (target / "notes.txt" if directory else target).read_bytes() == b"foreign writer"
    if directory and existing:
        assert (target / "old.yaml").read_bytes() == b"old"


@pytest.mark.parametrize("directory", [False, True])
def test_new_destination_at_publication_is_not_clobbered(publisher, tmp_path, monkeypatch, directory):
    source, target = artifacts(tmp_path, directory, False)
    rename = publisher._rename_new

    def creator(src, dst):
        if Path(src).parent.name.startswith(".spriggit-stage-"):
            if directory:
                target.mkdir()  # Even an empty new directory belongs to its creator.
            else:
                target.write_bytes(b"foreign writer")
        return rename(src, dst)

    monkeypatch.setattr(publisher, "_rename_new", creator)
    with pytest.raises(OSError):
        publisher.publish(source, target)
    if directory:
        assert target.is_dir() and list(target.iterdir()) == []
    else:
        assert target.read_bytes() == b"foreign writer"


@pytest.mark.parametrize("directory", [False, True])
def test_previous_destination_is_retained_after_commit(publisher, tmp_path, directory, capsys):
    source, target = artifacts(tmp_path, directory, True)
    publisher.publish(source, target)
    previous, = tmp_path.glob(".spriggit-previous-*")
    assert (previous / "old.yaml" if directory else previous).read_bytes() == b"old"
    assert (target / "fresh.yaml" if directory else target).read_bytes() == b"fresh"
    assert str(previous) in capsys.readouterr().err


@pytest.mark.parametrize("directory", [False, True])
def test_change_during_retirement_is_restored_with_its_bytes(publisher, tmp_path, monkeypatch, directory):
    source, target = artifacts(tmp_path, directory, True)
    rename = publisher._rename_new

    def editing_rename(src, dst):
        result = rename(src, dst)
        if Path(src) == target:
            (Path(dst) / "notes.txt" if directory else Path(dst)).write_bytes(b"foreign writer")
        return result

    monkeypatch.setattr(publisher, "_rename_new", editing_rename)
    with pytest.raises(ValueError, match="changed"):
        publisher.publish(source, target)
    assert (target / "notes.txt" if directory else target).read_bytes() == b"foreign writer"
    assert not list(tmp_path.glob(".spriggit-previous-*"))


def test_publication_failure_with_new_destination_keeps_both_versions(publisher, tmp_path, monkeypatch):
    source, target = artifacts(tmp_path, False, True)
    rename = publisher._rename_new

    def creator(src, dst):
        if Path(src).parent.name.startswith(".spriggit-stage-"):
            target.write_bytes(b"foreign writer")
        return rename(src, dst)

    monkeypatch.setattr(publisher, "_rename_new", creator)
    with pytest.raises((OSError, ValueError), match="retained|rollback"):
        publisher.publish(source, target)
    previous, = tmp_path.glob(".spriggit-previous-*")
    assert previous.read_bytes() == b"old"
    assert target.read_bytes() == b"foreign writer"
