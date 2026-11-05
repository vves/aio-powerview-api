"""Test Gen3 SSE framing, position updates, and connection lifecycle."""

import asyncio
import json
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from aiopvapi.events import ShadeEvent, ShadeEventState, ShadeEventStream
from aiopvapi.example.events import monitor
from aiopvapi.helpers.aiorequest import (
    AioRequest,
    PvApiConnectionError,
    PvApiResponseStatusError,
)
from aiopvapi.resources.light import ShadeLightState
from aiopvapi.resources.model import PowerviewData
from aiopvapi.resources.shade import (
    ShadeDualOverlappedIlluminated,
    ShadePosition,
    factory,
)
from aiopvapi.resources.shade_data import PowerviewShadeData
from aiopvapi.shades import Shades

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def captured_events() -> list[ShadeEvent]:
    """Load recorded Gen3 motion events."""
    records = (FIXTURES / "events/motion.sse").read_text().splitlines()
    return [
        ShadeEvent.from_data(json.loads(line.removeprefix("data: ")))
        for line in records
        if line.startswith("data:")
    ]


@pytest.fixture
def request_mock() -> Mock:
    """Return an HTTP request object backed by the actual library interface."""
    return Mock(spec=AioRequest, api_version=3, api_path="home", hub_ip="gateway.local")


@pytest.fixture
def shade_state(request_mock: Mock) -> ShadeEventState:
    """Build state with real Gen3 shade objects and snapshot data."""
    records = json.loads((FIXTURES / "gen3/home/shades.json").read_text())
    primary = deepcopy(records[0])
    primary["id"] = 9
    secondary = deepcopy(records[0])
    secondary["id"] = 378
    snapshot = [primary, secondary]
    data = PowerviewShadeData()

    data.store_group_data(
        PowerviewData(
            raw=snapshot,
            processed={
                record["id"]: factory(record, request_mock) for record in snapshot
            },
        )
    )
    return ShadeEventState(data)


def test_capture_updates(
    captured_events: list[ShadeEvent], shade_state: ShadeEventState
) -> None:
    """Both shades update despite timestamps crossing in arrival order."""
    assert all(shade_state.apply(event) for event in captured_events)
    assert shade_state.data.get_shade_position(9).primary == 58
    assert shade_state.data.get_raw_data(9)["positions"]["primary"] == 0.576
    assert shade_state.data.get_shade_position(378).tilt == 0
    assert shade_state.motion[9].event_type == "motion-stopped"
    assert shade_state.target_position(9) is None


def test_target_is_not_current_position(
    captured_events: list[ShadeEvent], shade_state: ShadeEventState
) -> None:
    """A move's target does not replace the reported starting position."""
    event = captured_events[-2]
    assert shade_state.apply(event)
    assert shade_state.data.get_shade_position(9).primary == 0
    assert shade_state.target_position(9).primary == 58
    assert event.eta_in_seconds == 10


@pytest.mark.parametrize(
    "offset", [pytest.param(0, id="duplicate"), pytest.param(-1, id="older")]
)
def test_outdated_event(
    captured_events: list[ShadeEvent], shade_state: ShadeEventState, offset: int
) -> None:
    """Ignore duplicate and older messages for the same shade."""
    final = captured_events[-1]
    assert shade_state.apply(final)
    outdated = replace(final, timestamp=final.timestamp + timedelta(seconds=offset))
    assert not shade_state.apply(outdated)
    assert shade_state.data.get_raw_data(9)["positions"]["primary"] == 0.576


def test_unknown_shade(
    captured_events: list[ShadeEvent], shade_state: ShadeEventState
) -> None:
    """An event for a newly added shade does not break existing entities."""
    assert not shade_state.apply(replace(captured_events[0], shade_id=99999))


