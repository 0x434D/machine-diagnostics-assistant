"""The tool set the agent may call: §5.3's fifteen analysis operations, and nothing else.

The agent cannot reach the plant (§4.5) and cannot write anywhere — there is no method here
that does. What it can do is read the analysis service, and the fifteen operations below are
declared once, as data, so that the tool schema the model answers against and the request
the client builds cannot disagree with each other. `tests/test_tools.py` holds them against
the committed OpenAPI contract, which is where §6.11's "neither binding can do anything the
other cannot" becomes checkable rather than asserted.

**No operation takes a time window from the model.** §6.1 step 2 splits deliberately — the
model reads "last night" out of a sentence, code resolves it against the shift calendar —
and a `from`/`to` in a tool schema is where that split would quietly leak back. The client
adds the window it was given to every operation that needs one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class Window:
    """A concrete UTC window, as `/time/resolve` computed it.

    `label` is the calendar's own phrasing of the window and is what the answer quotes back
    — "night shift 2026-09-11 22:00 – 06:00 Europe/Berlin" says which night, where
    "2026-09-11T20:00Z" makes the reader do the shift arithmetic the split exists to avoid.
    `closed` is true when the window has ended; an open window's counts are still moving.
    """

    start: datetime
    end: datetime
    label: str
    closed: bool


@dataclass(frozen=True)
class Param:
    name: str
    json_type: str
    description: str
    required: bool = False
    path: bool = False
    enum: tuple[str, ...] = ()


@dataclass(frozen=True)
class Operation:
    """One analysis endpoint, as both a tool schema and a request template.

    `operation_id` is the OpenAPI operationId rather than a second name for the same thing:
    it is what ties this table to `contracts/analysis.openapi.yaml`, and the tool name is
    the snake_case spelling a model is given.
    """

    name: str
    operation_id: str
    path: str
    description: str
    params: tuple[Param, ...] = ()
    window: bool = False
    binary: bool = False


_FROM = "from"
_TO = "to"

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="resolve_time",
        operation_id="resolveTime",
        path="/time/resolve",
        description=(
            "Resolve a time phrase to a concrete UTC window against the shift calendar. "
            "An expression the calendar does not know is refused with the list it does "
            "know, never guessed at. This reports what a phrase means; it does not change "
            "the window the other tools run over."
        ),
        params=(
            Param(
                name="expression",
                json_type="string",
                description="A time phrase read out of the question, e.g. 'last night'.",
                required=True,
            ),
        ),
    ),
    Operation(
        name="coverage",
        operation_id="coverage",
        path="/coverage",
        description=(
            "Where the data is and where it is not over the window: ingest gaps, the "
            "fraction covered, and how many events were observed at all."
        ),
        window=True,
    ),
    Operation(
        name="list_stops",
        operation_id="listStops",
        path="/stops",
        description=(
            "Every stop in the window with a duration and a category, plus the coverage "
            "that says whether an absence of parts was the line or the gateway."
        ),
        window=True,
    ),
    Operation(
        name="get_stop",
        operation_id="getStop",
        path="/stops/{identifier}",
        description=(
            "One stop, the state timeline around it, and the propagation chain as a "
            "visible derivation with its termination and any unexplained link."
        ),
        params=(
            Param(
                name="identifier",
                json_type="string",
                description="The stop id, from list_stops.",
                required=True,
                path=True,
            ),
        ),
    ),
    Operation(
        name="list_alarms",
        operation_id="listAlarms",
        path="/alarms",
        description=(
            "Every alarm whose life overlaps the window, newest first. A station the line "
            "does not have is refused rather than answered with an empty list."
        ),
        params=(
            Param(
                name="station",
                json_type="string",
                description="Restrict to one station, e.g. 'S2'.",
            ),
        ),
        window=True,
    ),
    Operation(
        name="signal_trend",
        operation_id="signalTrend",
        path="/signals/trend",
        description=(
            "One historised signal over the window, raw or bucketed. A station that "
            "published nothing under this name answers with an empty series, which is a "
            "different answer from a station that does not exist."
        ),
        params=(
            Param(
                name="station",
                json_type="string",
                description="The station, e.g. 'S2'.",
                required=True,
            ),
            Param(
                name="signal",
                json_type="string",
                description="The signal name, e.g. 'JoiningForce'.",
                required=True,
            ),
            Param(
                name="agg",
                json_type="string",
                description="Bucketing. Raw samples by default.",
                enum=("raw", "minute", "hour"),
            ),
        ),
        window=True,
    ),
    Operation(
        name="inspection_stats",
        operation_id="inspectionStats",
        path="/inspection/stats",
        description=(
            "Counts of parts and rejects over the window, the defect-class breakdown with "
            "the score threshold it was counted at, and the ingest gaps that make the "
            "counts incomplete. Optionally grouped."
        ),
        params=(
            Param(
                name="group_by",
                json_type="string",
                description="Group the counts along one dimension.",
                enum=("time", "carrier", "lane", "defect_class"),
            ),
        ),
        window=True,
    ),
    Operation(
        name="inspection_patterns",
        operation_id="inspectionPatterns",
        path="/inspection/patterns",
        description=(
            "Every dimension over the window with each value tested against the rest of "
            "its pool, so a concentration is separated from normal spread."
        ),
        window=True,
    ),
    Operation(
        name="line_status",
        operation_id="lineStatus",
        path="/line/status",
        description=(
            "Each station's last state and reason, buffer levels, standing alarms, the "
            "last part out, and how old all of that is."
        ),
    ),
    Operation(
        name="get_part",
        operation_id="getPart",
        path="/parts/{serial}",
        description=(
            "One assembly's genealogy, what each station recorded, its verdict and its "
            "disposition — keyed by the serial and never reconstructed from timestamps."
        ),
        params=(
            Param(
                name="serial",
                json_type="string",
                description="The assembly serial, e.g. 'A-00000007'.",
                required=True,
                path=True,
            ),
        ),
    ),
    Operation(
        name="get_part_image",
        operation_id="getPartImage",
        path="/parts/{serial}/image",
        description=(
            "Whether a reject's inspection image exists, and how large it is. Rejects "
            "only: a good part has no image and that is not a missing value."
        ),
        params=(
            Param(
                name="serial",
                json_type="string",
                description="The assembly serial.",
                required=True,
                path=True,
            ),
        ),
        binary=True,
    ),
    Operation(
        name="affected_parts",
        operation_id="affectedParts",
        path="/parts/affected",
        description=(
            "The containment scope: which parts a condition touched and where each went. "
            "The criteria conjoin and any may be omitted."
        ),
        params=(
            Param(
                name="station",
                json_type="string",
                description="Parts this station recorded, e.g. 'S3'.",
            ),
            Param(
                name="carrier",
                json_type="integer",
                description="Parts that rode this carrier.",
            ),
            Param(
                name="lot",
                json_type="string",
                description="Parts built from this component lot.",
            ),
            Param(
                name="defect_class",
                json_type="string",
                description="Parts whose inspection reached this class.",
            ),
            Param(
                name="signal",
                json_type="string",
                description="A per-part process value to compare, e.g. 'PeakForce'.",
            ),
            Param(
                name="below",
                json_type="number",
                description="Keep parts whose signal is below this.",
            ),
            Param(
                name="above",
                json_type="number",
                description="Keep parts whose signal is above this.",
            ),
        ),
        window=True,
    ),
    Operation(
        name="lot_parts",
        operation_id="lotParts",
        path="/lots/{lot_code}/parts",
        description="Which assemblies contain a component from this lot.",
        params=(
            Param(
                name="lot_code",
                json_type="string",
                description="The lot code, e.g. 'L-4471'.",
                required=True,
                path=True,
            ),
        ),
    ),
    Operation(
        name="component_assembly",
        operation_id="componentAssembly",
        path="/components/{serial}/assembly",
        description=(
            "Which assembly a single component serial went into, or that it has been read "
            "and not yet built into anything."
        ),
        params=(
            Param(
                name="serial",
                json_type="string",
                description="The component serial.",
                required=True,
                path=True,
            ),
        ),
    ),
    Operation(
        name="carrier_parts",
        operation_id="carrierParts",
        path="/carriers/{carrier_id}/parts",
        description=(
            "Which assemblies rode this carrier. The carriers run in a closed loop, so a "
            "carrier comes round again and this is a list over the window's history."
        ),
        params=(
            Param(
                name="carrier_id",
                json_type="integer",
                description="The carrier number.",
                required=True,
                path=True,
            ),
        ),
    ),
)

OPERATIONS_BY_NAME: Mapping[str, Operation] = {
    operation.name: operation for operation in OPERATIONS
}


def _schema(operation: Operation) -> dict[str, object]:
    properties: dict[str, object] = {}
    required: list[str] = []
    for param in operation.params:
        declared: dict[str, object] = {
            "type": param.json_type,
            "description": param.description,
        }
        if param.enum:
            declared["enum"] = list(param.enum)
        properties[param.name] = declared
        if param.required:
            required.append(param.name)
    schema: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "name": operation.name,
        "description": operation.description,
        "input_schema": _schema(operation),
    }
    for operation in OPERATIONS
]


def _error(detail: str, status: int | None = None) -> dict[str, object]:
    """§6.8's tool error, in the shape that goes back to the model as a tool result.

    A dict rather than an exception because this is not a fault: a refused argument, an
    invented tool name and a 422 from the service are all things the model can correct on
    its next turn, and the only way it can correct them is by being told.
    """
    result: dict[str, object] = {"error": True, "detail": detail}
    if status is not None:
        result["status"] = status
    return result


def _instant(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


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

    async def resolve_time(self, expression: str) -> Window | None:
        """§6.1 step 2's code half. `None` means the calendar does not know the phrase —
        by design, not by failure — and the caller's job is then to say what it assumed
        instead of substituting a window nobody asked for."""
        response = await self._get("/time/resolve", {"expression": expression})
        if response.status_code == 422:
            return None
        response.raise_for_status()
        body = cast(dict[str, object], response.json())
        window = cast(dict[str, str], body["window"])
        return Window(
            start=datetime.fromisoformat(window["from_ts"]),
            end=datetime.fromisoformat(window["to_ts"]),
            label=str(body["label"]),
            closed=bool(body["closed"]),
        )

    async def coverage(self, start: datetime, end: datetime) -> dict[str, object]:
        """§6.1 step 3. Its own call, because it is a guard rather than something the
        model may decide it does not need."""
        response = await self._get(
            "/coverage", {_FROM: _instant(start), _TO: _instant(end)}
        )
        response.raise_for_status()
        return cast(dict[str, object], response.json())

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
        start: datetime,
        end: datetime,
    ) -> dict[str, object]:
        """One tool call from the model, as a result the model can read either way.

        Everything the model got wrong — a tool that does not exist, an argument that does
        not, a missing required one, a refusal from the service — comes back as a result
        (§6.8). What does not come back as a result is a transport failure: that is not
        something this function can recover from, so it propagates to the loop, which is
        where the recovery is.
        """
        operation = OPERATIONS_BY_NAME.get(name)
        if operation is None:
            return _error(
                f"no tool named {name!r}; available: "
                f"{', '.join(sorted(OPERATIONS_BY_NAME))}"
            )

        declared = {param.name: param for param in operation.params}
        unknown = sorted(set(arguments) - set(declared))
        if unknown:
            # Dropping it would turn "parts on carrier 7" into "every part", which is a
            # wrong answer the model has no way to notice it caused.
            return _error(
                f"{name} has no argument(s) {unknown}; it takes "
                f"{sorted(declared) or 'none'}"
            )

        path = operation.path
        params: dict[str, str] = {}
        for param in operation.params:
            value = arguments.get(param.name)
            if value is None:
                if param.required:
                    return _error(f"{name} requires {param.name!r}")
                continue
            if param.path:
                path = path.replace(f"{{{param.name}}}", quote(str(value), safe=""))
            else:
                params[param.name] = str(value)

        if operation.window:
            params[_FROM] = _instant(start)
            params[_TO] = _instant(end)

        response = await self._get(path, params)
        if response.status_code >= 400:
            return _error(_detail(response), response.status_code)
        if operation.binary:
            # A model has no use for PNG bytes and they are not cheap to carry. §7.3
            # renders the image in the evidence panel from the citation; the model needs
            # to know only that there is one.
            return {
                "media_type": response.headers.get("content-type", ""),
                "bytes": len(response.content),
            }
        return cast(dict[str, object], response.json())

    async def exists(
        self, name: str, arguments: Mapping[str, object], window: Window
    ) -> bool:
        """§6.5's resolution step: does this id open?

        **404 and 422 mean "no"; anything else is a fault and propagates.** That asymmetry
        with `call` above is the whole point of a second method. `call` turns every error
        into something the model can read, which is right when the model can work around
        it — but verification cannot work around anything, and a 500 read as "does not
        resolve" would silently delete *true* claims during a database hiccup. §6.5's
        removal must be caused by an invented id and by nothing else.
        """
        result = await self.call(name, arguments, window.start, window.end)
        if not result.get("error"):
            return True
        status = result.get("status")
        if status in (404, 422):
            return False
        raise RuntimeError(
            f"resolving {name} {dict(arguments)} failed with {status}: "
            f"{result.get('detail')}"
        )

    async def fetch(
        self, name: str, arguments: Mapping[str, object], window: Window
    ) -> dict[str, object]:
        """A tool result for verification rather than for the model: any error is a fault.

        The kinds whose referent has no endpoint of its own — an alarm id, a pattern cell —
        are resolved by membership in a window's answer, and a window's answer that did not
        arrive is not evidence of absence.
        """
        result = await self.call(name, arguments, window.start, window.end)
        if result.get("error"):
            raise RuntimeError(
                f"resolving {name} {dict(arguments)} failed with "
                f"{result.get('status')}: {result.get('detail')}"
            )
        return result

    async def knowledge_exists(self, document_id: str) -> bool:
        """§5.3's `GET /knowledge/{id}`, which is what makes a `sop` citation openable.

        Not one of the fifteen tools and deliberately: §6.11 exposes the knowledge base as
        a *resource*, and the agent already holds the documents routing gave it. It is
        reachable here because §6.5 has to check that a cited document exists at all.
        """
        response = await self._get(f"/knowledge/{quote(document_id, safe='')}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True


def _detail(response: httpx.Response) -> str:
    """The service's own words, which is the half of an error the model can act on.

    /time/resolve's 422 carries the expressions it *does* understand; a bare status code
    would throw that away and leave the model rephrasing at random.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and "detail" in body:
        return str(cast(dict[str, object], body)["detail"])
    return str(body)
