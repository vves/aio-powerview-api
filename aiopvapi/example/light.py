"""Send a test Aura light command and observe confirmation with the SSE monitor."""

import argparse
import asyncio

import aiohttp

from aiopvapi.helpers.aiorequest import AioRequest
from aiopvapi.resources.shade import ShadeDualOverlappedIlluminated
from aiopvapi.shades import Shades


async def set_light(
    host: str,
    shade_id: int,
    brightness: float,
    temperature: int | float,
    rate_in_seconds: int | float,
) -> None:
    """Read the shade inventory and send one light command to an Aura shade."""
    async with aiohttp.ClientSession() as session:
        request = AioRequest(
            host,
            loop=asyncio.get_running_loop(),
            websession=session,
            api_version=3,
        )
        snapshot = await Shades(request).get_shades()
        shade = snapshot.processed.get(shade_id)
        if not isinstance(shade, ShadeDualOverlappedIlluminated):
            raise ValueError(f"Shade {shade_id} is not an Aura illuminated shade")
        await shade.set_light(
            brightness=brightness,
            temperature=temperature,
            rate_in_seconds=rate_in_seconds,
        )
        print("Light command sent; check the SSE monitor for reported state.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Gateway hostname or IP address")
    parser.add_argument("shade_id", type=int)
    parser.add_argument(
        "--brightness", type=float, required=True, help="Brightness fraction (0.0–1.0)"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        required=True,
        help="Color temperature (2200–6500 K)",
    )
    parser.add_argument(
        "--rate-in-seconds",
        type=float,
        required=True,
        help="Transition duration in seconds",
    )
    args = parser.parse_args()
    asyncio.run(
        set_light(
            args.host,
            args.shade_id,
            args.brightness,
            args.temperature,
            args.rate_in_seconds,
        )
    )
