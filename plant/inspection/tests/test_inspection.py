import base64
import statistics

from fastapi.testclient import TestClient
from inspection.app import DEFECT_CLASSES, app
from inspection.classifier import (
    InspectionResult,
    PartContext,
    SimulatedClassifier,
    TruthChannel,
    contrast_of,
)
from inspection.config import Settings
from inspection.render import render_part

client = TestClient(app)

# A part id and an image the direct classifier tests below can share. The image is real
# rather than a few bytes because `SimulatedClassifier` seeds from its length, and a
# stand-in would make these tests exercise a draw the service never makes.
PART = "C-1-00000042"
IMAGE = render_part(PART, [], 64, 64, seed=1, compress_level=1)


def _faithful(truth: TruthChannel) -> SimulatedClassifier:
    """A classifier with D7's two rates off, for the claims that are about what the
    classifier does when it is right. The rates have their own tests below."""
    return SimulatedClassifier(truth, seed=Settings().seed)


def _classify(classifier: SimulatedClassifier, part_id: str = PART) -> InspectionResult:
    return classifier.classify(IMAGE, PartContext(part_id, carrier_id=1))


def test_defect_classes_match_the_spec() -> None:
    assert DEFECT_CLASSES == [
        "gap",
        "crack",
        "misalignment",
        "missing_part",
        "scratch",
        "contamination",
    ]


def test_render_is_deterministic_for_a_seed() -> None:
    a = render_part("A-00000042", ["gap"], 320, 240, seed=7, compress_level=1)
    b = render_part("A-00000042", ["gap"], 320, 240, seed=7, compress_level=1)
    assert a == b
    assert a.startswith(b"\x89PNG")


def test_inspect_answers_with_the_whole_verdict_shape() -> None:
    """§3.4's wire contract, over the service as it is actually configured: a verdict,
    the named reason for it, the scalar confidence in that verdict, six independent
    per-class scores, and the model that produced them.

    Asserted as a shape rather than as a particular verdict, because D7's configured
    error rates mean the service's answer for one part is not the truth channel's --
    that is the point of them. What the classifier does when it is right, and what it
    does when it is wrong, are the direct tests below.
    """
    client.post(f"/truth/{PART}", json={"defects": ["crack"]})

    result = client.post(
        "/inspect",
        json={
            "part_id": PART,
            "image_b64": base64.b64encode(IMAGE).decode(),
            "carrier_id": 3,
        },
    ).json()
    assert set(result) == {
        "disposition",
        "defect_class",
        "confidence",
        "confidences",
        "model_version",
    }
    assert result["disposition"] in {"good", "reject"}
    assert set(result["confidences"]) == set(DEFECT_CLASSES)


def test_the_classifier_reports_the_declared_truth_when_it_does_not_err() -> None:
    """§3.4: truth reaches the classifier through the side channel keyed by part id,
    never through the request. A real model would ignore that channel and its signature
    would not change. (The request body's actual shape -- that it never mentions truth
    -- is pinned in simulator/tests/test_inspection_client.py, against the serialised
    bytes the wiring sends.)"""
    truth = TruthChannel()
    truth.declare(PART, ["crack"])
    result = _classify(_faithful(truth))
    assert result.disposition == "reject"
    assert result.defect_class == "crack"


def test_the_error_rates_are_off_unless_they_are_configured_on() -> None:
    """D7 puts false accepts and false rejects on the classifier. A default that
    invented them would mean every test and every stub in this repository ran against
    a classifier that lies at an unstated rate."""
    truth = TruthChannel()
    truth.declare(PART, ["gap"])
    plain = SimulatedClassifier(truth, seed=Settings().seed)
    assert plain.classify(IMAGE, PartContext(PART, 1)).disposition == "reject"


def test_a_false_accept_reports_good_and_scores_low_on_every_class() -> None:
    """A model that misses a defect does not report it *and* score it high -- it did
    not see it. The error is in the evidence, which is what makes the vector and the
    verdict always agree and leaves the truth channel the only thing that knows which
    of them was wrong."""
    truth = TruthChannel()
    truth.declare(PART, ["gap"])
    result = _classify(
        SimulatedClassifier(truth, seed=Settings().seed, false_accept_rate=1.0)
    )
    assert result.disposition == "good"
    assert result.defect_class is None
    assert all(score < 0.5 for score in result.confidences.values())


def test_a_false_reject_names_a_class_that_is_not_there() -> None:
    """The mirror: a good part reported defective scores high on the class the model
    thought it saw. §3.5 counts both as line behaviour, and §3.6's ground truth is
    what will score them."""
    result = _classify(
        SimulatedClassifier(TruthChannel(), seed=Settings().seed, false_reject_rate=1.0)
    )
    assert result.disposition == "reject"
    assert result.defect_class in DEFECT_CLASSES
    assert result.confidences[result.defect_class] > 0.5


def test_a_part_with_no_truth_entry_passes() -> None:
    """A part nobody declared anything about is a good part -- which is what makes the
    side channel additive rather than mandatory."""
    assert _classify(_faithful(TruthChannel())).disposition == "good"


def test_confidences_are_independent_per_class_scores() -> None:
    """§3.4 (amended): per-class scores are independent, not a distribution -- a
    real optics-fouling scenario needs every class's score able to fall together,
    which no normalisation over six values could express."""
    truth = TruthChannel()
    truth.declare(PART, ["scratch"])
    conf = _classify(_faithful(truth)).confidences

    assert set(conf) == set(DEFECT_CLASSES)
    assert all(0.0 <= v <= 1.0 for v in conf.values())
    assert max(conf, key=lambda name: conf[name]) == "scratch"
    assert sum(conf.values()) != 1.0


