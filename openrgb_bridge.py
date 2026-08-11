#!/usr/bin/env python3

"""Bridge between DualSense lightbar colors and OpenRGB devices."""

import logging
from typing import Optional, Tuple

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

logger = logging.getLogger(__name__)

__all__ = ["connect_openrgb", "update_openrgb"]


# ============================================================
# OpenRGB
# ============================================================


def connect_openrgb() -> Optional[OpenRGBClient]:
    """Connect to the local OpenRGB SDK server."""

    try:
        client = OpenRGBClient()
        logger.info("Connected to OpenRGB SDK server.")
        return client

    except ConnectionRefusedError:
        logger.error("OpenRGB SDK server is not available. Enable the OpenRGB SDK server.")

    except Exception as exc:
        logger.error("OpenRGB connection failed: %s", exc)

    return None


def update_openrgb(client: OpenRGBClient, rgb: Tuple[int, int, int]) -> None:
    """Apply an RGB color to all LEDs on all OpenRGB devices."""

    red, green, blue = rgb

    try:
        color = RGBColor(red, green, blue)

        for device in client.devices:
            if not device.leds:
                continue

            device.set_colors([color] * len(device.leds))

    except Exception as exc:
        logger.error("OpenRGB update failed: %s", exc)
