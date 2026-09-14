"""Fixtures for the analysis tests.

Real Postgres in a container rather than a fake: the endpoints are almost entirely SQL, and
a fake would assert that the queries are the ones written rather than that they are right.
The schema comes from the gateway's migration so there is one definition of it in the
repository, not a second one drifting quietly here.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from analysis.app import app
from analysis.config import Settings
from analysis.db import reset_pool
from analysis.dependencies import settings_dependency
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "gateway"
    / "Gateway"
    / "Migrations"
    / "001_m1.sql"
)

# Pinned by digest, not tag (§10.7). scripts/pin-images.sh re-resolves it.
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)

WINDOW_START = datetime(2026, 9, 12, 1, 0, 0, tzinfo=UTC)
TAKT = timedelta(seconds=6)
PARTS_IN_AN_HOUR = 600
# Every 20th part, which is a density chosen for the fixture and not the plant's rate:
# D10 puts that at §3.5's 1.5 %, nine rejects in this window, too few to spread over six
# defect classes and still assert a breakdown. Deliberately not tracking
# `Settings.reject_rate` -- these are query tests over seeded rows, and a fixture that
# moved whenever the plant was retuned would make them fail for a reason that is not theirs.
REJECT_EVERY = 20
REJECT_OFFSET = 7  # so A-00000007 is a reject and A-00000006 is not


@pytest.fixture(scope="session")
def postgres() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        url = container.get_connection_url()
        with psycopg.connect(url) as conn:
            conn.execute(MIGRATION.read_text())
            conn.commit()
        yield url


@pytest.fixture
def database(postgres: str) -> Iterator[str]:
    with psycopg.connect(postgres) as conn:
        conn.execute(
            "TRUNCATE inspection_images, inspection_results, signals, ingest_gaps, stations "
            "RESTART IDENTITY CASCADE"
        )
        conn.execute("INSERT INTO stations (code, name) VALUES ('S3', 'Inspection')")
        conn.commit()
    reset_pool()
    yield postgres
    reset_pool()


def _seed_parts(url: str) -> None:
    with psycopg.connect(url) as conn:
        station = conn.execute("SELECT id FROM stations WHERE code = 'S3'").fetchone()
        assert station is not None
        for index in range(PARTS_IN_AN_HOUR):
            serial = f"A-{index:08d}"
            reject = index % REJECT_EVERY == REJECT_OFFSET
            conn.execute(
                "INSERT INTO inspection_results (assembly_serial, source_ts, station_id, result,"
                " defect_class, confidence, model_version, image_ref)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'sim-1', %s)",
                (
                    serial,
                    WINDOW_START + TAKT * index,
                    station[0],
                    "reject" if reject else "good",
                    "gap" if reject else None,
                    0.87 if reject else None,
                    serial if reject else None,
                ),
            )
            if reject:
                conn.execute(
                    "INSERT INTO inspection_images (assembly_serial, bytes) VALUES (%s, %s)",
                    (serial, b"\x89PNG\r\n\x1a\n" + b"x" * 64),
                )
        conn.commit()


@pytest.fixture
def seeded_db(database: str) -> str:
    _seed_parts(database)
    return database


@pytest.fixture
def seeded_db_with_gap(database: str) -> str:
    _seed_parts(database)
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO ingest_gaps (from_ts, to_ts, reason) VALUES (%s, %s, 'plant_unreachable')",
            (
                WINDOW_START + timedelta(minutes=20),
                WINDOW_START + timedelta(minutes=25),
            ),
        )
        conn.commit()
    return database


@pytest.fixture
def client(database: str) -> Iterator[TestClient]:
    app.dependency_overrides[settings_dependency] = lambda: Settings(
        database_url=database
    )
    yield TestClient(app)
    app.dependency_overrides.clear()
