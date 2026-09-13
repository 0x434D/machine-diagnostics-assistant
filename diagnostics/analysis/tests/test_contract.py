"""The committed contract and the served schema must not drift apart.

§10.1: contracts/ is the single source of truth, and §7.3 generates the frontend's
TypeScript from it. A contract that quietly disagrees with the service is worse than none,
because the frontend's types then assert something false with full confidence.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from analysis.app import app
from fastapi.testclient import TestClient

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "analysis.openapi.yaml"


def _committed() -> dict[str, object]:
    """The contract as plain data. Typed as object rather than Any so that every step into
    it has to say what it expects, which is the point of reading it in a test at all."""
    loaded = yaml.safe_load(CONTRACT.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _mapping(parent: dict[str, object], key: str) -> dict[str, object]:
    value = parent[key]
    assert isinstance(value, dict), f"{key} is not a mapping"
    return value


def test_the_served_schema_matches_the_committed_contract() -> None:
    served = TestClient(app).get("/openapi.json").json()
    committed = _committed()

    paths = _mapping(committed, "paths")
    assert set(paths) == set(served["paths"])
    for path in paths:
        assert set(_mapping(paths, path)) == set(served["paths"][path]), path


def test_the_response_shapes_match_too_not_only_the_paths() -> None:
    """Paths alone would let a field be added, removed or retyped without failing —
    which is exactly the drift the generated TypeScript would then encode as truth."""
    served = TestClient(app).get("/openapi.json").json()

    committed = _mapping(_mapping(_committed(), "components"), "schemas")
    assert committed == served["components"]["schemas"]


def test_coverage_is_part_of_every_stats_response() -> None:
    """§5.3: /coverage exists so the agent can ask whether it has data before answering. In
    M1 it is folded into the one tool rather than split out, so §6.1's step-3 coverage check
    cannot be skipped by forgetting to call a second endpoint."""
    contract = _committed()
    schemas = _mapping(_mapping(contract, "components"), "schemas")
    stats = _mapping(_mapping(_mapping(contract, "paths"), "/inspection/stats"), "get")
    ok = _mapping(_mapping(stats, "responses"), "200")
    schema = _mapping(_mapping(_mapping(ok, "content"), "application/json"), "schema")
    ref = str(schema["$ref"]).rsplit("/", 1)[-1]

    assert "coverage" in _mapping(_mapping(schemas, ref), "properties")


def test_a_part_can_be_resolved_by_serial() -> None:
    """§6.5 verifies every cited id against the database and §7.2 requires that clicking a
    citation opens the underlying data. A citation you cannot open is barely a citation."""
    assert "/parts/{serial}" in _mapping(_committed(), "paths")
