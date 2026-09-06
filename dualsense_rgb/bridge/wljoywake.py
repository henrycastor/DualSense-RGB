#!/usr/bin/env python3

"""Manage the wljoywake subprocess for Wayland idle inhibition."""

import logging
import os
import signal
import subprocess
from typing import List, Optional

from dualsense_rgb.constants import WLJOYWAKE_CMD

logger = logging.getLogger(__name__)

__all__ = ["start_wljoywake", "stop_wljoywake", "is_wljoywake_running"]

_wljoywake_process: Optional[subprocess.Popen] = None


def start_wljoywake(cmd: Optional[List[str]] = None) -> None:
    """Start wljoywake as a background process.

    If wljoywake is already running, this is a no-op.
    """
    global _wljoywake_process

    if cmd is None:
        cmd = WLJOYWAKE_CMD

    if _wljoywake_process is not None and _wljoywake_process.poll() is None:
        logger.debug("wljoywake is already running (PID %d).", _wljoywake_process.pid)
        return

    try:
        _wljoywake_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Started wljoywake (PID %d).", _wljoywake_process.pid)
    except FileNotFoundError:
        logger.error(
            "wljoywake not found. Install it to enable screen wake on joystick activity."
        )
        _wljoywake_process = None
    except Exception as exc:
        logger.error("Failed to start wljoywake: %s", exc)
        _wljoywake_process = None


def stop_wljoywake() -> None:
    """Stop the wljoywake process if it is running."""
    global _wljoywake_process

    if _wljoywake_process is None:
        return

    try:
        if _wljoywake_process.poll() is None:
            # Process is still running — terminate the whole process group.
            os.killpg(os.getpgid(_wljoywake_process.pid), signal.SIGTERM)
            logger.info("Stopped wljoywake (PID %d).", _wljoywake_process.pid)
    except ProcessLookupError:
        pass  # Process already exited.
    except Exception as exc:
        logger.error("Failed to stop wljoywake: %s", exc)
    finally:
        _wljoywake_process = None


def is_wljoywake_running() -> bool:
    """Return True if wljoywake is currently running."""
    if _wljoywake_process is None:
        return False
    return _wljoywake_process.poll() is None
