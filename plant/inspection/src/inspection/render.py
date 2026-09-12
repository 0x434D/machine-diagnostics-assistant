"""The simulator renders the part image -- it knows the geometry and the defects (§3.4).

Deliberately duplicated, byte-for-byte, between `inspection.render` and `simulator.render`
rather than shared: the two are separate uv workspace members that must not import each
other (§10.7 -- the same argument that makes two lockfiles correct rather than wasteful
applies here to ~70 lines of code). If `render_part` changes, change both copies; each
package's test suite pins that the two stay identical.

§3.6's byte-identity claim also depends on both packages pinning the exact same pillow
version: the claim is about this module's own drawing and noise logic, but the PNG encoder
pillow ships can itself change its output across versions for identical pixel data.
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

# Blend weight for the sensor-noise layer below: subtle enough that the drawn geometry
# stays legible, strong enough that two renders never compress to the same bytes by
# accident. Not §10.3 configuration -- a fixed rendering detail, like BODY/BACKGROUND/
# DEFECT_INK above, rather than a physical or measured parameter.
_NOISE_ALPHA = 0.06


def render_part(
    part_id: str,
    defects: list[str],
    width: int,
    height: int,
    seed: int,
    compress_level: int,
) -> bytes:
    """Render a PNG of the part at `part_id` with `defects` painted onto it.

    Deterministic for a given (part_id, defects, width, height, seed) -- §3.6
    requires the whole plant to be reproducible from a seed. Raises `KeyError` if
    `defects` names a class outside `DEFECT_INK`.
    """
    rng = random.Random(f"{seed}:{part_id}")
    img = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(img)

    # two joined components, which is what S2 pressed together
    mid = width // 2
    draw.rectangle([mid - width // 3, height // 4, mid, 3 * height // 4], fill=BODY)
    draw.rectangle([mid, height // 4, mid + width // 3, 3 * height // 4], fill=BODY)

    for defect in defects:
        ink = DEFECT_INK[defect]
        x = rng.randint(width // 4, 3 * width // 4)
        y = rng.randint(height // 3, 2 * height // 3)
        if defect == "scratch":
            draw.line(
                [x, y, x + rng.randint(20, 60), y + rng.randint(-8, 8)],
                fill=ink,
                width=2,
            )
        elif defect == "gap":
            # Positioned at the RNG's x, not a fixed offset from mid: a gap fixed at
            # exactly `mid` renders identically for every part_id, which is the
            # degeneracy measurements/r4-image-sizes.txt's first attempt hit.
            draw.rectangle([x - 3, height // 4, x + 3, 3 * height // 4], fill=ink)
        elif defect == "missing_part":
            # A band around the RNG's y, not the whole second component: the same
            # fixed-geometry degeneracy as `gap` above, for the same reason.
            band = height // 12
            draw.rectangle(
                [
                    mid,
                    max(height // 4, y - band),
                    mid + width // 3,
                    min(3 * height // 4, y + band),
                ],
                fill=ink,
            )
        else:
            r = rng.randint(6, 18)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=ink)

    # Deterministic sensor noise -- image content this renderer owns (§3.4), not
    # §3.5's noise floor (M2 line behaviour on the *classifier's* verdict): a
    # separate RNG stream, keyed ":noise", so the two never share a knob. Built from
    # bulk random bytes plus one C-level blend rather than a per-pixel Python loop,
    # which the catch-up wall budget (measurements/r4-image-sizes.txt) does not have
    # room for at production part counts.
    noise_rng = random.Random(f"{seed}:{part_id}:noise")
    noise = Image.frombytes("L", (width, height), noise_rng.randbytes(width * height))
    img = Image.blend(img, noise.convert("RGB"), _NOISE_ALPHA)

    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=compress_level)
    return buf.getvalue()
