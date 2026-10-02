#!/usr/bin/env python3

"""Shared constants for the DualSense -> OpenRGB bridge."""

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


# ============================================================
# DualSense identification
# ============================================================


@dataclass(frozen=True)
class DualSenseIds:
    SONY_VID: str = "054C"
    DUALSENSE_PID: str = "0CE6"
    DUALSENSE_EDGE_PID: str = "0DF2"


DUALSENSE_IDS = DualSenseIds()

HIDRAW_DIR = Path("/sys/class/hidraw")
USBMON_DEV_TEMPLATE = "/dev/usbmon{}"

# Your USB descriptor:
#
# Interface 3:
#   EP 4 IN
#   EP 3 OUT
#
DUALSENSE_HID_OUT_ENDPOINT = 3

# DualSense output report IDs
DUALSENSE_REPORT_ID = 0x02

# ============================================================
# Idle detection
# ============================================================

# How long (seconds) without any DualSense HID OUT traffic before the
# controller is considered idle (screen off).
IDLE_TIMEOUT = 30.0

# Polling interval (seconds) used when no usbmon data is available so the
# monitor does not busy-spin while waiting for traffic.
POLL_INTERVAL = 0.05

# ============================================================
# libevdev (pyevdev) idle inhibition
# ============================================================

# Directory containing /dev/input/eventN nodes. The DualSense's
# corresponding event node is discovered from the hidraw sysfs
# hierarchy, so we do not hard-code a device number here.
INPUT_DEV_DIR = Path("/dev/input")

# /sys/class/input/eventN is where the per-input-node sysfs tree lives.
# NOTE: /dev/input/eventN is a character device node, not a directory,
# so the "device" symlink must be read under /sys/class/input, not
# under /dev/input.
INPUT_SYS_DIR = Path("/sys/class/input")

# Idle-inhibitor commands, tried in order until one actually stays
# alive. Each runs a blocking command (sleep infinity) for the
# lifetime of the inhibitor.
INHIBIT_BLOCKING_CMD = ["sleep", "infinity"]
INHIBIT_REASON = "DualSense RGB idle inhibitor"

# On GNOME/Wayland the compositor's idle (screen lock) handling is
# driven by org.gnome.SessionManager, so systemd-inhibit --what=idle
# is NOT honored by the lock. gnome-session-inhibit --inhibit idle is
# the tool that actually prevents the screen from locking on GNOME.
GNOME_SESSION_INHIBIT_CMD = [
    "gnome-session-inhibit",
    "--inhibit",
    "idle",
    "--reason=" + INHIBIT_REASON,
] + INHIBIT_BLOCKING_CMD

# Portable fallback for non-GNOME / systemd-based sessions.
SYSTEMD_INHIBIT_CMD = [
    "systemd-inhibit",
    "--what=idle:sleep",
    "--mode=block",
    "--who=DualSense RGB",
    "--why=" + INHIBIT_REASON,
] + INHIBIT_BLOCKING_CMD

INHIBIT_CMDS = [GNOME_SESSION_INHIBIT_CMD, SYSTEMD_INHIBIT_CMD]


@dataclass(frozen=True)
class LightbarOffsets:
    ENABLE_BIT = 0x04
    RED = 45
    GREEN = 46
    BLUE = 47


LIGHTBAR = LightbarOffsets()

# ============================================================
# usbmon binary API
# ============================================================

MON_IOC_MAGIC = 0x92
MON_IOCX_GETX_NR = 10

# struct mon_get_arg:
#
# struct mon_get_arg {
#     struct usbmon_packet *hdr;
#     void *data;
#     size_t alloc;
# };
#
# On normal 64-bit Linux this is 24 bytes.
#
MON_GET_ARG_SIZE = 24  # struct.calcsize("QQQ")

# ============================================================
# usbmon packet header
# ============================================================

# Linux usbmon_packet:
#
# offset 0:
#     u64 id
#
# offset 8:
#     u8 type
#
# offset 9:
#     u8 xfer_type
#
# offset 10:
#     u8 epnum
#
# offset 11:
#     u8 devnum
#
# offset 12:
#     u16 busnum
#
# offset 14:
#     char flag_setup
#
# offset 15:
#     char flag_data
#
# offset 16:
#     s64 ts_sec
#
# offset 24:
#     s32 ts_usec
#
# offset 28:
#     int status
#
# offset 32:
#     unsigned int length
#
# offset 36:
#     unsigned int len_cap
#
# offset 40:
#     setup/iso data
#
# offset 48:
#     int interval
#
# offset 52:
#     int start_frame
#
# offset 56:
#     unsigned int xfer_flags
#
# offset 60:
#     unsigned int ndesc
#
# Total: 72 bytes
#
USBMON_HEADER_FORMAT = (
    "<QBBBBHccqiiII"
    "16s"
    "iiII"
)

USBMON_HEADER_SIZE = 72  # struct.calcsize(USBMON_HEADER_FORMAT)

# ============================================================
# USB transfer types
# ============================================================


class UsbTransferType(IntEnum):
    ISO = 0
    INTERRUPT = 1
    CONTROL = 2
    BULK = 3


# ============================================================
# Endpoint parsing
# ============================================================

ENDPOINT_NUMBER_MASK = 0x7F
ENDPOINT_DIRECTION_MASK = 0x80

# ============================================================
# usbmon data buffer
# ============================================================

DATA_BUFFER_SIZE = 4096
