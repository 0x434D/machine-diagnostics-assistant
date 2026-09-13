import base64

from fastapi.testclient import TestClient
from inspection.app import DEFECT_CLASSES, app
from inspection.render import render_part

client = TestClient(app)


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


def test_inspect_request_carries_no_ground_truth() -> None:
    """§3.4: the classifier resolves truth through a side channel keyed by part id,
    NEVER through the request. A real model would ignore the side channel and its
    signature would not change. (The request body's actual shape -- that it never
    mentions truth -- is pinned more directly in simulator/tests/test_inspection_client.py,
    which inspects the serialised bytes the wiring actually sends.)"""
    client.post("/truth/A-00000001", json={"defects": ["crack"]})
    image = render_part("A-00000001", ["crack"], 64, 64, seed=1, compress_level=1)

    result = client.post(
        "/inspect",
        json={
            "part_id": "A-00000001",
            "image_b64": base64.b64encode(image).decode(),
            "carrier_id": 3,
        },
    ).json()
    assert result["disposition"] == "reject"
    assert result["defect_class"] == "crack"


def test_a_part_with_no_truth_entry_passes() -> None:
    image = render_part("A-00000002", [], 64, 64, seed=1, compress_level=1)
    result = client.post(
        "/inspect",
        json={
            "part_id": "A-00000002",
            "image_b64": base64.b64encode(image).decode(),
            "carrier_id": 1,
        },
    ).json()
    assert result["disposition"] == "good"


def test_confidences_are_independent_per_class_scores() -> None:
    """§3.4 (amended): per-class scores are independent, not a distribution -- a
    real optics-fouling scenario needs every class's score able to fall together,
    which no normalisation over six values could express."""
    client.post("/truth/A-00000003", json={"defects": ["scratch"]})
    image = render_part("A-00000003", ["scratch"], 64, 64, seed=1, compress_level=1)
    result = client.post(
        "/inspect",
        json={
            "part_id": "A-00000003",
            "image_b64": base64.b64encode(image).decode(),
            "carrier_id": 1,
        },
    ).json()

    conf = result["confidences"]
    assert set(conf) == set(DEFECT_CLASSES)
    assert all(0.0 <= v <= 1.0 for v in conf.values())
    assert max(conf, key=conf.get) == "scratch"


def test_a_good_part_scores_low_on_every_class() -> None:
    """§3.4 (amended): a good part simply scores low on all six classes and is
    confidently OK -- not, as a normalised reading would force, confidently OK
    while also claiming a third of one class."""
    image = render_part("A-00000004", [], 64, 64, seed=1, compress_level=1)
    result = client.post(
        "/inspect",
        json={
            "part_id": "A-00000004",
            "image_b64": base64.b64encode(image).decode(),
            "carrier_id": 1,
        },
    ).json()

    assert result["disposition"] == "good"
    assert all(v < 0.5 for v in result["confidences"].values())