def test_partial_positions(
    captured_events: list[ShadeEvent], shade_state: ShadeEventState
) -> None:
    """Only supplied axes change, preserving diagnostics and selected velocity."""
    original = deepcopy(shade_state.data.get_raw_data(9))
    shade_state.data.get_shade_position(9).velocity = 25
    assert shade_state.apply(
        replace(captured_events[-1], current_positions={"primary": 0.576})
    )
    raw = shade_state.data.get_raw_data(9)
    assert raw["positions"]["tilt"] == original["positions"]["tilt"]
    assert raw["batteryStatus"] == original["batteryStatus"]
    assert shade_state.data.get_shade_position(9).velocity == 25


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("id", True, id="boolean-id"),
        pytest.param("isoDate", "invalid", id="invalid-time"),
        pytest.param("isoDate", "2026-10-26T15:00:00", id="missing-timezone"),
        pytest.param("currentPositions", {"primary": float("nan")}, id="nan"),
        pytest.param("currentPositions", {"primary": 2}, id="outside-range"),
        pytest.param("currentPositions", {"primary": True}, id="boolean-position"),
        pytest.param("currentPositions", {}, id="empty-positions"),
        pytest.param(
            "targetPositions", {"primary": 0, "etaInSeconds": -1}, id="negative-eta"
        ),
    ],
)
def test_invalid_event(field: str, value: object) -> None:
    """Reject unusable motion state without inventing values."""
    data = {
        "evt": "motion-started",
        "id": 9,
        "isoDate": "2026-10-26T15:00:00Z",
        "currentPositions": {"primary": 0},
        "targetPositions": {"primary": 1},
        field: value,
    }
    with pytest.raises(ValueError):
        ShadeEvent.from_data(data)


@pytest.fixture
def response_mock(request_mock: Mock) -> Mock:
    """Return a stream response without opening a network socket."""
    response = Mock(status=200)
    request_mock.websession = Mock(get=AsyncMock(return_value=response))
    return response


async def lines(payload: bytes) -> AsyncIterator[bytes]:
    """Emulate aiohttp's line iteration."""
    for line in payload.splitlines(keepends=True):
        yield line


@pytest.mark.parametrize(
    ("filename", "count", "final_primary", "final_light"),
    [
        pytest.param("motion.sse", 10, 0.576, None, id="standard-shades"),
        pytest.param("aura.sse", 4, 0, ShadeLightState(100, 2700), id="aura"),
        pytest.param(
            "aura_temperature.sse",
            6,
            0,
            ShadeLightState(100, 2700),
            id="aura-temperature",
        ),
    ],
)
@pytest.mark.asyncio
async def test_stream_capture(
    request_mock: Mock,
    response_mock: Mock,
    filename: str,
    count: int,
    final_primary: float,
    final_light: ShadeLightState | None,
) -> None:
    """Read the greeting and all captured data records using SSE framing."""
    payload = (FIXTURES / "events" / filename).read_bytes() + b"\n"
    response_mock.content = lines(payload)
    events = []
    async with ShadeEventStream(request_mock) as stream:
        with pytest.raises(PvApiConnectionError, match="closed"):
            async for event in stream.events():
                events.append(event)
    assert len(events) == count
    assert events[-1].current_positions["primary"] == final_primary
    assert events[-1].current_light == final_light
    kwargs = request_mock.websession.get.call_args.kwargs
    assert kwargs["params"] == {"sse": "true", "raw": "true"}
    assert kwargs["timeout"].total is None
    response_mock.close.assert_called_once()
    request_mock.websession.close.assert_not_called()


@pytest.mark.asyncio
async def test_sse_framing(request_mock: Mock, response_mock: Mock) -> None:
    """Ignore comments, greetings, future events, and malformed messages."""
    response_mock.content = lines(
        b"100 HELO\r\n\r\n: heartbeat\r\n\r\ndata: invalid\r\n\r\n"
        b'data: []\n\ndata: {"evt":"future-event"}\n\n'
        b'data: {"evt":"motion-stopped",\r\n'
        b'data: "id":9,"isoDate":"2026-10-26T15:46:12.747Z",\r\n'
        b'data: "currentPositions":{"primary":0.576}}\r\n\r\n'
    )
    events = []
    async with ShadeEventStream(request_mock) as stream:
        with pytest.raises(PvApiConnectionError, match="closed"):
            async for event in stream.events():
                events.append(event)
    assert len(events) == 1
    assert events[0].shade_id == 9


