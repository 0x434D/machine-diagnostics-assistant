"""The simulator renders the part image -- it knows the geometry and the defects (§3.4).

This is a deliberate duplicate of `inspection.render`, not a shared helper: `simulator`
and `inspection` are separate uv workspace members that must not import each other
(§10.7 -- the same argument that makes two lockfiles correct rather than wasteful applies
here to ~40 lines of code). The inspection service keeps its own copy for its own tests;
if `render_part` ever changes, change both copies.
"""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw

BODY = (172, 176, 182)
BACKGROUND = (24, 26, 30)
DEFECT_INK = {
    "gap": (250, 90, 60),
    "crack": (255, 210, 60),
    "misalignment": (90, 170, 255),
    "missing_part": (40, 40, 48),
    "scratch": (235, 235, 245),
    "contamination": (120, 200, 120),
}


def render_part(
    part_id: str, defects: list[str], width: int, height: int, seed: int
) -> bytes:
    """Render a PNG of the part at `part_id` with `defects` painted onto it.

    Deterministic for a given (part_id, defects, width, height, seed) -- §3.6
    requires the whole plant to be reproducible from a seed.
    """
    rng = random.Random(f"{seed}:{part_id}")
    img = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(img)

    # two joined components, which is what S2 pressed together
    mid = width // 2
    draw.rectangle([mid - width // 3, height // 4, mid, 3 * height // 4], fill=BODY)
    draw.rectangle([mid, height // 4, mid + width // 3, 3 * height // 4], fill=BODY)

    for defect in defects:
        ink = DEFECT_INK.get(defect, (255, 0, 255))
        x = rng.randint(width // 4, 3 * width // 4)
        y = rng.randint(height // 3, 2 * height // 3)
        if defect == "scratch":
            draw.line(
                [x, y, x + rng.randint(20, 60), y + rng.randint(-8, 8)],
                fill=ink,
                width=2,
            )
        elif defect == "gap":
            draw.rectangle([mid - 3, height // 4, mid + 3, 3 * height // 4], fill=ink)
        elif defect == "missing_part":
            draw.rectangle(
                [mid, height // 4, mid + width // 3, 3 * height // 4], fill=ink
            )
        else:
            r = rng.randint(6, 18)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=ink)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
