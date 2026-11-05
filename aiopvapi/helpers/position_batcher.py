"""Coalesce compatible Gen3 position writes without reordering a shade's commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from typing import Any

from aiopvapi.helpers.aiorequest import PvApiCommandError


@dataclass
class _Command:
    url: str
    shade_id: int
    data: dict | None
    options: dict[str, Any]
    combine: bool
    future: asyncio.Future[dict]


@dataclass
class _Batch:
    command: _Command
    members: list[_Command] = field(default_factory=list)


class PositionBatcher:
    """Collect writes submitted in the same event-loop turn for one gateway."""

    def __init__(
        self,
        send: Callable[..., Awaitable[dict]],
        lock: asyncio.Lock | None = None,
    ) -> None:
        self._send = send
        self._lock = lock if lock is not None else asyncio.Lock()
        self._pending: list[_Command] = []
        self._scheduled: asyncio.Handle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._futures: set[asyncio.Future[dict]] = set()
        self._closed = False

    async def submit(
        self,
        url: str,
        shade_id: int,
        data: dict | None,
        options: dict[str, Any],
        *,
        combine: bool,
    ) -> dict:
        """Wait for the gateway result belonging to this shade."""
        if self._closed:
            raise RuntimeError("Position batching is closed")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._futures.add(future)
        future.add_done_callback(self._futures.discard)
        self._pending.append(
            _Command(url, shade_id, deepcopy(data), dict(options), combine, future)
        )
        if self._scheduled is None:
            self._scheduled = loop.call_soon(self._flush)
        return await future

    def close(self) -> None:
        """Cancel unsent work and in-flight writes when the gateway is unloaded."""
        self._closed = True
        if self._scheduled is not None:
            self._scheduled.cancel()
            self._scheduled = None
        self._pending.clear()
        for future in tuple(self._futures):
            future.cancel()
        for task in tuple(self._tasks):
            task.cancel()

    def _flush(self) -> None:
        self._scheduled = None
        pending, self._pending = self._pending, []
        task = asyncio.get_running_loop().create_task(self._dispatch(pending))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def _group(commands: list[_Command]) -> list[_Batch]:
        batches: list[_Batch] = []
        for command in commands:
            if command.future.cancelled():
                continue
            matching: _Batch | None = None
            for batch in reversed(batches):
                previous = batch.command
                if not command.combine or not previous.combine:
                    break
                if (
                    command.url == previous.url
                    and all(
                        PositionBatcher._payloads_match(command.data, member.data)
                        and (
                            command.shade_id != member.shade_id
                            or command.data == member.data
                        )
                        for member in batch.members
                    )
                    and command.options == previous.options
                ):
                    matching = batch
                    break
                if any(member.shade_id == command.shade_id for member in batch.members):
                    break
            if matching is None:
                batches.append(_Batch(command, [command]))
            else:
                matching.members.append(command)
        return batches

    @staticmethod
    def _payloads_match(first: dict | None, second: dict | None) -> bool:
        if first == second:
            return True
        if not isinstance(first, dict) or not isinstance(second, dict):
            return False
        left, right = deepcopy(first), deepcopy(second)
        left_positions = left.get("positions")
        right_positions = right.get("positions")
        if not isinstance(left_positions, dict) or not isinstance(
            right_positions, dict
        ):
            return False
        fields = [(left_positions, right_positions, {"primary", "secondary"})]
        left_light = left_positions.get("light")
        right_light = right_positions.get("light")
        if isinstance(left_light, dict) and isinstance(right_light, dict):
            fields.append((left_light, right_light, {"brightness"}))
        for left_fields, right_fields, percentages in fields:
            for key in percentages & left_fields.keys() & right_fields.keys():
                a, b = left_fields[key], right_fields[key]
                if not (
                    isinstance(a, (int, float))
                    and isinstance(b, (int, float))
                    and isfinite(a)
                    and isfinite(b)
                ):
                    return False
                # Decimal conversion keeps whole percentages below float boundaries.
                if abs(int(Decimal(str(a)) * 100) - int(Decimal(str(b)) * 100)) > 1:
                    return False
                left_fields[key] = b
        return left == right

    async def _dispatch(self, commands: list[_Command]) -> None:
        try:
            async with self._lock:
                for batch in self._group(commands):
                    members = [m for m in batch.members if not m.future.done()]
                    if not members:
                        continue
                    first = batch.command
                    ids = list(dict.fromkeys(member.shade_id for member in members))
                    try:
                        response = await self._send(
                            first.url,
                            data=first.data,
                            params={"ids": ",".join(map(str, ids))},
                            **first.options,
                        )
                    except Exception as error:  # noqa: BLE001
                        # Every transport failure must reach the waiting callers.
                        for member in members:
                            if not member.future.done():
                                member.future.set_exception(error)
                        continue
                    for member in members:
                        if member.future.done():
                            continue
                        try:
                            result = (
                                self._shade_result(response, member.shade_id)
                                if first.combine
                                else response
                            )
                        except PvApiCommandError as error:
                            member.future.set_exception(error)
                        else:
                            member.future.set_result(result)
        except asyncio.CancelledError:
            for command in commands:
                command.future.cancel()
            raise

    @staticmethod
    def _shade_result(response: dict, shade_id: int) -> dict:
        if not isinstance(response, dict):
            raise PvApiCommandError("Invalid position command response")
        if response.get("err") != 0:
            raise PvApiCommandError(
                f"Position command failed: {response.get('errMsg', response.get('err'))}"
            )
        responses = response.get("responses")
        if not isinstance(responses, list):
            raise PvApiCommandError("Position command response has no shade results")
        for result in responses:
            if isinstance(result, dict) and result.get("id") == shade_id:
                if result.get("err") != 0:
                    raise PvApiCommandError(
                        f"Shade {shade_id} rejected position command: {result.get('errMsg', result.get('err'))}"
                    )
                return {**response, "responses": [result]}
        raise PvApiCommandError(
            f"Position command response is missing shade {shade_id}"
        )