@pytest.mark.asyncio
async def test_rejected_connection(request_mock: Mock, response_mock: Mock) -> None:
    """Close unsuccessful responses without trying to decode them as SSE."""
    response_mock.status = 423
    with pytest.raises(PvApiResponseStatusError):
        async with ShadeEventStream(request_mock):
            pytest.fail("A maintenance response must not open a stream")
    response_mock.close.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(aiohttp.ClientError(), id="http"),
    ],
)
@pytest.mark.asyncio
async def test_connection_error(
    request_mock: Mock, response_mock: Mock, error: Exception
) -> None:
    """Expose connection failures through the library's normal error type."""
    request_mock.websession.get.side_effect = error
    with pytest.raises(PvApiConnectionError, match="Unable to open"):
        async with ShadeEventStream(request_mock):
            pytest.fail("A failed request must not enter the stream context")


def test_same_timestamp_transition(
    captured_events: list[ShadeEvent], shade_state: ShadeEventState
) -> None:
    """Distinct transitions within the same millisecond retain arrival order."""
    started = captured_events[-2]
    assert shade_state.apply(started)
    stopped = replace(captured_events[-1], timestamp=started.timestamp)
    assert shade_state.apply(stopped)
    assert shade_state.motion[9].event_type == "motion-stopped"


@pytest.mark.parametrize("api_version", [1, 2])
def test_legacy_rejected(request_mock: Mock, api_version: int) -> None:
    """Gen1 and Gen2 cannot accidentally select streaming."""
    request_mock.api_version = api_version
    with pytest.raises(ValueError, match="Gen3"):
        ShadeEventStream(request_mock)


@pytest.mark.asyncio
async def test_cancel_stream(request_mock: Mock, response_mock: Mock) -> None:
    """Cancellation closes the response and leaves the shared session alive."""

    async def consume() -> None:
        async with ShadeEventStream(request_mock):
            await asyncio.Future()

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    response_mock.close.assert_called_once()
    request_mock.websession.close.assert_not_called()


@pytest.mark.asyncio
async def test_event_size_limit(request_mock: Mock, response_mock: Mock) -> None:
    """A broken stream cannot build an unbounded event in memory."""
    response_mock.content = lines(b"data: " + b"x" * 65536 + b"\n\n")
    async with ShadeEventStream(request_mock) as stream:
        with pytest.raises(PvApiConnectionError, match="maximum size"):
            await anext(stream.events())


@pytest_asyncio.fixture
async def gateway() -> AsyncIterator[tuple[TestServer, list[str]]]:
    """Serve a current snapshot and subsequent motion events."""
    calls: list[str] = []

    async def events(request: web.Request) -> web.StreamResponse:
        assert request.query == {"sse": "true", "raw": "true"}
        calls.append("stream")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            b'data: {"evt":"motion-stopped","id":413,'
            b'"isoDate":"2026-10-26T15:46:12.747Z",'
            b'"currentPositions":{"primary":0.576}}\n\n'
        )
        await response.write_eof()
        return response

    async def snapshot(request: web.Request) -> web.Response:
        calls.append("snapshot")
        return web.json_response(
            json.loads((FIXTURES / "gen3/home/shades.json").read_text())
        )

    app = web.Application()
    app.router.add_get("/home/shades/events", events)
    app.router.add_get("/home/shades", snapshot)
    async with TestServer(app) as server:
        yield server, calls


