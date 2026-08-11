#!/usr/bin/env python3

import os
import re

from constants import (
    SONY_VID,
    DUALSENSE_PID,
    DUALSENSE_EDGE_PID,
    HIDRAW_DIR,
)


# ============================================================
# Find DualSense hidraw
# ============================================================

def find_dualsense_hidraw():
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

        if not os.path.isfile(uevent_path):
            continue

        try:
            with open(
                uevent_path,
                "r",
                encoding="utf-8",
                errors="ignore",
            ) as file:

                content = file.read().upper()

        except OSError:
            continue

        if SONY_VID not in content:
            continue

        if (
            DUALSENSE_PID in content
            or DUALSENSE_EDGE_PID in content
        ):
            return os.path.join(
                "/dev",
                node,
            )

    return None


# ============================================================
# USB topology
# ============================================================

def get_usb_topology(hidraw_path):
    """
    Resolve the USB bus/device address from the hidraw
    sysfs hierarchy.
    """

    node = os.path.basename(
        hidraw_path
    )

    current = os.path.realpath(
        os.path.join(
            HIDRAW_DIR,
            node,
            "device",
        )
    )

    while current != "/":

        uevent_path = os.path.join(
            current,
            "uevent",
        )

        if os.path.isfile(uevent_path):

            try:

                with open(
                    uevent_path,
                    "r",
                    encoding="utf-8",
                    errors="ignore",
                ) as file:

                    content = file.read()

            except OSError:

                content = ""

            bus_match = re.search(
                r"BUSNUM=(\d+)",
                content,
            )

            dev_match = re.search(
                r"DEVNUM=(\d+)",
                content,
            )

            if bus_match and dev_match:

                return (
                    int(bus_match.group(1)),
                    int(dev_match.group(1)),
                )

        parent = os.path.dirname(
            current
        )

        if parent == current:
            break

        current = parent

    return None


def get_usb_topology_udevadm(hidraw_path):
    """Fallback USB topology lookup."""

    try:

        result = os.popen(
            f"udevadm info --query=property "
            f"--name={hidraw_path}"
        ).read()

    except Exception:
        return None

    bus = None
    device = None

    for line in result.splitlines():

        if line.startswith("BUSNUM="):

            try:
                bus = int(
                    line.split("=", 1)[1]
                )
            except ValueError:
                pass

        elif line.startswith("DEVNUM="):

            try:
                device = int(
                    line.split("=", 1)[1]
                )
            except ValueError:
                pass

    if bus is not None and device is not None:
        return bus, device

    return None


# ============================================================
# DualSense RGB extraction
# ============================================================

def extract_dualsense_rgb(data):
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
    if data[0] != 0x02:
        return None

    # Need the complete RGB section.
    if len(data) < 48:
        return None

    # Common output valid_flag1.
    #
    # Bit 2 enables lightbar control.
    if not (
        data[2] & 0x04
    ):
        return None

    red = data[45]
    green = data[46]
    blue = data[47]

    return (
        red,
        green,
        blue,
    )
