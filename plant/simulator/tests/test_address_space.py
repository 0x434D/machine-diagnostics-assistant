import pytest
from conftest import new_server
from simulator.address_space import build_address_space
from simulator.events import EVENT_FIELDS


@pytest.mark.asyncio
async def test_s3_exposes_exactly_the_two_signals_and_no_packml() -> None:
    """§4.1 lists State and StateReason for every station, but §13 puts PackML in M2.
    A node that never changes is a fake, and §13's standard for M1 is that nothing
    in it is faked -- so they are absent, not static."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    names = {(await c.read_browse_name()).Name for c in await space.s3.get_children()}
    assert "TaktTime" in names
    assert "PartCount" in names
    assert "State" not in names
    assert "StateReason" not in names


@pytest.mark.asyncio
async def test_topology_is_browsable_under_line_stations() -> None:
    """§4.1: the gateway discovers the line's topology by browsing, never by config."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    await build_address_space(server, idx)

    line = await server.nodes.objects.get_child([f"{idx}:Line"])
    stations = await line.get_child([f"{idx}:Stations"])
    found = [(await s.read_browse_name()).Name for s in await stations.get_children()]
    assert found == ["S3_Inspection"]


@pytest.mark.asyncio
async def test_event_type_carries_an_image_field() -> None:
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    props = {
        (await p.read_browse_name()).Name
        for p in await space.event_type.get_properties()
    }
    assert {
        "AssemblySerial",
        "Disposition",
        "DefectClass",
        "Confidence",
        "Image",
    } <= props


@pytest.mark.asyncio
async def test_custom_event_fields_decode_first_and_in_declared_order() -> None:
    """Nothing pins EVENT_FIELDS' order against how the server actually hands the
    properties back -- reordering it would silently mis-assign every column
    Tasks 8 and 10 decode by position. get_properties() also returns
    BaseEventType's own inherited fields (13 of them); this only pins that our six
    lead, in the order EVENT_FIELDS declares."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    names = [
        (await p.read_browse_name()).Name
        for p in await space.event_type.get_properties()
    ]
    assert names[: len(EVENT_FIELDS)] == [name for name, _ in EVENT_FIELDS]
