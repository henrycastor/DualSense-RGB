#!/usr/bin/env python3

"""Tests for the packet filter extracted from dualsense_rgb.monitor."""

from dualsense_rgb.constants import (
    DUALSENSE_HID_OUT_ENDPOINT,
    ENDPOINT_DIRECTION_MASK,
    UsbTransferType,
)
from dualsense_rgb.device.usbmon import UsbmonPacket
from dualsense_rgb.monitor import is_lightbar_packet

BUS = 1
DEVICE = 4


def _packet(
    *,
    bus: int = BUS,
    device: int = DEVICE,
    endpoint: int = DUALSENSE_HID_OUT_ENDPOINT,
    transfer_type: int = UsbTransferType.INTERRUPT,
) -> UsbmonPacket:
    """Build a usbmon packet with only the fields the filter inspects."""
    return UsbmonPacket(
        id=0,
        type=0,
        transfer_type=transfer_type,
        endpoint=endpoint,
        device=device,
        bus=bus,
        flag_setup=0,
        flag_data=0,
        timestamp=(0, 0),
        status=0,
        length=0,
        captured_length=0,
        interval=0,
        start_frame=0,
        xfer_flags=0,
        num_desc=0,
    )


class TestIsLightbarPacket:
    def test_accepts_interrupt_out_transfer(self) -> None:
        assert is_lightbar_packet(_packet(), BUS, DEVICE) is True

    def test_rejects_other_bus(self) -> None:
        assert is_lightbar_packet(_packet(bus=9), BUS, DEVICE) is False

    def test_rejects_other_device(self) -> None:
        assert is_lightbar_packet(_packet(device=7), BUS, DEVICE) is False

    def test_rejects_other_endpoint(self) -> None:
        assert is_lightbar_packet(_packet(endpoint=2), BUS, DEVICE) is False

    def test_rejects_in_transfer_on_same_endpoint(self) -> None:
        # Same endpoint number, but the direction bit (0x80) is set, so
        # this is an IN transfer and must not be treated as a lightbar
        # report.
        in_endpoint = DUALSENSE_HID_OUT_ENDPOINT | ENDPOINT_DIRECTION_MASK
        assert is_lightbar_packet(_packet(endpoint=in_endpoint), BUS, DEVICE) is False

    def test_rejects_non_interrupt_transfer(self) -> None:
        for transfer_type in (
            UsbTransferType.ISO,
            UsbTransferType.CONTROL,
            UsbTransferType.BULK,
        ):
            packet = _packet(transfer_type=transfer_type)
            assert is_lightbar_packet(packet, BUS, DEVICE) is False

    def test_endpoint_number_is_unpacked_from_epnum(self) -> None:
        # usbmon packs the direction into bit 7 of epnum; the endpoint
        # number itself must still compare equal to OUT endpoint 3.
        packet = _packet(endpoint=DUALSENSE_HID_OUT_ENDPOINT)
        assert is_lightbar_packet(packet, BUS, DEVICE) is True
