#!/usr/bin/env python3

"""Low-level usbmon binary API for capturing DualSense USB traffic."""

import ctypes
import fcntl
import logging
import os
import struct
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from dualsense_rgb.constants import (
    MON_IOC_MAGIC,
    MON_IOCX_GETX_NR,
    MON_GET_ARG_SIZE,
    USBMON_HEADER_FORMAT,
    USBMON_HEADER_SIZE,
    USBMON_DEV_TEMPLATE,
    DATA_BUFFER_SIZE,
    ENDPOINT_NUMBER_MASK,
    ENDPOINT_DIRECTION_MASK,
)

logger = logging.getLogger(__name__)

__all__ = [
    "open_usbmon",
    "get_usbmon_packet",
    "parse_usbmon_header",
    "UsbmonPacket",
    "UsbmonEndpoint",
]


# ============================================================
# usbmon binary API
# ============================================================


def _ioc(direction: int, ioctl_type: int, number: int, size: int) -> int:
    """
    Linux _IOC() helper.

    Direction:
        0 = none
        1 = write
        2 = read
        3 = read/write
    """

    IOC_NRBITS = 8
    IOC_TYPEBITS = 8
    IOC_SIZEBITS = 14

    IOC_NRSHIFT = 0
    IOC_TYPESHIFT = IOC_NRSHIFT + IOC_NRBITS
    IOC_SIZESHIFT = IOC_TYPESHIFT + IOC_TYPEBITS
    IOC_DIRSHIFT = IOC_SIZESHIFT + IOC_SIZEBITS

    return (
        (direction << IOC_DIRSHIFT)
        | (ioctl_type << IOC_TYPESHIFT)
        | (number << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )


MON_IOCX_GETX = _ioc(
    1,                  # _IOW
    MON_IOC_MAGIC,
    MON_IOCX_GETX_NR,
    MON_GET_ARG_SIZE,
)


# ============================================================
# usbmon packet dataclass
# ============================================================


@dataclass
class UsbmonPacket:
    """Decoded 72-byte usbmon packet header."""

    id: int
    type: int
    transfer_type: int
    endpoint: int
    device: int
    bus: int
    flag_setup: int
    flag_data: int
    timestamp: Tuple[int, int]
    status: int
    length: int
    captured_length: int
    interval: int
    start_frame: int
    xfer_flags: int
    num_desc: int


@dataclass
class UsbmonEndpoint:
    """Parsed endpoint information from a usbmon packet."""

    number: int
    is_in: bool

    @classmethod
    def from_packet(cls, packet: UsbmonPacket) -> "UsbmonEndpoint":
        """Create an UsbmonEndpoint from a UsbmonPacket."""
        endpoint = packet.endpoint
        return cls(
            number=endpoint & ENDPOINT_NUMBER_MASK,
            is_in=bool(endpoint & ENDPOINT_DIRECTION_MASK),
        )


# ============================================================
# Parse usbmon binary header
# ============================================================


def parse_usbmon_header(header: bytes) -> Optional[UsbmonPacket]:
    """Decode the 72-byte usbmon packet header."""

    if len(header) < USBMON_HEADER_SIZE:
        return None

    values = struct.unpack(
        USBMON_HEADER_FORMAT,
        header[:USBMON_HEADER_SIZE],
    )

    (
        packet_id,
        packet_type,
        transfer_type,
        endpoint,
        device,
        bus,
        flag_setup,
        flag_data,
        ts_sec,
        ts_usec,
        status,
        length,
        captured_length,
        setup_area,
        interval,
        start_frame,
        xfer_flags,
        num_desc,
    ) = values

    return UsbmonPacket(
        id=packet_id,
        type=packet_type,
        transfer_type=transfer_type,
        endpoint=endpoint,
        device=device,
        bus=bus,
        flag_setup=flag_setup,
        flag_data=flag_data,
        timestamp=(ts_sec, ts_usec),
        status=status,
        length=length,
        captured_length=captured_length,
        interval=interval,
        start_frame=start_frame,
        xfer_flags=xfer_flags,
        num_desc=num_desc,
    )


# ============================================================
# Open usbmon binary device
# ============================================================


def _load_usbmon_module() -> bool:
    """Attempt to load the usbmon kernel module via modprobe."""
    try:
        result = subprocess.run(
            ["sudo", "modprobe", "usbmon"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_usbmon(bus: int) -> Optional[int]:
    """
    Open /dev/usbmonN.

    The binary usbmon API is required because the text API can
    truncate captured data before the RGB bytes at the end of
    the DualSense report.
    """

    path = USBMON_DEV_TEMPLATE.format(bus)

    if not os.path.exists(path):
        logger.warning("%s does not exist. Attempting to load usbmon module...", path)
        if _load_usbmon_module():
            logger.info("usbmon module loaded successfully.")
        else:
            logger.error("Failed to load usbmon module. Check: sudo modprobe usbmon")

        if not os.path.exists(path):
            logger.error("%s still does not exist after loading usbmon.", path)
            logger.info("Then:  ls -l %s", path)
            return None

    try:
        fd = os.open(path, os.O_RDWR)
        return fd

    except PermissionError:
        logger.error("Permission denied opening %s. Run this script with sudo.", path)

    except OSError as exc:
        logger.error("Could not open %s: %s", path, exc)

    return None


# ============================================================
# Get next usbmon packet
# ============================================================


def get_usbmon_packet(fd: int) -> Tuple[Optional[UsbmonPacket], bytes]:
    """
    Read the next usbmon binary event.

    Returns:
        (header, data)
    """

    header_buffer = ctypes.create_string_buffer(USBMON_HEADER_SIZE)
    data_buffer = ctypes.create_string_buffer(DATA_BUFFER_SIZE)

    header_address = ctypes.addressof(header_buffer)
    data_address = ctypes.addressof(data_buffer)

    # struct mon_get_arg
    #
    # hdr
    # data
    # alloc
    #
    arg = struct.pack(
        "QQQ",
        header_address,
        data_address,
        DATA_BUFFER_SIZE,
    )

    arg_buffer = bytearray(arg)

    try:
        fcntl.ioctl(
            fd,
            MON_IOCX_GETX,
            arg_buffer,
            True,
        )

    except InterruptedError:
        return None, b""

    except BlockingIOError:
        return None, b""

    except OSError as exc:
        # EAGAIN is possible with some configurations.
        if getattr(exc, "errno", None) == 11:
            return None, b""
        logger.error("Unexpected ioctl error: %s", exc)
        return None, b""

    header = bytes(header_buffer.raw)

    parsed = parse_usbmon_header(header)

    if parsed is None:
        return None, b""

    captured_length = parsed.captured_length

    if captured_length <= 0:
        return parsed, b""

    if captured_length > DATA_BUFFER_SIZE:
        captured_length = DATA_BUFFER_SIZE

    data = bytes(data_buffer.raw[:captured_length])

    return parsed, data
