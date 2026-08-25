#!/usr/bin/env python3

"""Bridge between DualSense lightbar colors and OpenRGB devices."""

import logging
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

logger = logging.getLogger(__name__)

__all__ = ["connect_openrgb", "update_openrgb"]


# ============================================================
# OpenRGB
# ============================================================


def _find_openrgb_executable() -> Optional[str]:
    """Try to locate the OpenRGB executable on the system."""
    candidates = [
        "OpenRGB",
        "openrgb",
        "/usr/bin/OpenRGB",
        "/usr/local/bin/OpenRGB",
        "/opt/OpenRGB/OpenRGB",
        "~/OpenRGB/OpenRGB",
        "/usr/games/OpenRGB",
        "/usr/share/OpenRGB/OpenRGB",
    ]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def _kill_openrgb_processes(executable: str) -> None:
    """Kill any running OpenRGB processes."""
    try:
        subprocess.run(
            ["pkill", "-f", executable],
            capture_output=True,
            text=True,
        )
        # Wait for processes to actually terminate.
        time.sleep(1)
    except OSError:
        pass


def _try_start_openrgb(executable: str, args: List[str]) -> bool:
    """Try to start OpenRGB with the given arguments and wait for the SDK server."""
    proc = None
    try:
        proc = subprocess.Popen(
            [executable] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except Exception as exc:
        logger.error("Failed to start OpenRGB with %s: %s", args, exc)
        return False

    for _ in range(10):
        time.sleep(1)
        try:
            client = OpenRGBClient()
            client.disconnect()
            logger.info("OpenRGB SDK server started successfully with %s.", args)
            return True
        except Exception:
            continue

    # If we get here, the server didn't start. Check if the process is still running.
    if proc is not None:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            logger.error("OpenRGB process exited prematurely.")
            if stdout:
                logger.error("stdout: %s", stdout.decode(errors="replace"))
            if stderr:
                logger.error("stderr: %s", stderr.decode(errors="replace"))
        else:
            # Process is still running but server didn't start — kill it.
            try:
                proc.kill()
                proc.wait()
            except OSError:
                pass

    return False


def _start_openrgb_server() -> bool:
    """Attempt to start the OpenRGB SDK server."""
    executable = _find_openrgb_executable()
    if executable is None:
        logger.error("OpenRGB executable not found. Install OpenRGB to use this feature.")
        logger.error("On Debian/Ubuntu: sudo apt install openrgb")
        logger.error("On Arch: sudo pacman -S openrgb")
        logger.error("Or download from: https://openrgb.org/")
        return False

    logger.info("Found OpenRGB executable: %s", executable)

    # Try common server flags
    server_args = [
        ["--server"],
        ["-s"],
        ["--server", "--headless"],
        ["--server", "--no-gui"],
    ]

    for args in server_args:
        logger.info("Trying to start OpenRGB SDK server: %s %s", executable, " ".join(args))
        if _try_start_openrgb(executable, args):
            return True

    # If none of the flags worked, an existing OpenRGB instance might be blocking it.
    # Kill any existing processes and try again with --server.
    logger.warning("OpenRGB SDK server did not start. Retrying after killing existing processes...")
    _kill_openrgb_processes(executable)

    if _try_start_openrgb(executable, ["--server"]):
        return True

    logger.error("OpenRGB SDK server could not be started.")
    logger.error("Please start OpenRGB manually and enable the SDK server in its settings.")
    logger.error("OpenRGB -> Settings -> SDK Server -> Enable")
    return False


def connect_openrgb() -> Optional[OpenRGBClient]:
    """Connect to the local OpenRGB SDK server."""

    try:
        client = OpenRGBClient()
        logger.info("Connected to OpenRGB SDK server.")
        return client

    except ConnectionRefusedError:
        logger.warning("OpenRGB SDK server is not available. Attempting to start it...")
        if _start_openrgb_server():
            try:
                client = OpenRGBClient()
                logger.info("Connected to OpenRGB SDK server after starting it.")
                return client
            except Exception as exc:
                logger.error("OpenRGB connection failed after starting server: %s", exc)
        else:
            logger.error("OpenRGB SDK server is not available. Enable the OpenRGB SDK server.")

    except Exception as exc:
        logger.error("OpenRGB connection failed: %s", exc)

    return None


def update_openrgb(client: OpenRGBClient, rgb: Tuple[int, int, int]) -> None:
    """Apply an RGB color to all LEDs on all OpenRGB devices."""

    red, green, blue = rgb

    try:
        color = RGBColor(red, green, blue)

        for device in client.devices:
            if not device.leds:
                continue

            device.set_colors([color] * len(device.leds))

    except Exception as exc:
        logger.error("OpenRGB update failed: %s", exc)
