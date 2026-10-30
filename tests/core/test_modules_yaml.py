"""Check that every workflow file is claimed by a module in docs/modules.yaml."""

import re
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES_YAML = REPO_ROOT / "docs" / "modules.yaml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

_PATH_ENTRY = re.compile(r"^\s+-\s+(?P<path>\S+)\s*$")


def _module_paths() -> list[str]:
    """Return every list entry under a ``paths:`` key in modules.yaml."""
    entries: list[str] = []
    in_paths = False
    for line in MODULES_YAML.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "paths:":
            in_paths = True
            continue
        match = _PATH_ENTRY.match(line)
        if in_paths and match:
            entries.append(match.group("path"))
        elif stripped:
            in_paths = False
    return entries


def _is_claimed(rel_path: str, patterns: list[str]) -> bool:
    path = PurePosixPath(rel_path)
    return any(path.full_match(pattern) for pattern in patterns)


def test_every_workflow_is_claimed_by_a_module() -> None:
    patterns = _module_paths()
    workflows = sorted(
        p.relative_to(REPO_ROOT).as_posix() for p in WORKFLOWS_DIR.iterdir() if p.is_file()
    )
    assert workflows, "no workflow files found"
    unclaimed = [w for w in workflows if not _is_claimed(w, patterns)]
    assert not unclaimed, f"workflows missing from docs/modules.yaml: {unclaimed}"
