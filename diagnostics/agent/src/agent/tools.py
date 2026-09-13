"""The tool set the agent may call. In M1 that is the analysis service's one tool plus the
resolver a citation needs, and nothing else — the agent cannot reach the plant (§4.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import httpx

TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "name": "inspection_stats",
        "description": (
            "Counts of parts and rejects over a closed time window, broken down by defect "
            "class, with the ingest gaps that make the window incomplete."
        ),
        "input_schema": {"type": "object", "properties": {}},
    }
]


class AnalysisClient:
    """The only thing the agent can reach. Read-only by construction: there is no method
    here that writes anything anywhere."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def _get(
        self, path: str, params: dict[str, str] | None = None
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(f"{self._base_url}{path}", params=params)
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.get(f"{self._base_url}{path}", params=params)

    async def inspection_stats(
        self, start: datetime, end: datetime
    ) -> dict[str, object]:
        response = await self._get(
            "/inspection/stats",
            {
                "from": start.isoformat().replace("+00:00", "Z"),
                "to": end.isoformat().replace("+00:00", "Z"),
            },
        )
        response.raise_for_status()
        return cast(dict[str, object], response.json())

    async def part_exists(self, serial: str) -> bool:
        """§6.5's resolution step. 404 and 200 are the whole answer; anything else is a
        fault and is not treated as "the id does not resolve"."""
        response = await self._get(f"/parts/{serial}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True
