"""Pins the two deliberate cross-workspace duplications (§10.7) named in
simulator/render.py's own docstring and in inspection_client.py's DEFECT_CLASSES
comment: `simulator.render` against `inspection.render`, and
`simulator.inspection_client.DEFECT_CLASSES` against
`inspection.classifier.DEFECT_CLASSES`.

Reads the sibling package's source from disk rather than importing it: `simulator`
and `inspection` are separate uv workspace members that must not depend on each
other, even from a test (§10.7) -- an `import inspection...` here would itself be
the violation these tests exist to catch drift on.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SIMULATOR_SRC = Path(__file__).resolve().parents[1] / "src" / "simulator"
_INSPECTION_SRC = (
    Path(__file__).resolve().parents[2] / "inspection" / "src" / "inspection"
)


def _list_literal_assignment(path: Path, name: str) -> list[str]:
    """Reads `name`'s assigned list literal out of `path`'s source via the AST,
    without importing the module. Raises `AssertionError` if `path` never assigns
    to `name` at module level."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, list):
                raise AssertionError(f"{name} in {path} is not a list literal")
            return value
    raise AssertionError(f"no top-level assignment to {name} found in {path}")


def test_render_duplicate_matches_inspections_copy() -> None:
    """A change to one copy of render.py with no matching change to the other must
    fail here, not slip through as the two packages silently disagreeing on how a
    part looks."""
    ours = (_SIMULATOR_SRC / "render.py").read_bytes()
    theirs = (_INSPECTION_SRC / "render.py").read_bytes()
    assert ours == theirs


def test_defect_classes_duplicate_matches_inspections_copy() -> None:
    """Reproduces the review's finding: adding a seventh class to only one of the
    two DEFECT_CLASSES copies previously broke nothing, because nothing compared
    them."""
    ours = _list_literal_assignment(
        _SIMULATOR_SRC / "inspection_client.py", "DEFECT_CLASSES"
    )
    theirs = _list_literal_assignment(
        _INSPECTION_SRC / "classifier.py", "DEFECT_CLASSES"
    )
    assert ours == theirs
