#!/usr/bin/env python3

"""Tests for dualsense_rgb.device.dualsense module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dualsense_rgb.constants import DUALSENSE_IDS
from dualsense_rgb.device.dualsense import (
    read_uevent,
    parse_bus_device,
    find_dualsense_hidraw,
    get_usb_topology,
    get_usb_topology_udevadm,
    extract_dualsense_rgb,
)


class TestReadUevent:
    def test_read_existing_file(self, tmp_path: Path) -> None:
        uevent = tmp_path / "uevent"
        uevent.write_text("BUSNUM=3\nDEVNUM=5\n")
        result = read_uevent(uevent)
        assert result == "BUSNUM=3\nDEVNUM=5\n"

    def test_read_missing_file(self, tmp_path: Path) -> None:
        result = read_uevent(tmp_path / "missing")
        assert result is None


class TestParseBusDevice:
    def test_valid_content(self) -> None:
        result = parse_bus_device("BUSNUM=3\nDEVNUM=5\n")
        assert result == (3, 5)

    def test_missing_busnum(self) -> None:
        result = parse_bus_device("DEVNUM=5\n")
        assert result is None

    def test_missing_devnum(self) -> None:
        result = parse_bus_device("BUSNUM=3\n")
        assert result is None

    def test_empty_content(self) -> None:
        result = parse_bus_device("")
        assert result is None


class TestFindDualSenseHidraw:
    @patch("dualsense_rgb.device.dualsense.HIDRAW_DIR")
    def test_find_dualsense(self, mock_hidraw_dir: MagicMock, tmp_path: Path) -> None:
        # Setup mock directory structure
        hidraw_node = tmp_path / "hidraw0"
        hidraw_node.mkdir()
        device_dir = hidraw_node / "device"
        device_dir.mkdir()
        uevent = device_dir / "uevent"
        uevent.write_text(
            f"HID_ID=0003:{DUALSENSE_IDS.SONY_VID}:{DUALSENSE_IDS.DUALSENSE_PID}\n"
        )

        mock_hidraw_dir.is_dir.return_value = True
        mock_hidraw_dir.iterdir.return_value = [hidraw_node]

        with patch("dualsense_rgb.device.dualsense.HIDRAW_DIR", tmp_path):
            result = find_dualsense_hidraw()

        assert result == "/dev/hidraw0"

    @patch("dualsense_rgb.device.dualsense.HIDRAW_DIR")
    def test_no_dualsense(self, mock_hidraw_dir: MagicMock) -> None:
        mock_hidraw_dir.is_dir.return_value = False
        result = find_dualsense_hidraw()
        assert result is None


class TestExtractDualSenseRgb:
    def test_valid_report(self) -> None:
        data = bytearray(48)
        data[0] = 0x02  # Report ID
        data[2] = 0x04  # Lightbar enable bit
        data[45] = 255  # Red
        data[46] = 128  # Green
        data[47] = 64   # Blue
        result = extract_dualsense_rgb(bytes(data))
        assert result == (255, 128, 64)

    def test_empty_data(self) -> None:
        result = extract_dualsense_rgb(b"")
        assert result is None

    def test_wrong_report_id(self) -> None:
        data = bytearray(48)
        data[0] = 0x01  # Wrong report ID
        data[2] = 0x04
        data[45] = 255
        result = extract_dualsense_rgb(bytes(data))
        assert result is None

    def test_lightbar_disabled(self) -> None:
        data = bytearray(48)
        data[0] = 0x02
        data[2] = 0x00  # Lightbar disabled
        data[45] = 255
        result = extract_dualsense_rgb(bytes(data))
        assert result is None

    def test_short_data(self) -> None:
        data = bytearray(10)
        data[0] = 0x02
        data[2] = 0x04
        result = extract_dualsense_rgb(bytes(data))
        assert result is None