def test_two_classes_can_score_high_on_one_part() -> None:
    """§3.5 scenarios 4 and 5 each put two defects on one part, and pattern DP-02 is
    keyed on the pair. Mutually exclusive classes cannot express it, and neither can a
    distribution -- this is the shape the whole vector exists for."""
    truth = TruthChannel()
    truth.declare(PART, ["misalignment", "scratch"])
    conf = _classify(_faithful(truth)).confidences
    assert conf["misalignment"] > 0.5
    assert conf["scratch"] > 0.5


def test_a_good_part_scores_low_on_every_class_and_is_confidently_good() -> None:
    """§3.4 (amended): a good part simply scores low on all six classes and is
    confidently OK -- not, as a normalised reading would force, confidently OK
    while also claiming a third of one class. The recorded defect is exactly that
    reading: 27 % confident and ~30 % misaligned, on a part with nothing wrong."""
    result = _classify(_faithful(TruthChannel()))

    assert result.disposition == "good"
    assert all(v < 0.5 for v in result.confidences.values())
    assert result.confidence > 0.5


# --- D8: the confidence comes off the image -------------------------------------------


def _clean_frames(count: int, clarity: float = 1.0) -> list[bytes]:
    """`count` frames of good parts at the shipped image settings, so the contrast below
    is the one the plant actually renders rather than one at a test resolution."""
    settings = Settings()
    return [
        render_part(
            f"A-{index:08d}",
            [],
            320,
            240,
            seed=settings.seed,
            compress_level=1,
            clarity=clarity,
        )
        for index in range(count)
    ]


def test_the_configured_reference_contrast_is_what_a_clean_lens_renders() -> None:
    """`reference_contrast` is what every confidence is divided by, so a change to
    `render.py` that left it behind would scale every verdict in the plant by a constant
    and nothing would say so.

    Measured here rather than asserted from memory: 200 clean frames, mean 66.364 grey
    levels with a standard deviation of 0.016 -- the geometry is identical every time and
    only the seeded sensor noise moves, which is why the tolerance can be this tight.
    """
    measured = statistics.fmean(contrast_of(frame) for frame in _clean_frames(60))
    configured = Settings().reference_contrast
    assert abs(measured - configured) < 0.5, (
        f"a clean frame renders {measured:.3f} grey levels of contrast and "
        f"reference_contrast says {configured}: every confidence the service reports is "
        "being divided by the wrong number"
    )


def test_a_fouled_frame_decays_the_verdict_and_every_class_together() -> None:
    """**D8, and §3.5's row 6.** The decay is caused rather than declared: the frame
    carries less contrast and the classifier reads it off the pixels, with no knob and no
    sight of the truth channel or the fault.

    All six classes and the verdict fall **together**, which is the shape §3.4's
    independent scores exist to allow -- six values that must sum to 1 cannot all fall.

    Measured over 120 good parts at the fouling scenario's own 0.55: the verdict
    confidence goes 0.9303 -> 0.5150 and the mean class score 0.0452 -> 0.0249, both
    0.55 of where they started.

    The honest limit, stated where it belongs: contrast -> clarity -> confidence is still
    a formula. It is one layer below the knob it replaces, not nowhere.
    """
    settings = Settings()
    classifier = SimulatedClassifier(
        TruthChannel(),
        seed=settings.seed,
        reference_contrast=settings.reference_contrast,
    )

    def readings(clarity: float) -> tuple[float, float]:
        results = [
            classifier.classify(frame, PartContext(f"A-{index:08d}", carrier_id=1))
            for index, frame in enumerate(_clean_frames(60, clarity))
        ]
        return (
            statistics.fmean(r.confidence for r in results),
            statistics.fmean(statistics.fmean(r.confidences.values()) for r in results),
        )

    clarity = 0.55
    clean_confidence, clean_scores = readings(1.0)
    fouled_confidence, fouled_scores = readings(clarity)

    assert abs(fouled_confidence / clean_confidence - clarity) < 0.05
    assert abs(fouled_scores / clean_scores - clarity) < 0.05


def test_a_fouled_lens_costs_certainty_and_not_the_verdict() -> None:
    """§3.5's row 6 has two halves and this is the second: the scrap rate stays flat.

    What a fouled lens takes away is certainty. The disposition still comes from the
    truth channel and D7's two error rates, so a run under scenario 6 rejects exactly the
    parts it would have rejected with a clean lens -- which is what separates fouling
    from anything that damages parts, and what makes the pair of consequences a test
    rather than one observation stated twice.
    """
    settings = Settings()
    truth = TruthChannel()
    defective = {f"A-{index:08d}" for index in range(0, 60, 7)}
    for part_id in defective:
        truth.declare(part_id, ["scratch"])
    classifier = SimulatedClassifier(
        truth, seed=settings.seed, reference_contrast=settings.reference_contrast
    )

    def dispositions(clarity: float) -> list[str]:
        return [
            classifier.classify(
                render_part(
                    f"A-{index:08d}",
                    ["scratch"] if f"A-{index:08d}" in defective else [],
                    320,
                    240,
                    seed=settings.seed,
                    compress_level=1,
                    clarity=clarity,
                ),
                PartContext(f"A-{index:08d}", carrier_id=1),
            ).disposition
            for index in range(60)
        ]

    assert dispositions(0.55) == dispositions(1.0)
    assert set(dispositions(1.0)) == {"good", "reject"}
