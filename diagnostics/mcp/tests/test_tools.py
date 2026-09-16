"""What the generated tools put on the wire, and what they send to the analysis service.

The requests are driven through `httpx.MockTransport` rather than a live service: what these
assert is the URL this binding builds from a tool call, which is where a binding goes wrong
silently. The queries behind those URLs are tested against real Postgres in the analysis
package, and testing them again through a second transport would test the database twice.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import mcp_types as types
import pytest
import yaml
from mcp_server import contract
from mcp_server.config import Settings
from mcp_server.tools import AnalysisTools

BASE = "http://analysis.test"


def tools_over(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response]
) -> AnalysisTools:
    return AnalysisTools(
        contract.load(settings.contract),
        BASE,
        settings.analysis_timeout_seconds,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def echoing(
    status: int = 200, body: object | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    """Answers anything, and hands the request back so the test can assert on the URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json=body if body is not None else {"url": str(request.url)},
        )

    return handler


async def test_a_path_parameter_lands_in_the_path(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await tools_over(settings, handler).call("getPart", {"serial": "A-00000007"})

    assert seen[0].url.path == "/parts/A-00000007"


async def test_a_serial_that_looks_like_a_path_cannot_become_one(
    settings: Settings,
) -> None:
    """A path parameter is escaped, so a serial cannot reach a different endpoint.

    §5.3's serials are opaque strings the caller supplies, and a model that has been told
    a serial by a user is exactly where a `../` arrives.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await tools_over(settings, handler).call("getPart", {"serial": "../line/status"})

    # `raw_path`, because `url.path` hands back the decoded form and would read as though
    # the escaping had not happened.
    assert seen[0].url.raw_path == b"/parts/..%2Fline%2Fstatus"


async def test_query_parameters_land_in_the_query(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await tools_over(settings, handler).call(
        "inspectionStats",
        {
            "from": "2026-09-16T06:00:00Z",
            "to": "2026-09-16T14:00:00Z",
            "group_by": "carrier",
        },
    )

    assert dict(seen[0].url.params) == {
        "from": "2026-09-16T06:00:00Z",
        "to": "2026-09-16T14:00:00Z",
        "group_by": "carrier",
    }


async def test_an_omitted_optional_parameter_is_simply_absent(
    settings: Settings,
) -> None:
    """Not sent as an empty string, which the endpoint would have to interpret."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await tools_over(settings, handler).call(
        "listAlarms", {"from": "2026-09-16T06:00:00Z", "to": "2026-09-16T14:00:00Z"}
    )

    assert "station" not in dict(seen[0].url.params)


async def test_a_missing_required_parameter_never_reaches_the_service(
    settings: Settings,
) -> None:
    """A window is not optional, and a call without one is not a smaller question.

    Refused here rather than sent, because the endpoint's 422 would come back as a tool
    error the model might read as "no data for that window".
    """
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        del request
        called = True
        return httpx.Response(200, json={})

    with pytest.raises(contract.ContractError, match="to is required"):
        await tools_over(settings, handler).call(
            "coverage", {"from": "2026-09-16T06:00:00Z"}
        )

    assert not called


async def test_an_unknown_argument_is_refused(settings: Settings) -> None:
    """`station` where `signal` was meant would silently answer a different question."""
    with pytest.raises(contract.ContractError, match="unknown argument"):
        await tools_over(settings, echoing()).call("lineStatus", {"station": "S2"})


async def test_a_json_result_carries_the_same_object_the_rest_binding_returns(
    settings: Settings,
) -> None:
    """Both bindings reach the same capability, which includes the shape of the answer."""
    body = {"stations": [{"id": "S2", "state": "stopped"}]}
    result = await tools_over(settings, echoing(body=body)).call("lineStatus", {})

    assert result.structured_content == body
    assert (
        json.loads(
            result.content[0].text
            if isinstance(result.content[0], types.TextContent)
            else "{}"
        )
        == body
    )
    assert result.is_error is False


async def test_a_png_comes_back_as_an_image_and_not_as_text(
    settings: Settings,
) -> None:
    """`/parts/{serial}/image` is the one operation whose 200 is not JSON.

    The media type is read off the contract, so this branch exists because the contract
    says so rather than because someone remembered this endpoint.
    """
    pixel = b"\x89PNG\r\n\x1a\n"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=pixel, headers={"content-type": "image/png"})

    result = await tools_over(settings, handler).call(
        "getPartImage", {"serial": "A-00000007"}
    )

    image = result.content[0]
    assert isinstance(image, types.ImageContent)
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data) == pixel


async def test_a_404_is_a_tool_error_and_not_an_exception(settings: Settings) -> None:
    """§6.5 rests on telling a missing id from a broken service.

    A citation check that could not distinguish them would have to treat every failure as
    "this id does not resolve", which is how a real outage becomes a stripped finding.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json={"detail": "no part with serial 'A-1'"})

    result = await tools_over(settings, handler).call("getPart", {"serial": "A-1"})

    assert result.is_error is True
    assert "404" in (
        result.content[0].text
        if isinstance(result.content[0], types.TextContent)
        else ""
    )


async def test_a_broken_service_is_not_reported_as_a_missing_id(
    settings: Settings,
) -> None:
    """The other half of the distinction above: a connection failure propagates.

    CLAUDE.md's rule, in the one place it matters most here — a caught transport error that
    came back as a tool result would be this server turning a loud failure into a quiet
    wrong answer.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(httpx.ConnectError):
        await tools_over(settings, handler).call("getPart", {"serial": "A-1"})


def test_every_json_operation_returns_an_object(settings: Settings) -> None:
    """`structuredContent` is defined as an object, so this holds the assumption the
    dispatch makes — and points at the guard that already exists for the day it stops."""
    document = yaml.safe_load(settings.contract.read_text())
    schemas = document["components"]["schemas"]

    for route, item in document["paths"].items():
        content = item["get"]["responses"]["200"]["content"]
        schema = content.get("application/json")
        if schema is None:
            continue
        name = schema["schema"]["$ref"].rsplit("/", 1)[-1]
        assert schemas[name]["type"] == "object", route


def test_a_parameter_in_a_header_is_refused(tmp_path: Path) -> None:
    """A tool argument goes in the path or the query. Anything else is a different kind of
    thing, and ignoring it would build a request that asks something narrower."""
    written = tmp_path / "header.yaml"
    written.write_text(
        "paths:\n"
        "  /parts:\n"
        "    get:\n"
        "      operationId: listParts\n"
        "      summary: List parts\n"
        "      parameters:\n"
        "        - name: X-Tenant\n"
        "          in: header\n"
        "          required: true\n"
        "          schema: {type: string}\n"
        "      responses:\n"
        "        '200':\n"
        "          content:\n"
        "            application/json: {}\n"
    )

    with pytest.raises(contract.ContractError, match="unsupported parameter location"):
        contract.load(written)
