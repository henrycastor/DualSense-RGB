#!/usr/bin/env python3

"""Tests for dualsense_rgb.bridge.openrgb module."""

from unittest.mock import MagicMock, patch

import pytest

from dualsense_rgb.bridge.openrgb import (
    _find_openrgb_executable,
    _kill_openrgb_processes,
    _try_start_openrgb,
    _start_openrgb_server,
    connect_openrgb,
    update_openrgb,
)


class TestFindOpenRgbExecutable:
    @patch("dualsense_rgb.bridge.openrgb.shutil.which")
    def test_find_in_path(self, mock_which: MagicMock) -> None:
        mock_which.return_value = "/usr/bin/OpenRGB"
        result = _find_openrgb_executable()
        assert result == "/usr/bin/OpenRGB"

    @patch("dualsense_rgb.bridge.openrgb.shutil.which")
    def test_not_found(self, mock_which: MagicMock) -> None:
        mock_which.return_value = None
        result = _find_openrgb_executable()
        assert result is None


class TestUpdateOpenRgb:
    def test_update_devices(self) -> None:
        client = MagicMock()
        device1 = MagicMock()
        device1.leds = [MagicMock(), MagicMock()]
        device2 = MagicMock()
        device2.leds = [MagicMock()]
        client.devices = [device1, device2]

        update_openrgb(client, (255, 128, 64))

        assert device1.set_colors.call_count == 1
        assert device2.set_colors.call_count == 1

    def test_update_no_leds(self) -> None:
        client = MagicMock()
        device = MagicMock()
        device.leds = []
        client.devices = [device]

        update_openrgb(client, (255, 128, 64))

        device.set_colors.assert_not_called()
