#!/usr/bin/env python3

"""Tests for dualsense_rgb.device.usbmon module."""

import struct

from dualsense_rgb.constants import (
    USBMON_HEADER_FORMAT,
    UsbTransferType,
)
from dualsense_rgb.device.usbmon import (
    parse_usbmon_header,
    UsbmonPacket,
    UsbmonEndpoint,
    _ioc,
)


class TestIoc:
    def test_ioc_read(self) -> None:
        result = _ioc(2, 0x92, 10, 24)
        assert isinstance(result, int)
        assert result > 0

    def test_ioc_write(self) -> None:
        result = _ioc(1, 0x92, 10, 24)
        assert isinstance(result, int)
        assert result > 0


class TestParseUsbmonHeader:
    def test_valid_header(self) -> None:
        header = struct.pack(
            USBMON_HEADER_FORMAT,
            1,      # id
            2,      # type
            UsbTransferType.INTERRUPT,  # transfer_type
            3,      # endpoint
            4,      # device
            5,      # bus
            b"\x00", # flag_setup
            b"\x01", # flag_data
            1000,   # ts_sec
            500000, # ts_usec
            0,      # status
            64,     # length
            64,     # captured_length
            b"\x00" * 16,  # setup_area
            1,      # interval
            0,      # start_frame
            0,      # xfer_flags
            0,      # num_desc
        )
        result = parse_usbmon_header(header)
        assert result is not None
        assert result.id == 1
        assert result.type == 2
        assert result.transfer_type == UsbTransferType.INTERRUPT
        assert result.endpoint == 3
        assert result.device == 4
        assert result.bus == 5
        assert result.timestamp == (1000, 500000)
        assert result.length == 64
        assert result.captured_length == 64

    def test_short_header(self) -> None:
        result = parse_usbmon_header(b"\x00" * 10)
        assert result is None


class TestUsbmonEndpoint:
    def test_out_endpoint(self) -> None:
        packet = UsbmonPacket(
            id=1, type=2, transfer_type=UsbTransferType.INTERRUPT,
            endpoint=3, device=4, bus=5,
            flag_setup=0, flag_data=1,
            timestamp=(0, 0), status=0, length=0,
            captured_length=0, interval=0, start_frame=0,
            xfer_flags=0, num_desc=0,
        )
        ep = UsbmonEndpoint.from_packet(packet)
        assert ep.number == 3
        assert ep.is_in is False

    def test_in_endpoint(self) -> None:
        packet = UsbmonPacket(
            id=1, type=2, transfer_type=UsbTransferType.INTERRUPT,
            endpoint=0x84, device=4, bus=5,  # 0x84 = EP 4 IN
            flag_setup=0, flag_data=1,
            timestamp=(0, 0), status=0, length=0,
            captured_length=0, interval=0, start_frame=0,
            xfer_flags=0, num_desc=0,
        )
        ep = UsbmonEndpoint.from_packet(packet)
        assert ep.number == 4
        assert ep.is_in is True
