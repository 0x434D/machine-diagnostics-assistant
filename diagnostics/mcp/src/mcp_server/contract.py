"""`contracts/analysis.openapi.yaml`, read as the definition both bindings come from.

§6.11: *"The OpenAPI spec defines the capabilities; the frontend binds over REST, agent
runtimes bind over MCP, both generated from one source. A test asserts that the MCP tool
list and the OpenAPI operation set derive from the same definition. Neither binding can do
anything the other cannot."*

So there is no list of tool names in this package. An operation exists as a tool because it
is in the contract, and it is described to a calling model with the same prose the endpoint
documents itself with — which means an endpoint whose docstring improves improves the tool
description too, without anyone remembering to copy it.

**Read-only is enforced here rather than promised.** §6.11 exposes the analysis queries and
nothing that writes, and there is deliberately no plant-side MCP at all: operating the plant
agentically would let the diagnostics side know what it broke and destroy the evaluation. A
contract operation that is not a GET therefore raises at load, before the server starts,
instead of quietly becoming a tool that writes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote

import yaml

DEFAULT_CONTRACT: Path = (
    Path(__file__).resolve().parents[4] / "contracts" / "analysis.openapi.yaml"
)
"""Where the contract lives: `contracts/` at the repository root (§10.1).

`Settings.contract` is the deployment's dial for it; the container lays the file down and
says so in `diagnostics/Dockerfile` rather than relying on this arithmetic landing right.
"""

READ_ONLY_METHOD = "get"
"""The only HTTP method §6.11 permits a tool to be generated from."""

ParameterLocation = Literal["path", "query"]


class ContractError(Exception):
    """The contract says something this generator will not turn into a tool.

    Raised rather than logged, and at load rather than at call: a capability that silently
    fails to appear is indistinguishable, from outside, from one that was never specified.
    """


@dataclass(frozen=True)
class Parameter:
    """One operation parameter, with where it goes in the request."""

    name: str
    location: ParameterLocation
    required: bool
    schema: Mapping[str, object]
    description: str | None = None


@dataclass(frozen=True)
class Operation:
    """One contract operation, as both an MCP tool and an HTTP request.

    `name` is the OpenAPI `operationId` unchanged. Keeping them the same string is what
    makes the parity assertion a set comparison rather than a mapping that could itself be
    wrong.
    """

    name: str
    path: str
    description: str
    parameters: tuple[Parameter, ...]
    media_type: str

    @property
    def input_schema(self) -> dict[str, object]:
        """The tool's JSON Schema, assembled from the operation's own parameter schemas.

        The parameter schemas are copied through as the contract states them — including
        `format: date-time` and the enums — so a model calling this tool is told exactly
        what the REST endpoint would have told a frontend.
        """
        properties: dict[str, object] = {}
        required: list[str] = []
        for parameter in self.parameters:
            schema = dict(parameter.schema)
            # OpenAPI's generated `title` is the parameter name in title case and carries
            # nothing a model can use; the endpoint's own prose does, when there is any.
            schema.pop("title", None)
            if parameter.description:
                schema["description"] = parameter.description
            properties[parameter.name] = schema
            if parameter.required:
                required.append(parameter.name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            # A misspelled argument is a different answer, not a smaller one: `station` for
            # `signal` would silently widen the window instead of failing.
            "additionalProperties": False,
        }

    def url_for(self, arguments: Mapping[str, object]) -> tuple[str, dict[str, str]]:
        """This operation's path and query string for one call.

        Raises `ContractError` for an unknown argument or a missing required one, so a
        malformed call fails here rather than reaching the analysis service as a request
        for something else.
        """
        known = {parameter.name for parameter in self.parameters}
        unknown = sorted(set(arguments) - known)
        if unknown:
            raise ContractError(f"{self.name}: unknown argument(s) {unknown}")

        path = self.path
        query: dict[str, str] = {}
        for parameter in self.parameters:
            if parameter.name not in arguments:
                if parameter.required:
                    raise ContractError(f"{self.name}: {parameter.name} is required")
                continue
            value = arguments[parameter.name]
            if value is None:
                # An explicit null is the caller saying "leave this out", which is what
                # omitting an optional query parameter means to the endpoint.
                continue
            rendered = _render(self.name, parameter.name, value)
            if parameter.location == "path":
                path = path.replace(f"{{{parameter.name}}}", quote(rendered, safe=""))
            else:
                query[parameter.name] = rendered
        return path, query


def load(path: Path) -> tuple[Operation, ...]:
    """Every operation in the contract, in the order a tool list should show them.

    Raises `ContractError` if the document is malformed or if any operation is not a GET.
    """
    document = _mapping(yaml.safe_load(path.read_text()), "the contract")
    paths = _mapping(document.get("paths"), "paths")

    operations: list[Operation] = []
    for route in sorted(paths):
        item = _mapping(paths[route], route)
        for method in sorted(item):
            if method != READ_ONLY_METHOD:
                raise ContractError(
                    f"{method.upper()} {route}: §6.11 exposes the analysis queries and "
                    "nothing that writes, so only GET becomes a tool"
                )
            operations.append(_operation(_mapping(item[method], route), route))

    names = [operation.name for operation in operations]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ContractError(f"duplicate operationId(s) {duplicates}")
    return tuple(operations)


def _operation(body: Mapping[str, object], route: str) -> Operation:
    name = body.get("operationId")
    if not isinstance(name, str) or not name:
        # FastAPI generates one from the function name when the route does not set it, so
        # an operation without one means the contract was not generated by `make contract`.
        raise ContractError(f"GET {route}: no operationId")

    return Operation(
        name=name,
        path=route,
        description=_description(body, route),
        parameters=tuple(
            _parameter(_mapping(entry, f"{name} parameter"), name)
            for entry in _sequence(body.get("parameters", []), f"{name} parameters")
        ),
        media_type=_media_type(body, name),
    )


def _parameter(body: Mapping[str, object], operation: str) -> Parameter:
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise ContractError(f"{operation}: a parameter with no name")

    location = body.get("in")
    if location not in ("path", "query"):
        # Header and cookie parameters are a different kind of thing from an argument a
        # model supplies, and nothing in §5.3 has one. Refused rather than ignored.
        raise ContractError(
            f"{operation}.{name}: unsupported parameter location {location!r}"
        )

    description = body.get("description")
    return Parameter(
        name=name,
        location=location,
        required=body.get("required") is True,
        schema=_mapping(body.get("schema", {}), f"{operation}.{name} schema"),
        description=description if isinstance(description, str) else None,
    )


def _description(body: Mapping[str, object], route: str) -> str:
    """What the endpoint says about itself, as what a calling model is told.

    The summary is FastAPI's title-cased function name and carries nothing; the description
    is the handler's docstring, which is where the semantics live — the half-open window,
    the fact that the defect breakdown is not a partition, which ids resolve. A tool whose
    description omits that is a tool a model will use confidently and wrongly.
    """
    description = body.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    summary = body.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    raise ContractError(f"GET {route}: no description and no summary")


def _media_type(body: Mapping[str, object], operation: str) -> str:
    """What a 200 returns, read from the contract rather than assumed to be JSON.

    `/parts/{serial}/image` returns `image/png`, and a tool that handed a model a PNG as
    text would be handing it noise.
    """
    responses = _mapping(body.get("responses"), f"{operation} responses")
    ok = _mapping(responses.get("200"), f"{operation} 200 response")
    content = _mapping(ok.get("content"), f"{operation} 200 content")
    media_types = sorted(content)
    if len(media_types) != 1:
        raise ContractError(
            f"{operation}: a 200 with {len(media_types)} media types is ambiguous for a tool"
        )
    return media_types[0]


def _render(operation: str, parameter: str, value: object) -> str:
    if isinstance(value, bool):
        # `str(True)` is "True", which no query parser reads as true. Handled before the
        # int branch below, because bool is a subclass of int.
        return "true" if value else "false"
    if isinstance(value, str | int | float):
        return str(value)
    raise ContractError(
        f"{operation}.{parameter}: {type(value).__name__} is not something a query "
        "string can carry"
    )


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ContractError(f"{what} is not a mapping")
    for key in value:
        if not isinstance(key, str):
            raise ContractError(f"{what}: key {key!r} is not a name")
    return cast(Mapping[str, object], value)


def _sequence(value: object, what: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ContractError(f"{what} is not a list")
    return cast(Sequence[object], value)
