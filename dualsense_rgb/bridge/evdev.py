#!/usr/bin/env python3

"""libevdev (pyevdev) idle inhibition for the DualSense controller.

Reads DualSense input events directly through libevdev and inhibits
session idle (sleep / screen lock) in-process via ``systemd-inhibit``.

The DualSense's ``/dev/input/eventX`` node is discovered from the hidraw
sysfs hierarchy, so the device is matched by vendor/product id rather
than by a hard-coded event number.
"""

import logging
import os
import signal
import subprocess
import threading
import time
from typing import Callable, Optional

from dualsense_rgb.constants import (
    DUALSENSE_IDS,
    INHIBIT_CMDS,
    INPUT_DEV_DIR,
    INPUT_SYS_DIR,
)
from dualsense_rgb.device.dualsense import read_uevent

logger = logging.getLogger(__name__)

__all__ = [
    "IdleTracker",
    "find_dualsense_input_device",
    "GamepadIdleInhibitor",
    "start_idle_inhibitor",
    "stop_idle_inhibitor",
    "is_idle_inhibitor_running",
    "start_event_reader",
    "stop_event_reader",
]


# ============================================================
# Idle tracking
# ============================================================


class IdleTracker:
    """Track whether the DualSense is idle or active based on activity.

    The controller is considered idle once no activity has been seen for
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
# libevdev device discovery
# ============================================================


def _is_dualsense_event(uevent_content: str) -> bool:
    """Return True if the given uevent content identifies a DualSense."""
    content_upper = uevent_content.upper()
    if DUALSENSE_IDS.SONY_VID not in content_upper:
        return False
    return (
        DUALSENSE_IDS.DUALSENSE_PID in content_upper
        or DUALSENSE_IDS.DUALSENSE_EDGE_PID in content_upper
    )


def find_dualsense_input_device() -> Optional[str]:
    """Find the DualSense ``/dev/input/eventX`` node via sysfs.

    The hidraw node and the input event node share the same underlying
    USB device, so we walk the sysfs ``device`` symlink from each event
    node and match against the DualSense vendor/product ids.

    The ``device`` symlink lives under ``/sys/class/input/eventN`` —
    NOT under ``/dev/input/eventN``, which is a character device node
    and has no ``device`` subdirectory. Returns the matching
    ``/dev/input/eventX`` path, or the first match if several event
    nodes belong to the same controller.
    """

    if not INPUT_SYS_DIR.is_dir():
        return None

    matches = []
    for node in sorted(INPUT_SYS_DIR.iterdir(), key=lambda p: p.name):
        if not node.name.startswith("event"):
            continue

        # eventN/device is the USB device behind this input node.
        uevent_path = node / "device" / "uevent"

        content = read_uevent(uevent_path)
        if content is None:
            continue

        if _is_dualsense_event(content):
            matches.append(str(INPUT_DEV_DIR / node.name))

    if not matches:
        return None

    # A single USB controller exposes several event nodes (main gamepad,
    # motion sensors, touchpad, headset jack) that all share the same
    # USB device. Return the lowest-numbered one, which is conventionally
    # the main gamepad node.
    return matches[0]


def _ev_syn_type(evdev) -> int:
    """Resolve the ``EV_SYN`` event type across evdev builds.

    PyPI ``pyevdev`` exposes it at top level (``evdev.EV_SYN``); Debian's
    ``python-evdev`` exposes it under ``evdev.ecodes.EV_SYN``. Fall back
    to the raw integer (0) if neither is available.
    """
    for attr in ("EV_SYN",):
        value = getattr(evdev, attr, None)
        if isinstance(value, int):
            return value
    ecodes = getattr(evdev, "ecodes", None)
    if ecodes is not None:
        value = getattr(ecodes, "EV_SYN", None)
        if isinstance(value, int):
            return value
    return 0  # EV_SYN is always 0 in the Linux input event API.


# ============================================================
# Idle inhibitor (systemd-inhibit managed subprocess)
# ============================================================


class GamepadIdleInhibitor:
    """Manage an idle-inhibitor subprocess that blocks session idle.

    The inhibitor is started when the controller is active and stopped
    when it goes idle. Several candidate commands are tried in order
    (GNOME-aware first, then a portable systemd fallback); the first
    one that actually stays alive wins. This handles both invalid
    options and compositors whose idle handling is not driven by
    systemd (e.g. GNOME/Wayland uses org.gnome.SessionManager).
    """

    # Seconds to wait after spawning a candidate before checking it
    # did not immediately exit (e.g. due to an unknown option).
    _START_GRACE = 0.5

    def __init__(self, cmds=None) -> None:
        if cmds is None:
            cmds = list(INHIBIT_CMDS)
        elif isinstance(cmds, str):
            cmds = [cmds]
        else:
            cmds = [list(c) for c in cmds]
        self._cmds = cmds
        self._process: Optional[subprocess.Popen] = None
        self._active_cmd = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def active_command(self) -> Optional[list]:
        """The inhibitor command that is currently running, if any."""
        return self._active_cmd

    def start(self) -> None:
        """Start the inhibitor if it is not already running."""
        with self._lock:
            if self.is_running:
                logger.debug("Idle inhibitor is already running (PID %d).", self._process.pid)
                return

            for cmd in self._cmds:
                process = self._try_spawn(cmd)
                if process is not None:
                    self._process = process
                    self._active_cmd = cmd
                    logger.info("Started idle inhibitor via %s (PID %d).", cmd[0], process.pid)
                    return

            logger.error(
                "No idle inhibitor could be started. Tried: %s",
                [c[0] for c in self._cmds],
            )

    def _try_spawn(self, cmd) -> Optional[subprocess.Popen]:
        """Spawn one inhibitor candidate, returning it only if it stays alive."""
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            logger.debug("Idle inhibitor %s not found.", cmd[0])
            return None
        except Exception as exc:
            logger.error("Failed to start idle inhibitor %s: %s", cmd[0], exc)
            return None

        # A bad option (e.g. unknown flag) makes the process exit
        # immediately. Give it a moment and only accept it if it is
        # still alive.
        try:
            process.wait(timeout=self._START_GRACE)
        except subprocess.TimeoutExpired:
            pass  # Still running — good.
        else:
            logger.warning(
                "Idle inhibitor %s exited immediately (rc=%s); trying next.",
                cmd[0],
                process.returncode,
            )
            return None

        return process

    def stop(self) -> None:
        """Stop the inhibitor if it is running."""
        with self._lock:
            if self._process is None:
                return

            try:
                if self._process.poll() is None:
                    # Terminate the whole process group.
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                    logger.info("Stopped idle inhibitor (PID %d).", self._process.pid)
            except ProcessLookupError:
                pass  # Process already exited.
            except Exception as exc:
                logger.error("Failed to stop idle inhibitor: %s", exc)
            finally:
                self._process = None
                self._active_cmd = None


# Module-level inhibitor instance.
_inhibitor = GamepadIdleInhibitor()


def start_idle_inhibitor(cmd=None) -> None:
    """Start the idle inhibitor. See ``GamepadIdleInhibitor.start``.

    ``cmd`` may be a single command list or a list of command lists.
    """
    if cmd is not None:
        global _inhibitor
        _inhibitor = GamepadIdleInhibitor(cmd)
    _inhibitor.start()


def stop_idle_inhibitor() -> None:
    """Stop the idle inhibitor. See ``GamepadIdleInhibitor.stop``."""
    _inhibitor.stop()


def is_idle_inhibitor_running() -> bool:
    """Return True if the idle inhibitor is currently running."""
    return _inhibitor.is_running


# ============================================================
# Input event reader
# ============================================================


def _drain_events(device, idle: IdleTracker, stop_event) -> None:
    """Read input events from ``device`` and feed them to ``idle``.

    Runs in a daemon thread. ``EV_SYN`` events are noise and do not
    count as activity. Any other event type marks the controller as
    active.
    """

    try:
        import evdev  # type: ignore[import-not-found]
    except ImportError:
        logger.error("pyevdev (evdev) is not installed. Cannot read input events.")
        return

    ev_syn = _ev_syn_type(evdev)

    while not stop_event.is_set():
        try:
            events = device.read()
            if not events:
                stop_event.wait(0.05)
                continue
            # Some evdev builds raise BlockingIOError (EAGAIN) while
            # iterating the returned events, not just from read() itself.
            for event in events:
                if event.type == ev_syn:
                    continue
                idle.mark_activity()
        except (OSError, BlockingIOError):
            # No events available right now — avoid busy-spinning.
            stop_event.wait(0.05)
            continue
        except Exception as exc:
            logger.error("Error reading input events: %s", exc)
            stop_event.wait(0.1)
            continue


# ============================================================
# Event reader thread
# ============================================================


def start_event_reader(
    device_path: str,
    idle: IdleTracker,
    on_error: Optional[Callable[[str], None]] = None,
) -> Optional[threading.Thread]:
    """Open the DualSense input device and drain events in a daemon thread.

    The thread feeds real input events into ``idle`` so the idle
    inhibitor can be toggled. Returns the thread (already started), or
    None if the device could not be opened.
    """

    try:
        import evdev  # type: ignore[import-not-found]
    except ImportError:
        msg = "pyevdev (evdev) is not installed. Cannot read input events."
        logger.error(msg)
        if on_error is not None:
            on_error(msg)
        return None

    try:
        device = evdev.InputDevice(device_path)
    except OSError as exc:
        msg = f"Could not open {device_path}: {exc}"
        logger.error(msg)
        if on_error is not None:
            on_error(msg)
        return None

    # Read-only, non-grabbing (the default): the compositor / game
    # still receives the events, we just observe them. Some evdev
    # builds expose grab()/ungrab() while others expose grab(state);
    # we never need to grab, so leave the device in its default
    # (non-grabbed) state to stay compatible across builds.

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_event_reader,
        args=(device, idle, stop_event),
        name="dualsense-evdev",
        daemon=True,
    )
    thread._dualsense_stop_event = stop_event  # type: ignore[attr-defined]
    thread.start()
    logger.info("Started evdev event reader for %s.", device_path)
    return thread


def _run_event_reader(device, idle: IdleTracker, stop_event) -> None:
    """Thread body that wraps ``_drain_events`` with cleanup."""
    try:
        _drain_events(device, idle, stop_event)
    finally:
        try:
            device.close()
        except Exception:
            pass


def stop_event_reader(thread: Optional[threading.Thread]) -> None:
    """Signal an event-reader thread to stop and wait for it to exit."""
    if thread is None:
        return
    stop_event = getattr(thread, "_dualsense_stop_event", None)
    if stop_event is not None:
        stop_event.set()
    thread.join(timeout=2.0)