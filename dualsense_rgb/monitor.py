#!/usr/bin/env python3

"""Monitor DualSense lightbar changes via usbmon and forward to OpenRGB."""

import fcntl
import logging
import os
import time
from typing import Optional, Tuple

from dualsense_rgb.constants import (
    DUALSENSE_HID_OUT_ENDPOINT,
    UsbTransferType,
    IDLE_TIMEOUT,
    POLL_INTERVAL,
)
from dualsense_rgb.device.usbmon import open_usbmon, get_usbmon_packet, UsbmonEndpoint
from dualsense_rgb.device.dualsense import (
    find_dualsense_hidraw,
    get_usb_topology,
    get_usb_topology_udevadm,
    extract_dualsense_rgb,
)
from dualsense_rgb.bridge.openrgb import connect_openrgb, update_openrgb
from dualsense_rgb.bridge.evdev import (
    IdleTracker,
    find_dualsense_input_device,
    start_event_reader,
    start_idle_inhibitor,
    stop_event_reader,
    stop_idle_inhibitor,
)

logger = logging.getLogger(__name__)

__all__ = ["is_lightbar_packet", "monitor_dualsense", "main"]


# ============================================================
# Packet filtering
# ============================================================


def is_lightbar_packet(packet, bus: int, device: int) -> bool:
    """Return True if a usbmon packet carries a DualSense lightbar report.

    A packet qualifies only when all of the following hold:

    * it comes from the DualSense' own bus and device address,
    * it targets HID OUT endpoint ``DUALSENSE_HID_OUT_ENDPOINT``
      (``usbmon``'s ``epnum`` packs the direction into bit 7, so the
      raw value for OUT endpoint 3 is 3, not 131),
    * it travels OUT rather than IN, and
    * it is an interrupt transfer. ``usbmon`` ``xfer_type`` values are
      0 = ISO, 1 = Interrupt, 2 = Control, 3 = Bulk.
    """
    if packet.bus != bus:
        return False

    if packet.device != device:
        return False

    endpoint = UsbmonEndpoint.from_packet(packet)

    if endpoint.number != DUALSENSE_HID_OUT_ENDPOINT:
        return False

    if endpoint.is_in:
        return False

    if packet.transfer_type != UsbTransferType.INTERRUPT:
        return False

    return True


# ============================================================
# Monitor DualSense
# ============================================================