@pytest.mark.asyncio
async def test_snapshot_and_reconnect(gateway: tuple[TestServer, list[str]]) -> None:
    """Read current state before opening the stream on every connection."""
    server, calls = gateway
    async with aiohttp.ClientSession() as session:
        request = AioRequest(
            str(server.make_url("")), websession=session, api_version=3
        )
        for _ in range(2):
            data = PowerviewShadeData()
            data.store_group_data(await Shades(request).get_shades())
            state = ShadeEventState(data)
            assert data.get_shade_position(413).primary == 0
            async with ShadeEventStream(request) as stream:
                event = await anext(stream.events())
                assert state.apply(event)
                assert data.get_shade_position(413).primary == 58
        assert not session.closed
    assert calls == ["snapshot", "stream", "snapshot", "stream"]


@pytest.mark.asyncio
async def test_monitor_reconnect(
    gateway: tuple[TestServer, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The runnable prototype resnapshots after a stream disconnect."""
    server, calls = gateway
    reconnect_delay = AsyncMock(side_effect=[None, asyncio.CancelledError])
    monkeypatch.setattr("aiopvapi.example.events.asyncio.sleep", reconnect_delay)
    with pytest.raises(asyncio.CancelledError):
        await monitor(str(server.make_url("")))
    assert calls == ["snapshot", "stream", "snapshot", "stream"]
    assert reconnect_delay.await_args_list == [call(1), call(1)]
    output = capsys.readouterr().out
    assert output.count("shade=413 motion-stopped") == 2


@pytest.mark.parametrize("shade_index", [80, 95, 96])
def test_aura_shade_indexes(request_mock: Mock, shade_index: int) -> None:
    """Recognize every Aura index without requiring capability fallback."""
    records = json.loads((FIXTURES / "gen3/home/shades.json").read_text())
    raw = deepcopy(records[-1])
    raw["type"] = shade_index
    raw.pop("capabilities")
    raw["positions"]["light"] = {"brightness": 50, "temperature": 2700}

    shade = factory(raw, request_mock)

    assert isinstance(shade, ShadeDualOverlappedIlluminated)
    assert shade.type_name == "Aura® Illuminated Shade"
    assert shade.current_light == ShadeLightState(50, 2700)


@pytest.fixture
def aura_state(request_mock: Mock) -> ShadeEventState:
    """Initialize Aura shades using the existing type 95 snapshot fixture."""
    records = json.loads((FIXTURES / "gen3/home/shades.json").read_text())
    snapshot = []
    for shade_id in (281, 291):
        raw = deepcopy(records[-1])
        raw["id"] = shade_id
        raw["positions"] = {
            "primary": 0,
            "secondary": 1,
            "tilt": 0,
            "light": {"brightness": 0, "temperature": 2700},
        }
        snapshot.append(raw)
    data = PowerviewShadeData()
    data.store_group_data(
        PowerviewData(
            raw=snapshot,
            processed={raw["id"]: factory(raw, request_mock) for raw in snapshot},
        )
    )
    return ShadeEventState(data)


@pytest.mark.parametrize("shade_id", [281, 291])
def test_aura_motion(aura_state: ShadeEventState, shade_id: int) -> None:
    """Light-only movement preserves the front and rear panel positions."""
    shade = aura_state.data.get_shade(shade_id)
    assert isinstance(shade, ShadeDualOverlappedIlluminated)
    assert shade.current_light == ShadeLightState(0, 2700)
    records = (FIXTURES / "events/aura.sse").read_text().splitlines()
    events = [
        ShadeEvent.from_data(json.loads(line.removeprefix("data: ")))
        for line in records
        if line.startswith("data:")
    ]
    started, stopped = [event for event in events if event.shade_id == shade_id]
    assert aura_state.apply(started)
    assert shade.current_light == ShadeLightState(0, 2700)
    assert aura_state.target_light(shade_id) == ShadeLightState(100, 2700)
    assert aura_state.apply(stopped)
    assert shade.current_light == ShadeLightState(100, 2700)
    assert aura_state.target_light(shade_id) is None
    assert aura_state.data.get_shade_position(shade_id).primary == 0
    assert aura_state.data.get_shade_position(shade_id).secondary == 100


@pytest.mark.parametrize(
    ("positions", "expected"),
    [
        pytest.param(
            {"light": {"brightness": 50}}, ShadeLightState(50, 2700), id="dim"
        ),
        pytest.param({"light": {"brightness": 0}}, ShadeLightState(0, 2700), id="off"),
        pytest.param(
            {"light": {"temperature": 3500}}, ShadeLightState(0, 3500), id="temperature"
        ),
        pytest.param({"primary": 0.5}, ShadeLightState(0, 2700), id="panel-only"),
    ],
)
def test_aura_partial_update(
    aura_state: ShadeEventState, positions: dict[str, object], expected: ShadeLightState
) -> None:
    """Merge partial light state without resetting omitted fields or axes."""
    event = ShadeEvent.from_data(
        {
            "evt": "motion-stopped",
            "id": 281,
            "isoDate": "2026-10-26T16:25:08.895Z",
            "currentPositions": positions,
        }
    )
    assert aura_state.apply(event)
    shade = aura_state.data.get_shade(281)
    assert isinstance(shade, ShadeDualOverlappedIlluminated)
    assert shade.current_light == expected
    assert aura_state.data.get_shade_position(281).secondary == 100


def test_aura_unknown_light(request_mock: Mock) -> None:
    """A snapshot's null light state is unknown rather than off."""
    records = json.loads((FIXTURES / "gen3/home/shades.json").read_text())
    shade = factory(records[-1], request_mock)
    assert isinstance(shade, ShadeDualOverlappedIlluminated)
    assert shade.current_light is None


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({}, id="empty"),
        pytest.param({"brightness": True}, id="boolean"),
        pytest.param({"brightness": -1}, id="negative-brightness"),
        pytest.param({"brightness": 101}, id="excessive-brightness"),
        pytest.param({"brightness": float("nan")}, id="nan"),
        pytest.param({"temperature": 0}, id="zero-kelvin"),
        pytest.param({"temperature": 2199}, id="below-minimum-kelvin"),
        pytest.param({"temperature": 6501}, id="above-maximum-kelvin"),
        pytest.param({"temperature": "2700"}, id="string-temperature"),
    ],
)
def test_invalid_light(data: dict[str, object]) -> None:
    """Reject invalid light values without treating them as a valid off state."""
    with pytest.raises(ValueError):
        ShadeLightState.from_data(data)


