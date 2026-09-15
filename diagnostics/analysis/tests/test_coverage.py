"""§4.4 / §5.3's coverage guard: a window's data is trustworthy, partly trustworthy, or not.

Every test below is really testing one thing from two directions: that `Coverage` cannot be
misread as "the line was quiet" when the truth is "ingest was down". §6.1 step 3 makes that
distinction a guard the agent's answer must carry, so the return type — not a docstring — is
what has to make the two situations impossible to confuse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analysis.coverage import Coverage, GapInterval, compute_coverage
from analysis.windows import Window


def _at(seconds: int) -> datetime:
    """A UTC instant `seconds` after a fixed epoch, so test windows read as plain offsets."""
    return datetime(2026, 9, 15, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def _window(start: int, end: int) -> Window:
    return Window(from_ts=_at(start), to_ts=_at(end))


def _gap(start: int, end: int, reason: str = "ingest down") -> GapInterval:
    return GapInterval(from_ts=_at(start), to_ts=_at(end), reason=reason)


def test_no_gaps_is_fully_covered() -> None:
    coverage = compute_coverage(_window(0, 100), gaps=[])

    assert coverage.fully_covered is True
    assert coverage.covered_fraction == 1.0
    assert coverage.gaps == ()


def test_window_wholly_inside_one_gap_is_zero_covered() -> None:
    coverage = compute_coverage(_window(20, 80), gaps=[_gap(0, 100)])

    assert coverage.fully_covered is False
    assert coverage.covered_fraction == 0.0
    # Clipped to the window, not reported at the gap's own 0..100 extent.
    assert coverage.gaps == (GapInterval(_at(20), _at(80), "ingest down"),)


def test_gap_overlapping_only_the_leading_edge_is_clipped() -> None:
    coverage = compute_coverage(_window(0, 100), gaps=[_gap(-50, 30, "reconnect")])

    assert coverage.gaps == (GapInterval(_at(0), _at(30), "reconnect"),)
    assert coverage.covered_fraction == 0.7
    assert coverage.fully_covered is False


def test_gap_overlapping_only_the_trailing_edge_is_clipped() -> None:
    coverage = compute_coverage(_window(0, 100), gaps=[_gap(70, 150, "overflow")])

    assert coverage.gaps == (GapInterval(_at(70), _at(100), "overflow"),)
    assert coverage.covered_fraction == 0.7
    assert coverage.fully_covered is False


def test_several_gaps_including_touching_and_overlapping_ones_do_not_double_count() -> (
    None
):
    # [10,30) and [30,50) touch exactly at 30; [40,50) sits inside the second and also
    # overlaps a third, disjoint gap [90,100). Naively summing all four clipped durations
    # would give 20+20+10+10=60 uncovered seconds out of 100 (0.40 covered) instead of the
    # true 50 uncovered seconds (0.50 covered) the merged union actually spans.
    coverage = compute_coverage(
        _window(0, 100),
        gaps=[
            _gap(10, 30, "a"),
            _gap(30, 50, "b"),
            _gap(40, 50, "c"),
            _gap(90, 100, "d"),
        ],
    )

    assert coverage.covered_fraction == 0.5
    assert coverage.fully_covered is False
    assert (
        len(coverage.gaps) == 4
    )  # provenance kept: one entry per input row, not merged


def test_gap_entirely_outside_the_window_is_ignored() -> None:
    coverage = compute_coverage(_window(0, 100), gaps=[_gap(200, 300)])

    assert coverage.gaps == ()
    assert coverage.fully_covered is True
    assert coverage.covered_fraction == 1.0


def test_gap_ending_exactly_at_the_window_start_does_not_overlap() -> None:
    # Half-open semantics: [.., from_ts) ends the instant the window begins.
    coverage = compute_coverage(_window(50, 100), gaps=[_gap(0, 50)])

    assert coverage.gaps == ()
    assert coverage.fully_covered is True


def test_gap_starting_exactly_at_the_window_end_does_not_overlap() -> None:
    coverage = compute_coverage(_window(0, 50), gaps=[_gap(50, 100)])

    assert coverage.gaps == ()
    assert coverage.fully_covered is True


def test_fully_covered_and_gap_covered_windows_are_not_confusable() -> None:
    """The distinguishing test. Both windows below would show "zero events found" to a
    caller that only looked at a count — the whole reason this module exists is that the
    two `Coverage` reports must still visibly differ."""
    quiet = compute_coverage(_window(0, 100), gaps=[])
    blacked_out = compute_coverage(_window(0, 100), gaps=[_gap(0, 100, "gateway down")])

    assert quiet.fully_covered is True
    assert blacked_out.fully_covered is False
    assert quiet.gaps != blacked_out.gaps
    assert quiet.covered_fraction != blacked_out.covered_fraction
    assert quiet != blacked_out


def test_coverage_report_carries_the_requested_window() -> None:
    window = _window(0, 100)
    coverage = compute_coverage(window, gaps=[])

    assert isinstance(coverage, Coverage)
    assert coverage.window == window
