"""Read a Gen3 snapshot and shade events without sending movement commands."""

import argparse
import asyncio
import logging

import aiohttp

from aiopvapi.events import ShadeEventState, ShadeEventStream
from aiopvapi.helpers.aiorequest import AioRequest, PvApiError
from aiopvapi.resources.light import ShadeLightState
from aiopvapi.resources.shade import ShadeDualOverlappedIlluminated
from aiopvapi.resources.shade_data import PowerviewShadeData
from aiopvapi.shades import Shades

_LOGGER = logging.getLogger(__name__)


async def monitor(host: str) -> None:
    """Fetch a snapshot on every connection and print reported shade changes."""
    delay = 1
    async with aiohttp.ClientSession() as session:
        request = AioRequest(
            host,
            loop=asyncio.get_running_loop(),
            websession=session,
            api_version=3,
        )
        while True:
            try:
                snapshot = await Shades(request).get_shades()
                data = PowerviewShadeData()
                data.store_group_data(snapshot)
                state = ShadeEventState(data)
                async with ShadeEventStream(request) as stream:
                    for shade_id in snapshot.processed:
                        print(
                            f"snapshot shade={shade_id} "
                            f"position={data.get_shade_position(shade_id)} "
                            f"light={get_light_state(data, shade_id)}",
                            flush=True,
                        )
                    async for event in stream.events():
                        delay = 1
                        if state.apply(event):
                            print(
                                f"{event.timestamp.isoformat()} "
                                f"shade={event.shade_id} {event.event_type} "
                                f"position={data.get_shade_position(event.shade_id)} "
                                f"target={state.target_position(event.shade_id)} "
                                f"light={get_light_state(data, event.shade_id)} "
                                f"target_light={state.target_light(event.shade_id)}",
                                flush=True,
                            )
            except PvApiError as error:
                _LOGGER.warning("%s; reconnecting in %ss", error, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def get_light_state(data: PowerviewShadeData, shade_id: int) -> ShadeLightState | None:
    """Return light state for shades that support lighting."""
    shade = data.get_shade(shade_id)
    if isinstance(shade, ShadeDualOverlappedIlluminated):
        return shade.current_light
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Gateway hostname or IP address")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(monitor(args.host))
    except KeyboardInterrupt:
        pass
