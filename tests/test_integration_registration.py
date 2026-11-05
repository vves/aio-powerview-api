"""Test gateway registration for third-party integrations."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from aiopvapi.helpers.aiorequest import (
    AioRequest,
    PvApiMaintenance,
    PvApiResponseStatusError,
)
from aiopvapi.hub import Hub


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "body"), [(200, "{}"), (204, None)])
async def test_register_home_assistant(status: int, body: str | None) -> None:
    """Register the exact case-sensitive support tag with a PUT and no payload."""
    calls: list[tuple[str, bytes]] = []

    async def register(request: web.Request) -> web.Response:
        calls.append((request.path, await request.read()))
        return web.Response(status=status, text=body, content_type="application/json")

    app = web.Application()
    app.router.add_put("/home/integration/HomeAssistant", register)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        request = AioRequest(
            str(server.make_url("/")), websession=session, api_version=3
        )
        await Hub(request).register_integration("HomeAssistant")
    assert calls == [("/home/integration/HomeAssistant", b"")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [(503, PvApiResponseStatusError), (423, PvApiMaintenance)],
)
async def test_registration_failure(status: int, error: type[Exception]) -> None:
    """Surface registration failure so consumers can retry startup."""

    async def register(request: web.Request) -> web.Response:
        return web.Response(status=status)

    app = web.Application()
    app.router.add_put("/home/integration/HomeAssistant", register)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        request = AioRequest(
            str(server.make_url("/")), websession=session, api_version=3
        )
        with pytest.raises(error):
            await Hub(request).register_integration("HomeAssistant")


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_legacy_registration(version: int) -> None:
    """Legacy hubs do not support gateway registration."""
    request = AioRequest("127.0.0.1", websession=MagicMock(), api_version=version)
    request.put = AsyncMock()
    await Hub(request).register_integration("HomeAssistant")
    request.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_registration_name() -> None:
    """Other identifiers retain the existing gateway validation rules."""
    request = AioRequest("127.0.0.1", websession=MagicMock(), api_version=3)
    request.put = AsyncMock()
    await Hub(request).register_integration("invalid/name")
    request.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_commands_retain_maintenance_handling() -> None:
    """A 204 outside registration retains the existing gateway maintenance signal."""

    async def move(request: web.Request) -> web.Response:
        return web.Response(status=204)

    app = web.Application()
    app.router.add_put("/home/shades/positions", move)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        request = AioRequest(
            str(server.make_url("/")), websession=session, api_version=3
        )
        with pytest.raises(PvApiMaintenance):
            await request.put(str(server.make_url("/home/shades/positions")))
