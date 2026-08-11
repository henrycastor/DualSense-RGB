#!/usr/bin/env python3

"""DualSense device discovery, USB topology, and RGB extraction."""

import os
import re
import subprocess
from typing import Optional, Tuple

from constants import (
    SONY_VID,
    DUALSENSE_PID,
    DUALSENSE_EDGE_PID,
    HIDRAW_DIR,
    DUALSENSE_REPORT_ID,
    LIGHTBAR_ENABLE_BIT,
    LIGHTBAR_RED_OFFSET,
    LIGHTBAR_GREEN_OFFSET,
    LIGHTBAR_BLUE_OFFSET,
)

__all__ = [
    "find_dualsense_hidraw",
    "get_usb_topology",
    "get_usb_topology_udevadm",
    "extract_dualsense_rgb",
    "read_uevent",
    "parse_bus_device",
]


# ============================================================
# Helpers
# ============================================================


def read_uevent(path: str) -> Optional[str]:
    """Read a sysfs uevent file, returning None on error."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as file:
            return file.read()
    except OSError:
        return None


def parse_bus_device(content: str) -> Optional[Tuple[int, int]]:
    """Extract (bus, device) from uevent content or udevadm output."""
    bus_match = re.search(r"BUSNUM=(\d+)", content)
    dev_match = re.search(r"DEVNUM=(\d+)", content)

    if bus_match and dev_match:
        return int(bus_match.group(1)), int(dev_match.group(1))

    return None


# ============================================================
# Find DualSense hidraw
# ============================================================


def find_dualsense_hidraw() -> Optional[str]:
    """Find the DualSense hidraw node using sysfs."""

    if not os.path.isdir(HIDRAW_DIR):
        return None

    for node in os.listdir(HIDRAW_DIR):

        uevent_path = os.path.join(
            HIDRAW_DIR,
            node,
            "device",
            "uevent",
        )

        content = read_uevent(uevent_path)

        if content is None:
            continue

        content_upper = content.upper()

        if SONY_VID not in content_upper:
            continue

        if (
            DUALSENSE_PID in content_upper
            or DUALSENSE_EDGE_PID in content_upper
        ):
            return os.path.join(
                "/dev",
                node,
            )

    return None


# ============================================================
# USB topology
# ============================================================


def get_usb_topology(hidraw_path: str) -> Optional[Tuple[int, int]]:
    """
    Resolve the USB bus/device address from the hidraw
    sysfs hierarchy.
    """

    node = os.path.basename(hidraw_path)

    current = os.path.realpath(
        os.path.join(
            HIDRAW_DIR,
            node,
            "device",
        )
    )

    while current != "/":

        uevent_path = os.path.join(current, "uevent")

        content = read_uevent(uevent_path)

        if content is not None:
            topology = parse_bus_device(content)

            if topology is not None:
                return topology

        parent = os.path.dirname(current)

        if parent == current:
            break

        current = parent

    return None


def get_usb_topology_udevadm(hidraw_path: str) -> Optional[Tuple[int, int]]:
    """Fallback USB topology lookup using udevadm."""

    try:
        result = subprocess.run(
            [
                "udevadm",
                "info",
                "--query=property",
                "--name",
                hidraw_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout

    except Exception:
        return None

    return parse_bus_device(result)


# ============================================================
# DualSense RGB extraction
# ============================================================


def extract_dualsense_rgb(data: bytes) -> Optional[Tuple[int, int, int]]:
    """
    Extract RGB from the wired DualSense output report.

    Linux hid-playstation defines the USB output report as:

        byte 0 = report ID 0x02

    The common output structure begins immediately after the
    report ID.

    The lightbar fields are:

        byte 45 = red
        byte 46 = green
        byte 47 = blue

    The lightbar control bit is:

        byte 2, bit 2
    """

    if not data:
        return None

    # USB DualSense main output report.
    if data[0] != DUALSENSE_REPORT_ID:
        return None

    # Need the complete RGB section.
    if len(data) < 48:
        return None

    # Common output valid_flag1.
    #
    # Bit 2 enables lightbar control.
    if not (data[2] & LIGHTBAR_ENABLE_BIT):
        return None

    red = data[LIGHTBAR_RED_OFFSET]
    green = data[LIGHTBAR_GREEN_OFFSET]
    blue = data[LIGHTBAR_BLUE_OFFSET]

    return (red, green, blue)
