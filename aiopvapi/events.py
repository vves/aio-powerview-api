"""Receive Gen3 shade motion events from a PowerView gateway."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any

import aiohttp

from aiopvapi.helpers.aiorequest import (
    AioRequest,
    PvApiConnectionError,
    PvApiResponseStatusError,
)
from aiopvapi.helpers.constants import ATTR_ID, ATTR_POSITIONS, POSITIONS_V3
from aiopvapi.helpers.tools import get_base_path
from aiopvapi.resources.light import ShadeLightState
from aiopvapi.resources.shade import ShadePosition
from aiopvapi.resources.shade_data import PowerviewShadeData

_LOGGER = logging.getLogger(__name__)
_MAX_EVENT_SIZE = 65536


def _positions(value: Any) -> dict[str, float]:
    """Validate the positions carried by a motion event."""
    if not isinstance(value, dict):
        raise ValueError("Missing positions")
    positions = {}
    for key in POSITIONS_V3:
        if key not in value:
            continue
        position = value[key]
        if (
            isinstance(position, bool)
            or not isinstance(position, (int, float))
            or not math.isfinite(position)
            or not 0 <= position <= 1
        ):
            raise ValueError("Invalid position")
        positions[key] = float(position)
    return positions


@dataclass(frozen=True)
class ShadeEvent:
    """A reported motion state; target positions are never reported positions."""

    event_type: str
    shade_id: int
    timestamp: datetime
    current_positions: dict[str, float]
    target_positions: dict[str, float] | None = None
    eta_in_seconds: float | None = None
    current_light: ShadeLightState | None = None
    target_light: ShadeLightState | None = None

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> ShadeEvent | None:
        """Parse supported events, ignoring future event types."""
        event_type = data.get("evt")
        if event_type not in ("motion-started", "motion-stopped"):
            return None
        shade_id = data[ATTR_ID]
        if isinstance(shade_id, bool) or not isinstance(shade_id, int):
            raise ValueError("Invalid shade id")
        timestamp = datetime.fromisoformat(data["isoDate"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("Event timestamp must include a timezone")
        target = None
        target_light = None
        eta = None
        if event_type == "motion-started":
            target_data = data["targetPositions"]
            target = _positions(target_data)
            target_light = ShadeLightState.from_data(target_data.get("light"))
            if not target and target_light is None:
                raise ValueError("Empty target positions")
            eta = target_data.get("etaInSeconds")
            if eta is not None and (
                isinstance(eta, bool)
                or not isinstance(eta, (int, float))
                or not math.isfinite(eta)
                or eta < 0
            ):
                raise ValueError("Invalid ETA")
        current = _positions(data["currentPositions"])
        current_light = ShadeLightState.from_data(data["currentPositions"].get("light"))
        if not current and current_light is None:
            raise ValueError("Empty current positions")
        return cls(
            event_type,
            shade_id,
            timestamp,
            current,
            target,
            eta,
            current_light,
            target_light,
        )


class ShadeEventStream:
    """One Gen3 SSE connection using the caller's HTTP session.

    Fetch the current shade snapshot before entering the context, including on
    reconnect: connecting does not return state and has no replay guarantee.
    Cancellation or context exit closes only the streaming response.
    """

    def __init__(self, request: AioRequest) -> None:
        """Initialize the stream without opening a connection."""
        if request.api_version != 3:
            raise ValueError("Shade event streaming requires a Gen3 gateway")
        self._request = request
        self._response: aiohttp.ClientResponse | None = None

    async def __aenter__(self) -> ShadeEventStream:
        """Open the stream without the normal request's total timeout."""
        if self._response is not None:
            raise RuntimeError("Stream is already connected")
        try:
            response = await self._request.websession.get(
                get_base_path(self._request.hub_ip, "home/shades/events"),
                params={"sse": "true", "raw": "true"},
                headers={"Accept": "text/event-stream"},
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=15),
            )
        except (aiohttp.ClientError, TimeoutError) as error:
            raise PvApiConnectionError("Unable to open shade event stream") from error
        if response.status != 200:
            response.close()
            raise PvApiResponseStatusError(response.status)
        self._response = response
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the connection on error, cancellation, or unload."""
        if self._response is not None:
            self._response.close()
            self._response = None

    async def events(self) -> AsyncIterator[ShadeEvent]:
        """Yield motion events from blank-line-delimited SSE data fields."""
        if self._response is None:
            raise RuntimeError("Stream is not connected")
        data_lines: list[str] = []
        size = 0
        try:
            async for raw_line in self._response.content:
                size += len(raw_line)
                if size > _MAX_EVENT_SIZE:
                    raise PvApiConnectionError("Shade event exceeded maximum size")
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line:
                    field, separator, value = line.partition(":")
                    if field == "data" and separator:
                        data_lines.append(value.removeprefix(" "))
                    continue
                if data_lines:
                    try:
                        data = json.loads("\n".join(data_lines))
                        if not isinstance(data, dict):
                            raise ValueError("Event is not an object")
                        event = ShadeEvent.from_data(data)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        _LOGGER.debug("Ignoring malformed shade event")
                    else:
                        if event is not None:
                            yield event
                data_lines = []
                size = 0
        except (aiohttp.ClientError, TimeoutError, UnicodeError, ValueError) as error:
            raise PvApiConnectionError("Shade event stream interrupted") from error
        raise PvApiConnectionError("Shade event stream closed")


class ShadeEventState:
    """Merge stream positions into an existing shade snapshot.

    Create a new instance for each connection: gateway clocks can change across
    reboots. Ordering is tracked per shade, rather than across the whole home.
    """

    def __init__(self, data: PowerviewShadeData) -> None:
        """Initialize with the snapshot fetched for this connection."""
        self.data = data
        self.motion: dict[int, ShadeEvent] = {}

    def apply(self, event: ShadeEvent) -> bool:
        """Merge a known shade's latest event using its existing conversions."""
        if event.shade_id not in self.data.get_all_raw_data():
            return False
        previous = self.motion.get(event.shade_id)
        if previous is not None and (
            event.timestamp < previous.timestamp or event == previous
        ):
            return False
        raw = self.data.get_raw_data(event.shade_id)
        raw[ATTR_POSITIONS].update(event.current_positions)
        if event.current_light is not None:
            light = raw[ATTR_POSITIONS].get("light") or {}
            raw[ATTR_POSITIONS]["light"] = {**light, **event.current_light.as_dict()}
        shade = self.data.get_shade(event.shade_id)
        shade.raw_data = raw
        self.data.update_shade_position(event.shade_id, shade.current_position)
        self.motion[event.shade_id] = event
        return True

    def target_position(self, shade_id: int) -> ShadePosition | None:
        """Return the target through the same shade-specific conversions."""
        event = self.motion.get(shade_id)
        if event is None or not event.target_positions:
            return None
        shade = self.data.get_shade(shade_id)
        return shade.get_additional_positions(
            shade.raw_to_structured({ATTR_POSITIONS: event.target_positions})
        )

    def target_light(self, shade_id: int) -> ShadeLightState | None:
        """Return the requested light state separately from reported state."""
        event = self.motion.get(shade_id)
        return event.target_light if event is not None else None