def monitor_dualsense(
    fd: int,
    bus: int,
    device: int,
    openrgb_client,
    idle_timeout: float = IDLE_TIMEOUT,
    input_device: Optional[str] = None,
) -> None:
    """Capture usbmon events and forward DualSense lightbar color to OpenRGB.

    Idle inhibition is driven by real DualSense input events (via
    libevdev) when ``input_device`` is provided. The inhibitor is
    started immediately (the controller is assumed to be in use) and
    stopped when no activity has been seen for ``idle_timeout`` seconds.
    """

    logger.info("Bus:       %03d", bus)
    logger.info("Device:    %03d", device)
    logger.info("Endpoint:  OUT %d", DUALSENSE_HID_OUT_ENDPOINT)
    logger.info("Capturing full usbmon binary transfers...")
    logger.info("Waiting for DualSense lightbar changes...")
    logger.info(
        "Idle detection: screen off after %.1fs of inactivity.", idle_timeout
    )
    if input_device is not None:
        logger.info("Input device: %s", input_device)

    # Put the usbmon fd in non-blocking mode so the monitor loop can
    # detect idle periods (no traffic) instead of blocking forever on
    # the next packet.
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except OSError as exc:
        logger.warning("Could not set usbmon fd non-blocking: %s", exc)

    idle = IdleTracker(idle_timeout)

    # Start the libevdev event reader (if available) so real input
    # events drive the idle tracker. This is best-effort: if pyevdev
    # is missing or the device cannot be opened we fall back to the
    # usbmon-traffic-based idle detection below.
    event_thread = None
    if input_device is not None:
        event_thread = start_event_reader(input_device, idle)

    # The controller is in use at startup, so keep the session awake.
    start_idle_inhibitor()

    last_rgb: Optional[Tuple[int, int, int]] = None

    try:
        while True:
            try:
                packet, data = get_usbmon_packet(fd)
            except OSError as exc:
                logger.error("Failed to read usbmon packet: %s", exc)
                continue

            if packet is None:
                # No traffic right now. Check whether the DualSense has
                # gone idle (screen off) and avoid busy-spinning.
                if idle.update():
                    logger.info("DualSense is now IDLE (screen off).")
                    stop_idle_inhibitor()
                time.sleep(POLL_INTERVAL)
                continue

            # ------------------------------------------------
            # Device filter
            # ------------------------------------------------

            if packet.bus != bus or packet.device != device:
                continue

            # Any traffic from our DualSense counts as activity, so the
            # screen stays on while the controller is in use.
            if idle.mark_activity():
                logger.info("DualSense is now ACTIVE (screen on).")
            # start() is idempotent: restart the inhibitor on every observed
            # activity, not just on the idle->active transition. This covers
            # the case where the event-reader thread already cleared the idle
            # flag (so mark_activity() above returns False), the case where
            # the inhibitor process died while the controller was already
            # active, and the normal transition case.
            start_idle_inhibitor()

            # ------------------------------------------------
            # Endpoint / transfer-type filter
            # ------------------------------------------------

            if not is_lightbar_packet(packet, bus, device):
                continue

            if not data:
                continue

            # ------------------------------------------------
            # Diagnostic information
            # ------------------------------------------------

            logger.debug("DualSense HID OUT: transfer=%d bytes", len(data))
            logger.debug("  %s", data.hex(" "))

            # ------------------------------------------------
            # RGB
            # ------------------------------------------------

            rgb = extract_dualsense_rgb(data)

            if rgb is None:
                logger.debug("  No lightbar color in this report.")
                continue

            if rgb == last_rgb:
                continue

            last_rgb = rgb

            red, green, blue = rgb

            logger.info("")
            logger.info("==============================================")
            logger.info(" DualSense LIGHTBAR")
            logger.info("==============================================")
            logger.info(" R = %d", red)
            logger.info(" G = %d", green)
            logger.info(" B = %d", blue)
            logger.info("==============================================")
            logger.info("")

            update_openrgb(openrgb_client, rgb)

    except KeyboardInterrupt:
        logger.info("Stopping monitor...")

    except Exception as exc:
        logger.error("Unexpected error in monitor loop: %s", exc, exc_info=True)

    finally:
        # Stop the libevdev event reader and the idle inhibitor.
        stop_event_reader(event_thread)
        stop_idle_inhibitor()

        try:
            os.close(fd)
        except OSError:
            pass


# ============================================================
# Main
# ============================================================


def main(idle_timeout: float = IDLE_TIMEOUT) -> None:
    """Entry point: discover DualSense, open usbmon, and start monitoring."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("==============================================")
    logger.info(" DualSense -> OpenRGB")
    logger.info(" USB HID Lightbar Bridge")
    logger.info("==============================================")
    logger.info("")

    # --------------------------------------------------------
    # Find DualSense
    # --------------------------------------------------------

    hidraw_path = find_dualsense_hidraw()

    if hidraw_path is None:
        logger.error("DualSense not detected.")
        return

    logger.info("DualSense HID: %s", hidraw_path)

    # --------------------------------------------------------
    # Find USB topology
    # --------------------------------------------------------

    topology = get_usb_topology(hidraw_path)

    if topology is None:
        logger.info("Sysfs topology lookup failed. Trying udevadm...")
        topology = get_usb_topology_udevadm(hidraw_path)

    if topology is None:
        logger.error("Could not determine USB topology.")
        return

    bus, device = topology

    logger.info("USB topology: Bus %03d, Device %03d", bus, device)

    # --------------------------------------------------------
    # Find the DualSense input event node (libevdev idle detection)
    # --------------------------------------------------------

    input_device = find_dualsense_input_device()

    if input_device is None:
        logger.warning(
            "DualSense input event node not found. Idle inhibition will "
            "fall back to usbmon traffic detection only."
        )

    # --------------------------------------------------------
    # Open usbmon binary interface
    # --------------------------------------------------------

    usbmon_fd = open_usbmon(bus)

    if usbmon_fd is None:
        return

    logger.info("Opened /dev/usbmon%d", bus)

    # --------------------------------------------------------
    # OpenRGB
    # --------------------------------------------------------

    openrgb_client = connect_openrgb()

    if openrgb_client is None:
        try:
            os.close(usbmon_fd)
        except OSError:
            pass
        return

    # --------------------------------------------------------
    # Monitor
    # --------------------------------------------------------

    try:
        monitor_dualsense(
            usbmon_fd,
            bus,
            device,
            openrgb_client,
            idle_timeout,
            input_device=input_device,
        )
    finally:
        # Ensure the OpenRGB client is cleaned up.
        try:
            openrgb_client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
