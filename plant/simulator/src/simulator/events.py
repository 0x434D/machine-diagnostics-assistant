"""The inspection event's field shape.

Kept apart from address_space.py: the fields are what Task 5's inspection
service and the gateway both need to agree on, independent of how the address
space wires the event type into the server.
"""

from __future__ import annotations

from asyncua import ua

EVENT_FIELDS: tuple[tuple[str, ua.VariantType], ...] = (
    ("AssemblySerial", ua.VariantType.String),
    ("Disposition", ua.VariantType.String),
    ("DefectClass", ua.VariantType.String),
    ("Confidence", ua.VariantType.Double),
    ("ModelVersion", ua.VariantType.String),
    ("Image", ua.VariantType.ByteString),
)
