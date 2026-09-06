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
    ENDPOINT_NUMBER_MASK,
    ENDPOINT_DIRECTION_MASK,
    IDLE_TIMEOUT,
    POLL_INTERVAL,
)
from dualsense_rgb.device.usbmon import open_usbmon, get_usbmon_packet, UsbmonPacket, UsbmonEndpoint
from dualsense_rgb.device.dualsense import (
    find_dualsense_hidraw,
    get_usb_topology,
    get_usb_topology_udevadm,
    extract_dualsense_rgb,
)
from dualsense_rgb.bridge.openrgb import connect_openrgb, update_openrgb
from dualsense_rgb.bridge.wljoywake import start_wljoywake, stop_wljoywake

logger = logging.getLogger(__name__)

__all__ = ["monitor_dualsense", "main", "IdleTracker"]


# ============================================================
# Idle tracking
# ============================================================


class IdleTracker:
    """Track whether the DualSense is idle or active based on activity.

    The controller is considered idle once no traffic has been seen for
    ``idle_timeout`` seconds. While active the "screen" is considered on;
    once idle it is considered off.
    """

    def __init__(self, idle_timeout: float, clock=time.monotonic) -> None:
        self.idle_timeout = idle_timeout
        self._clock = clock
        self.last_activity = clock()
        self.is_idle = False

    def mark_activity(self) -> bool:
        """Record activity from the DualSense.

        Returns True if the controller was idle and is now active again.
        """
        was_idle = self.is_idle
        self.last_activity = self._clock()
        self.is_idle = False
        return was_idle

    def update(self) -> bool:
        """Check for a transition into the idle state.

        Returns True if the controller just became idle.
        """
        if self.is_idle:
            return False
        if (self._clock() - self.last_activity) >= self.idle_timeout:
            self.is_idle = True
            return True
        return False


# ============================================================
# Monitor DualSense
# ============================================================


def monitor_dualsense(
    fd: int,
    bus: int,
    device: int,
    openrgb_client,
    idle_timeout: float = IDLE_TIMEOUT,
) -> None:
    """Capture usbmon events and forward DualSense lightbar color to OpenRGB."""

    logger.info("Bus:       %03d", bus)
    logger.info("Device:    %03d", device)
    logger.info("Endpoint:  OUT %d", DUALSENSE_HID_OUT_ENDPOINT)
    logger.info("Capturing full usbmon binary transfers...")
    logger.info("Waiting for DualSense lightbar changes...")
    logger.info(
        "Idle detection: screen off after %.1fs of inactivity.", idle_timeout
    )

    # Put the usbmon fd in non-blocking mode so the monitor loop can
    # detect idle periods (no traffic) instead of blocking forever on
    # the next packet.
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except OSError as exc:
        logger.warning("Could not set usbmon fd non-blocking: %s", exc)

    idle = IdleTracker(idle_timeout)

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
                    stop_wljoywake()
                time.sleep(POLL_INTERVAL)
                continue

            # ------------------------------------------------
            # Device filter
            # ------------------------------------------------

            if packet.bus != bus:
                continue

            if packet.device != device:
                continue

            # Any traffic from our DualSense counts as activity, so the
            # screen stays on while the controller is in use.
            if idle.mark_activity():
                logger.info("DualSense is now ACTIVE (screen on).")
                start_wljoywake()

            # ------------------------------------------------
            # Endpoint filter
            #
            # usbmon epnum contains the endpoint number and
            # direction. For OUT endpoint 3 this should be 3.
            # ------------------------------------------------

            endpoint = UsbmonEndpoint.from_packet(packet)

            if endpoint.number != DUALSENSE_HID_OUT_ENDPOINT:
                continue

            if endpoint.is_in:
                continue

            # ------------------------------------------------
            # We want interrupt transfers.
            #
            # usbmon xfer_type:
            #
            # 0 = ISO
            # 1 = Interrupt
            # 2 = Control
            # 3 = Bulk
            # ------------------------------------------------

            if packet.transfer_type != UsbTransferType.INTERRUPT:
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
        monitor_dualsense(usbmon_fd, bus, device, openrgb_client, idle_timeout)
    finally:
        # Ensure the OpenRGB client is cleaned up.
        try:
            openrgb_client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