@pytest.mark.parametrize(
    ("brightness", "temperature", "rate_in_seconds"),
    [
        pytest.param(0.0, 2700, 1, id="off"),
        pytest.param(0.5, 2700, 2, id="dim"),
        pytest.param(1.0, 3500, 1, id="temperature"),
        pytest.param(1.0, 2700, 0, id="instant"),
        pytest.param(1.0, 2200, 1, id="minimum-kelvin"),
        pytest.param(1.0, 6500, 1, id="maximum-kelvin"),
    ],
)
@pytest.mark.asyncio
async def test_aura_light_command(
    aura_state: ShadeEventState,
    request_mock: Mock,
    brightness: float,
    temperature: int,
    rate_in_seconds: int,
) -> None:
    """Send all required light fields and wait for SSE to report the result."""
    request_mock.put = AsyncMock()
    shade = aura_state.data.get_shade(281)
    assert isinstance(shade, ShadeDualOverlappedIlluminated)
    original = deepcopy(shade.raw_data)
    await shade.set_light(
        brightness=brightness, temperature=temperature, rate_in_seconds=rate_in_seconds
    )
    request_mock.put.assert_awaited_once_with(
        "http://gateway.local/home/shades/positions",
        data={
            "positions": {
                "light": {
                    "brightness": brightness,
                    "temperature": temperature,
                    "rateInSeconds": rate_in_seconds,
                }
            }
        },
        params={"ids": 281},
    )
    assert shade.raw_data == original


