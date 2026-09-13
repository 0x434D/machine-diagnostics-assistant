"""R5's runner is measurement code, so it is tested for correctness of the
*measurement*, not for the value it produces -- a threshold here would make the
gate depend on the machine it runs on."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "measurements"))

from run_r5 import measure


@pytest.mark.asyncio
async def test_the_probe_reports_every_row_it_asked_for(tmp_path: Path) -> None:
    """The whole point of R5 is catching silent loss, so a probe that cannot tell
    written from expected proves nothing."""
    result = await measure(
        streams=4,
        rows_per_stream=50,
        batch_size=20,
        pause_s=0.0,
        db_path=tmp_path / "r5.db",
    )
    assert result.rows_expected == 200
    assert result.rows_written == 200
    assert result.dropped == 0


@pytest.mark.asyncio
async def test_the_probe_detects_loss_rather_than_reporting_success(
    tmp_path: Path,
) -> None:
    """asyncua's per-item queue caps at 10,000 and discards the oldest. A probe that
    reports a clean run while rows are missing is the defect M1 found, reproduced in
    the tool meant to find it."""
    result = await measure(
        streams=1,
        rows_per_stream=12_000,
        batch_size=12_000,  # one uninterrupted burst, no chance to drain
        pause_s=0.0,
        db_path=tmp_path / "r5-overflow.db",
    )
    assert result.dropped > 0
    assert result.rows_written < result.rows_expected
