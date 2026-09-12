import base64
import json

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
    a = render_part("A-00000042", ["gap"], 640, 480, seed=7)
    b = render_part("A-00000042", ["gap"], 640, 480, seed=7)
    assert a == b
    assert a.startswith(b"\x89PNG")


def test_inspect_request_carries_no_ground_truth() -> None:
    """§3.4: the classifier resolves truth through a side channel keyed by part id,
    NEVER through the request. A real model would ignore the side channel and its
    signature would not change -- so the request body must not mention truth."""
    client.post("/truth/A-00000001", json={"defects": ["crack"]})
    image = render_part("A-00000001", ["crack"], 64, 64, seed=1)

    sent = {"part_id": "A-00000001", "image_b64": "", "carrier_id": 3}
    assert "defect" not in json.dumps(sent)
    assert "truth" not in json.dumps(sent)

    sent["image_b64"] = base64.b64encode(image).decode()
    result = client.post("/inspect", json=sent).json()
    assert result["disposition"] == "reject"
    assert result["defect_class"] == "crack"


def test_a_part_with_no_truth_entry_passes() -> None:
    image = render_part("A-00000002", [], 64, 64, seed=1)
    result = client.post(
        "/inspect",
        json={
            "part_id": "A-00000002",
            "image_b64": base64.b64encode(image).decode(),
            "carrier_id": 1,
        },
    ).json()
    assert result["disposition"] == "good"


def test_confidences_are_a_distribution_over_all_classes() -> None:
    """§3.4: plausible per-class confidence distributions, so the confidence field
    carries information."""
    client.post("/truth/A-00000003", json={"defects": ["scratch"]})
    image = render_part("A-00000003", ["scratch"], 64, 64, seed=1)
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
    assert abs(sum(conf.values()) - 1.0) < 1e-6
    assert max(conf, key=conf.get) == "scratch"
