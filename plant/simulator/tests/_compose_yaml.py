"""Compose-YAML reading, shared by every test that parses a stack's `compose.yml`.

Not `test_`-prefixed, so pytest never collects it as a suite of its own -- it exists to
be imported. Factored out rather than left duplicated in `test_compose_invariants.py`:
CLAUDE.md's rule is that two copies of one piece of logic is where three comes from, and
`test_identity_boundary.py` needed the same YAML-shaped read as soon as it needed a
second Compose file's services.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml


def load(path: Path) -> dict[str, object]:
    return cast(dict[str, object], yaml.safe_load(path.read_text()))


def services(doc: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], doc.get("services") or {})
