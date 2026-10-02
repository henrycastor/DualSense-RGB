#!/usr/bin/env python3

"""DualSense device discovery, USB topology, and RGB extraction."""

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from dualsense_rgb.constants import (
    DUALSENSE_IDS,
    HIDRAW_DIR,
    DUALSENSE_REPORT_ID,
    LIGHTBAR,
)

logger = logging.getLogger(__name__)

__all__ = [
    "is_dualsense_uevent",
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


def read_uevent(path: "Path") -> Optional[str]:
    """Read a sysfs uevent file, returning None on error."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as file:
            return file.read()
    except OSError:
        return None


def is_dualsense_uevent(content: str) -> bool:
    """Return True if sysfs uevent content identifies a DualSense.

    The same sysfs hierarchy backs both the hidraw node and the input
    event nodes, so every device match funnels through this single
    predicate: match the Sony vendor id, then accept either the
    DualSense or the DualSense Edge product id.

    Matching is case-insensitive because sysfs reports ids inconsistently
    across kernels (e.g. ``PRODUCT=054c/0ce6/...`` vs. upper-case ids in
    ``HID_ID=0003:054C:0CE6``).
    """
    content_upper = content.upper()

    if DUALSENSE_IDS.SONY_VID not in content_upper:
        return False

    return (
        DUALSENSE_IDS.DUALSENSE_PID in content_upper
        or DUALSENSE_IDS.DUALSENSE_EDGE_PID in content_upper
    )


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

    if not HIDRAW_DIR.is_dir():
        return None

    for node in sorted(HIDRAW_DIR.iterdir(), key=lambda p: p.name):

        uevent_path = node / "device" / "uevent"

        content = read_uevent(uevent_path)

        if content is None:
            continue

        if is_dualsense_uevent(content):
            return str(Path("/dev") / node.name)

    return None


# ============================================================
# USB topology
# ============================================================


def get_usb_topology(hidraw_path: str) -> Optional[Tuple[int, int]]:
    """
    Resolve the USB bus/device address from the hidraw
    sysfs hierarchy.
    """

    node = Path(hidraw_path).name

    try:
        current = (HIDRAW_DIR / node / "device").resolve()
    except RuntimeError as exc:
        logger.error("Failed to resolve sysfs path: %s", exc)
        return None

    while current != Path("/"):

        uevent_path = current / "uevent"

        content = read_uevent(uevent_path)

        if content is not None:
            topology = parse_bus_device(content)

            if topology is not None:
                return topology

        parent = current.parent

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
    if not (data[2] & LIGHTBAR.ENABLE_BIT):
        return None

    red = data[LIGHTBAR.RED]
    green = data[LIGHTBAR.GREEN]
    blue = data[LIGHTBAR.BLUE]

    return (red, green, blue)