@pytest.mark.parametrize(
    ("brightness", "temperature", "rate_in_seconds"),
    [
        pytest.param(-0.1, 2700, 1, id="negative-brightness"),
        pytest.param(1.01, 2700, 1, id="excessive-brightness"),
        pytest.param(100.0, 2700, 1, id="percent-in-command"),
        pytest.param(1.0, 0, 1, id="zero-kelvin"),
        pytest.param(1.0, 2199, 1, id="below-minimum-kelvin"),
        pytest.param(1.0, 6501, 1, id="above-maximum-kelvin"),
        pytest.param(1.0, 2700, -1, id="negative-duration"),
        pytest.param(1.0, 2700, float("nan"), id="nan-duration"),
        pytest.param(1.0, 2700, True, id="boolean-duration"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_aura_command(
    aura_state: ShadeEventState,
    request_mock: Mock,
    brightness: float,
    temperature: int,
    rate_in_seconds: int | float,
) -> None:
    """Reject invalid commands before contacting the gateway."""
    request_mock.put = AsyncMock()
    shade = aura_state.data.get_shade(281)
    assert isinstance(shade, ShadeDualOverlappedIlluminated)
    with pytest.raises(ValueError):
        await shade.set_light(
            brightness=brightness,
            temperature=temperature,
            rate_in_seconds=rate_in_seconds,
        )
    request_mock.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_aura_http_command() -> None:
    """Send the confirmed command through the real HTTP client and parse its reply."""
    response = json.loads((FIXTURES / "events/aura_command.json").read_text())
    received: list[dict[str, object]] = []

    async def positions(request: web.Request) -> web.Response:
        assert request.query == {"ids": "291"}
        received.append(await request.json())
        return web.json_response(response)

    app = web.Application()
    app.router.add_put("/home/shades/positions", positions)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        request = AioRequest(
            str(server.make_url("")), websession=session, api_version=3
        )
        records = json.loads((FIXTURES / "gen3/home/shades.json").read_text())
        raw = records[-1]
        raw["id"] = 291
        shade = factory(raw, request)
        assert isinstance(shade, ShadeDualOverlappedIlluminated)
        await shade.set_light(brightness=1.0, temperature=2700, rate_in_seconds=1)
        assert shade.current_light is None
    assert received == [
        {
            "positions": {
                "light": {"brightness": 1.0, "temperature": 2700, "rateInSeconds": 1}
            }
        }
    ]


def test_aura_temperature_sequence(aura_state: ShadeEventState) -> None:
    """Temperature changes update only the corresponding shade's light state."""
    records = (FIXTURES / "events/aura_temperature.sse").read_text().splitlines()
    events = [
        ShadeEvent.from_data(json.loads(line.removeprefix("data: ")))
        for line in records
        if line.startswith("data:")
    ]
    assert all(aura_state.apply(event) for event in events)
    shade_281 = aura_state.data.get_shade(281)
    shade_291 = aura_state.data.get_shade(291)
    assert isinstance(shade_281, ShadeDualOverlappedIlluminated)
    assert isinstance(shade_291, ShadeDualOverlappedIlluminated)
    assert shade_281.current_light == ShadeLightState(100, 2200)
    assert shade_291.current_light == ShadeLightState(100, 2700)
    assert aura_state.data.get_shade_position(281).secondary == 100
    assert aura_state.data.get_shade_position(291).secondary == 100


@pytest.mark.parametrize(
    ("update_position", "expected"),
    [
        pytest.param(True, 50, id="legacy-optimistic"),
        pytest.param(False, 0, id="await-stream"),
    ],
)
@pytest.mark.asyncio
async def test_move_state_confirmation(
    aura_state: ShadeEventState,
    request_mock: Mock,
    update_position: bool,
    expected: int,
) -> None:
    """Stream consumers can send a move without overwriting reported positions."""
    request_mock.put = AsyncMock(return_value={"err": 0})
    shade = aura_state.data.get_shade(281)
    await shade.move(ShadePosition(primary=50), update_position=update_position)
    request_mock.put.assert_awaited_once()
    assert shade.current_position.primary == expected
