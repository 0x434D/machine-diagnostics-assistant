"""§5.5's `/inspection/patterns`: statistics, not ranking — and often nothing.

*"Carrier 7 is 4 % above average, n=38, not significant"* is a valid and important answer,
and returning it is what stops the agent inventing a story out of a noise floor that was put
there on purpose. `significance.py` chose the test and `patterns.py` chose the correction;
this module reads the rows and reports what they said.

**The lot dimension is here and is not in §5.5's list, and §3.5 scenario 7 cannot be
answered without it.** That scenario is a run of rising `gap` defects with a *perfectly
stable* joining force: the symptom points straight at a press drift, and the only thing
separating that wrong answer from the right one is that the defects correlate with the
supplier lot. A part's lot membership is a per-part fact reached through the genealogy, so
testing it is no different in kind from testing a carrier — it is only the dimension §5.5
happened not to list.

**The lane dimension is refused rather than tested, and that is the other interesting
decision here.** §5.3 and §5.5 both list `lane`; §3.5 of the same specification says the line cannot
distinguish it, because every assembly draws one component from *each* feeder lane and there
is therefore no contrast group. Running the test anyway would return "not significant" over
two groups holding the same parts — a true-sounding sentence about a comparison that was
never made. The counts are real and stay available at `/inspection/stats?group_by=lane`;
what this endpoint declines to do is put a verdict on them.
"""

from __future__ import annotations

from fastapi import APIRouter

from analysis import patterns, queries
from analysis.db import connection
from analysis.dependencies import SettingsDep, WindowDep
from analysis.models import DimensionPatterns, PatternReport, PatternValue, Window
from analysis.routes_coverage import coverage_of
from analysis.significance import SignificanceSettings, Verdict

router = APIRouter()

LANE_NOT_COMPARABLE = (
    "\u00a73.5, on this line: \u201cevery assembly draws one component from each lane, so a "
    "contaminated lane touches every part and there is no contrast group: \u2018these parts "
    "saw lane 2 and those did not\u2019 is a distinction the line cannot make\u201d \u2014 and "
    "naming the lane \u201cneeds evidence the plant does not yet carry\u201d. So there is no "
    "comparison to run and no verdict to report. The counts are at "
    "/inspection/stats?group_by=lane; what cannot be had from them is a significance "
    "verdict, and 'not significant' here would be a statement about a comparison that was "
    "never made."
)
"""Why `lane` carries no verdict, in §3.5's own words.

Quoted rather than paraphrased on purpose. A reader who cannot see the reason reads the
absence as an oversight and \u201cnot significant\u201d as a clean bill of health, and the next
person to come along "fixes" this into a silent negative. The quotation is what makes it
plain that the specification already settled this, and where.
"""


@router.get("/inspection/patterns", operation_id="inspectionPatterns")
def inspection_patterns(window: WindowDep, settings: SettingsDep) -> PatternReport:
    """Every §5.5 dimension over the window, each value against the rest of its pool.

    Three observation sets, because the dimensions do not all count the same thing. Carrier
    and time bucket are one trial per part with "was it rejected" as the outcome. Lot is one
    trial per part *per lot it was built from* — a part contains two, one per feeder lane —
    with the same outcome. Defect class is one trial per part *per class* with "did it reach
    the threshold on this class" as the outcome — §3.4's six scores are independent, a part can carry several, and dividing a
    part between the classes it carries would invent a constraint the classifier does not
    have. Both use the part as the denominator, which is the same denominator
    `/inspection/stats?group_by=defect_class` counts against.

    Coverage rides along for the reason it rides along everywhere: a window the gateway was
    down for produces a smaller sample, and a smaller sample is exactly what turns a real
    effect into `not_enough_data`.
    """
    pattern_settings = patterns.PatternSettings(
        significance=SignificanceSettings(
            alpha=settings.significance_alpha,
            minimum_sample=settings.significance_minimum_sample,
        ),
        correction=settings.pattern_correction,
    )

    with connection(settings) as conn:
        parts = queries.part_observations(
            conn, window, int(settings.stats_time_bucket.total_seconds())
        )
        lots = queries.lot_observations(conn, window)
        classes = queries.defect_class_observations(
            conn, window, settings.defect_class_threshold
        )
        coverage = coverage_of(conn, window)

    dimensions = [
        _tested(
            patterns.find_patterns(parts, patterns.Dimension.CARRIER, pattern_settings)
        ),
        DimensionPatterns(
            dimension=patterns.Dimension.LANE,
            comparable=False,
            not_comparable=LANE_NOT_COMPARABLE,
            patterns=[],
            unattributed=None,
        ),
        _tested(patterns.find_patterns(lots, patterns.Dimension.LOT, pattern_settings)),
        _tested(
            patterns.find_patterns(
                classes, patterns.Dimension.DEFECT_CLASS, pattern_settings
            )
        ),
        _tested(
            patterns.find_patterns(
                parts, patterns.Dimension.TIME_BUCKET, pattern_settings
            )
        ),
    ]

    return PatternReport(
        window=Window.of(window),
        coverage=coverage,
        alpha=pattern_settings.significance.alpha,
        minimum_sample=pattern_settings.significance.minimum_sample,
        correction=pattern_settings.correction,
        defect_class_threshold=settings.defect_class_threshold,
        dimensions=dimensions,
        significant_count=sum(
            len([p for p in dimension.patterns if p.verdict is Verdict.SIGNIFICANT])
            for dimension in dimensions
        ),
    )


def _tested(report: patterns.PatternReport) -> DimensionPatterns:
    """One dimension's computed report in the shape the wire carries."""
    return DimensionPatterns(
        dimension=report.dimension,
        comparable=True,
        not_comparable=None,
        patterns=[_value(pattern) for pattern in report.patterns],
        unattributed=report.unattributed,
    )


def _value(pattern: patterns.Pattern) -> PatternValue:
    comparison = pattern.comparison
    return PatternValue(
        value=pattern.value,
        observed=comparison.observed.successes,
        trials=comparison.observed.trials,
        observed_share=comparison.observed_share,
        expected_share=comparison.expected_share,
        effect_size=comparison.effect_size,
        p_value=comparison.p_value,
        adjusted_p_value=pattern.adjusted_p_value,
        verdict=pattern.verdict,
    )
