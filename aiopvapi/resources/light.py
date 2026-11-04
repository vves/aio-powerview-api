"""Light state for illuminated PowerView shades."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ShadeLightState:
    """Reported brightness in percent and color temperature in Kelvin."""

    brightness: int | float | None = None
    temperature: int | float | None = None

    @classmethod
    def from_data(cls, data: object) -> ShadeLightState | None:
        """Parse optional light state without assuming an unknown light is off."""
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError("Invalid light state")
        brightness = data.get("brightness")
        temperature = data.get("temperature")
        for value in (brightness, temperature):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("Invalid light value")
        if brightness is not None and not 0 <= brightness <= 100:
            raise ValueError("Invalid light brightness")
        if temperature is not None and not 2200 <= temperature <= 6500:
            raise ValueError("Invalid light temperature")
        if brightness is None and temperature is None:
            raise ValueError("Empty light state")
        return cls(brightness, temperature)

    def as_dict(self) -> dict[str, int | float]:
        """Return only known values for merging partial updates."""
        return {
            key: value
            for key, value in (
                ("brightness", self.brightness),
                ("temperature", self.temperature),
            )
            if value is not None
        }
