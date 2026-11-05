"""Test the Gen3 batching transport against a real local HTTP server."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from aiopvapi.helpers.aiorequest import (
    AioRequest,
    PvApiCommandError,
    PvApiResponseStatusError,
)


@dataclass
class Gateway:
    request: AioRequest
    url: str
    calls: list[dict] = field(default_factory=list)
    responses: list[tuple[int, dict]] = field(default_factory=list)
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@pytest_asyncio.fixture
async def gateway() -> AsyncIterator[Gateway]:
    async def handle(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else None
        state.calls.append(
            {"path": request.path, "ids": request.query["ids"], "data": data}
        )
        state.entered.set()
        await state.release.wait()
        if state.responses:
            status, response = state.responses.pop(0)
            return web.json_response(response, status=status)
        return web.json_response(
            {
                "err": 0,
                "responses": [
                    {"id": int(value), "err": 0}
                    for value in request.query["ids"].split(",")
                ],
            }
        )

    app = web.Application()
    app.router.add_put("/home/shades/{command}", handle)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        request = AioRequest(
            str(server.make_url("/")), websession=session, api_version=3
        )
        request.enable_position_batching()
        state = Gateway(request, str(server.make_url("/home/shades/positions")))
        state.release.set()
        try:
            yield state
        finally:
            state.release.set()
            request.disable_position_batching()
            await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "positions",
    [
        pytest.param({"primary": 0.7}, id="position"),
        pytest.param({"tilt": 0.7}, id="tilt"),
        pytest.param(
            {"light": {"brightness": 0.5, "temperature": 2700, "rateInSeconds": 1}},
            id="aura",
        ),
    ],
)
async def test_same_payload(gateway: Gateway, positions: dict) -> None:
    results = await asyncio.gather(
        *(
            gateway.request.put(
                gateway.url, data={"positions": positions}, params={"ids": shade_id}
            )
            for shade_id in (1, 2, 3)
        )
    )
    assert gateway.calls == [
        {
            "path": "/home/shades/positions",
            "ids": "1,2,3",
            "data": {"positions": positions},
        }
    ]
    assert results == [
        {"err": 0, "responses": [{"id": 1, "err": 0}]},
        {"err": 0, "responses": [{"id": 2, "err": 0}]},
        {"err": 0, "responses": [{"id": 3, "err": 0}]},
    ]


@pytest.mark.asyncio
async def test_different_payloads(gateway: Gateway) -> None:
    await asyncio.gather(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.2}}, params={"ids": 1}
        ),
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 2}
        ),
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.2}}, params={"ids": 3}
        ),
    )
    assert gateway.calls == [
        {
            "path": "/home/shades/positions",
            "ids": "1,3",
            "data": {"positions": {"primary": 0.2}},
        },
        {
            "path": "/home/shades/positions",
            "ids": "2",
            "data": {"positions": {"primary": 0.7}},
        },
    ]


@pytest.mark.asyncio
async def test_same_shade_order(gateway: Gateway) -> None:
    await asyncio.gather(
        *(
            gateway.request.put(
                gateway.url,
                data={"positions": {"primary": position}},
                params={"ids": 1},
            )
            for position in (0.2, 0.7, 0.2)
        )
    )
    assert [call["data"]["positions"]["primary"] for call in gateway.calls] == [
        0.2,
        0.7,
        0.2,
    ]


@pytest.mark.asyncio
async def test_duplicate_target(gateway: Gateway) -> None:
    await asyncio.gather(
        *(
            gateway.request.put(
                gateway.url,
                data={"positions": {"primary": 0.7}},
                params={"ids": shade_id},
            )
            for shade_id in (1, 1, 2)
        )
    )
    assert [call["ids"] for call in gateway.calls] == ["1,2"]


@pytest.mark.asyncio
async def test_stop_is_ordering_barrier(gateway: Gateway) -> None:
    await asyncio.gather(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 1}
        ),
        gateway.request.put(
            gateway.url.removesuffix("positions") + "stop", params={"ids": 1}
        ),
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 2}
        ),
    )
    assert [(call["path"], call["ids"]) for call in gateway.calls] == [
        ("/home/shades/positions", "1"),
        ("/home/shades/stop", "1"),
        ("/home/shades/positions", "2"),
    ]


@pytest.mark.asyncio
async def test_cancel_before_send(gateway: Gateway) -> None:
    first = asyncio.create_task(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 1}
        )
    )
    second = asyncio.create_task(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 2}
        )
    )
    asyncio.get_running_loop().call_soon(first.cancel)
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert [call["ids"] for call in gateway.calls] == ["2"]


@pytest.mark.asyncio
async def test_cancel_in_flight(gateway: Gateway) -> None:
    gateway.release.clear()
    first = asyncio.create_task(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 1}
        )
    )
    second = asyncio.create_task(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 2}
        )
    )
    await gateway.entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    gateway.release.set()
    assert await second == {"err": 0, "responses": [{"id": 2, "err": 0}]}
    assert [call["ids"] for call in gateway.calls] == ["1,2"]


@pytest.mark.asyncio
async def test_close_cancels_unsent(gateway: Gateway) -> None:
    first = asyncio.create_task(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 1}
        )
    )
    second = asyncio.create_task(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 2}
        )
    )
    asyncio.get_running_loop().call_soon(gateway.request.disable_position_batching)
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_partial_failure(gateway: Gateway) -> None:
    gateway.responses.append(
        (200, {"err": 0, "responses": [{"id": 1, "err": 0}, {"id": 2, "err": 42}]})
    )
    results = await asyncio.gather(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 1}
        ),
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 2}
        ),
        return_exceptions=True,
    )
    assert results[0] == {"err": 0, "responses": [{"id": 1, "err": 0}]}
    assert isinstance(results[1], PvApiCommandError)
    assert "Shade 2" in str(results[1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"err": 7, "errMsg": "Unavailable"}, id="gateway-error"),
        pytest.param({"err": 0}, id="missing-results"),
        pytest.param({"err": 0, "responses": []}, id="missing-shade"),
    ],
)
async def test_invalid_result(gateway: Gateway, response: dict) -> None:
    gateway.responses.append((200, response))
    with pytest.raises(PvApiCommandError):
        await gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 1}
        )


@pytest.mark.asyncio
async def test_transport_failure_and_recovery(gateway: Gateway) -> None:
    gateway.responses.append((503, {"errMsg": "Unavailable"}))
    results = await asyncio.gather(
        *(
            gateway.request.put(
                gateway.url,
                data={"positions": {"primary": 0.7}},
                params={"ids": shade_id},
            )
            for shade_id in (1, 2)
        ),
        return_exceptions=True,
    )
    assert all(isinstance(result, PvApiResponseStatusError) for result in results)
    assert await gateway.request.put(
        gateway.url, data={"positions": {"primary": 0.3}}, params={"ids": 1}
    ) == {"err": 0, "responses": [{"id": 1, "err": 0}]}


@pytest.mark.asyncio
async def test_batching_is_opt_in(gateway: Gateway) -> None:
    gateway.request.disable_position_batching()
    await asyncio.gather(
        *(
            gateway.request.put(
                gateway.url,
                data={"positions": {"primary": 0.7}},
                params={"ids": shade_id},
            )
            for shade_id in (1, 2)
        )
    )
    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_legacy_gateway_rejected(gateway: Gateway) -> None:
    gateway.request.disable_position_batching()
    gateway.request.api_version = 2
    with pytest.raises(ValueError, match="Gen3"):
        gateway.request.enable_position_batching()


@pytest.mark.asyncio
async def test_close_cancels_in_flight(gateway: Gateway) -> None:
    gateway.release.clear()
    command = asyncio.create_task(
        gateway.request.put(
            gateway.url, data={"positions": {"primary": 0.7}}, params={"ids": 1}
        )
    )
    await gateway.entered.wait()
    gateway.request.disable_position_batching()
    with pytest.raises(asyncio.CancelledError):
        await command
    gateway.release.set()


@pytest.mark.asyncio
async def test_separate_connections_do_not_merge(gateway: Gateway) -> None:
    async with aiohttp.ClientSession() as session:
        other = AioRequest(gateway.url, websession=session, api_version=3)
        other.enable_position_batching()
        try:
            await asyncio.gather(
                gateway.request.put(
                    gateway.url,
                    data={"positions": {"primary": 0.7}},
                    params={"ids": 1},
                ),
                other.put(
                    gateway.url,
                    data={"positions": {"primary": 0.7}},
                    params={"ids": 2},
                ),
            )
        finally:
            other.disable_position_batching()
    assert len(gateway.calls) == 2
    assert {call["ids"] for call in gateway.calls} == {"1", "2"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "second", "expected_ids"),
    [
        pytest.param({"primary": 0.502}, {"primary": 0.51}, ["1,2"], id="primary"),
        pytest.param(
            {"secondary": 0.502}, {"secondary": 0.519}, ["1,2"], id="secondary"
        ),
        pytest.param({"primary": 0.51}, {"primary": 0.50}, ["1,2"], id="reverse"),
        pytest.param(
            {"primary": 0.29}, {"primary": 0.30}, ["1,2"], id="float-boundary"
        ),
        pytest.param({"primary": 0.50}, {"primary": 0.52}, ["1", "2"], id="outside"),
        pytest.param({"tilt": 0.502}, {"tilt": 0.51}, ["1", "2"], id="tilt-exact"),
        pytest.param(
            {"light": {"brightness": 0.502, "temperature": 2700, "rateInSeconds": 1}},
            {"light": {"brightness": 0.51, "temperature": 2700, "rateInSeconds": 1}},
            ["1,2"],
            id="brightness",
        ),
        pytest.param(
            {"light": {"brightness": 0.5, "temperature": 2700, "rateInSeconds": 1}},
            {"light": {"brightness": 0.52, "temperature": 2700, "rateInSeconds": 1}},
            ["1", "2"],
            id="brightness-outside",
        ),
        pytest.param(
            {"light": {"brightness": 0.5, "temperature": 2700, "rateInSeconds": 1}},
            {"light": {"brightness": 0.51, "temperature": 2701, "rateInSeconds": 1}},
            ["1", "2"],
            id="temperature-exact",
        ),
        pytest.param(
            {"light": {"brightness": 0.5, "temperature": 2700, "rateInSeconds": 1}},
            {"light": {"brightness": 0.51, "temperature": 2700, "rateInSeconds": 2}},
            ["1", "2"],
            id="transition-exact",
        ),
    ],
)
async def test_percentage_tolerance(
    gateway: Gateway, first: dict, second: dict, expected_ids: list[str]
) -> None:
    await asyncio.gather(
        gateway.request.put(gateway.url, data={"positions": first}, params={"ids": 1}),
        gateway.request.put(gateway.url, data={"positions": second}, params={"ids": 2}),
    )
    assert [call["ids"] for call in gateway.calls] == expected_ids
    assert gateway.calls[0]["data"] == {"positions": first}


@pytest.mark.asyncio
@pytest.mark.parametrize(("ids", "count"), [((1, 2, 3), 2), ((1, 1, 1), 3)])
async def test_tolerance_does_not_chain_or_replace_commands(
    gateway: Gateway, ids: tuple[int, int, int], count: int
) -> None:
    await asyncio.gather(
        *(
            gateway.request.put(
                gateway.url,
                data={"positions": {"primary": position}},
                params={"ids": shade_id},
            )
            for shade_id, position in zip(ids, (0.51, 0.50, 0.52), strict=True)
        )
    )
    assert gateway.calls[-1]["data"] == {"positions": {"primary": 0.52}}
    assert len(gateway.calls) == count
