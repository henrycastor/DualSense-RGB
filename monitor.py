#!/usr/bin/env python3

import logging
import os

from constants import DUALSENSE_HID_OUT_ENDPOINT
from usbmon import open_usbmon, get_usbmon_packet
from dualsense import (
    find_dualsense_hidraw,
    get_usb_topology,
    get_usb_topology_udevadm,
    extract_dualsense_rgb,
)
from openrgb_bridge import connect_openrgb, update_openrgb

logger = logging.getLogger(__name__)


# ============================================================
# Monitor DualSense
# ============================================================

def monitor_dualsense(fd, bus, device, openrgb):

    logger.info("Bus:       %03d", bus)
    logger.info("Device:    %03d", device)
    logger.info("Endpoint:  OUT %d", DUALSENSE_HID_OUT_ENDPOINT)
    logger.info("Capturing full usbmon binary transfers...")
    logger.info("Waiting for DualSense lightbar changes...")

    last_rgb = None

    try:
        while True:
            packet, data = get_usbmon_packet(fd)

            if packet is None:
                continue

            # ------------------------------------------------
            # Device filter
            # ------------------------------------------------

            if packet["bus"] != bus:
                continue

            if packet["device"] != device:
                continue

            # ------------------------------------------------
            # Endpoint filter
            #
            # usbmon epnum contains the endpoint number and
            # direction. For OUT endpoint 3 this should be 3.
            # ------------------------------------------------

            endpoint = packet["endpoint"]
            endpoint_number = endpoint & 0x7F
            endpoint_is_in = bool(endpoint & 0x80)

            if endpoint_number != DUALSENSE_HID_OUT_ENDPOINT:
                continue

            if endpoint_is_in:
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

            if packet["transfer_type"] != 1:
                continue

            if not data:
                continue

            # ------------------------------------------------
            # Only inspect reports beginning with 0x02.
            # ------------------------------------------------

            if data[0] != 0x02:
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

            update_openrgb(openrgb, rgb)

    except KeyboardInterrupt:
        logger.info("Stopping monitor...")

    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# ============================================================
# Main
# ============================================================

def main():

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

    openrgb = connect_openrgb()

    if openrgb is None:
        os.close(usbmon_fd)
        return

    # --------------------------------------------------------
    # Monitor
    # --------------------------------------------------------

    monitor_dualsense(usbmon_fd, bus, device, openrgb)


if __name__ == "__main__":
    main()
