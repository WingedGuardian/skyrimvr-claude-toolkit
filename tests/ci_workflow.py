"""Read both committed workflow files without overlooking a moved job."""
from pathlib import Path


def load_ci(root=None):
    import yaml
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    main = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    full = yaml.safe_load((root / ".github/workflows/full-qualification.yml").read_text(encoding="utf-8"))
    assert not (set(main["jobs"]) & set(full["jobs"])), "duplicate workflow job identity"
    main["jobs"].update(full["jobs"])
    return main
