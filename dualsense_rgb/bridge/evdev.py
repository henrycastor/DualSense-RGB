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
import sys
import threading
import time
from typing import Callable, Optional

from dualsense_rgb.constants import (
    ENV_BLOCKLIST,
    ENV_NULL,
    INHIBIT_CMDS,
    INPUT_DEV_DIR,
    INPUT_SYS_DIR,
    PROC_ENV,
    SESSION_ENV_KEYS,
)
from dualsense_rgb.device.dualsense import is_dualsense_uevent, read_uevent

logger = logging.getLogger(__name__)

__all__ = [
    "EventReader",
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


def _event_node_index(node) -> int:
    """Return the numeric index of an ``eventN`` sysfs node.

    A sort key is required rather than a plain string sort because
    ``event10`` sorts *before* ``event2`` lexicographically, which would
    break the "lowest-numbered node wins" contract below on any system
    with ten or more input nodes. Nodes whose name is not of the form
    ``event<N>`` sort last, so a malformed entry can never outrank a real
    one.
    """
    name = node.name
    suffix = name[len("event"):] if name.startswith("event") else ""

    if not suffix.isdigit():
        return sys.maxsize

    return int(suffix)


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

    best_node = None

    for node in INPUT_SYS_DIR.iterdir():
        if not node.name.startswith("event"):
            continue

        # eventN/device is the USB device behind this input node.
        uevent_path = node / "device" / "uevent"

        content = read_uevent(uevent_path)
        if content is None:
            continue

        if not is_dualsense_uevent(content):
            continue

        # A single USB controller exposes several event nodes (main
        # gamepad, motion sensors, touchpad, headset jack) that all share
        # the same USB device. The lowest-numbered one is conventionally
        # the main gamepad node.
        if best_node is None or _event_node_index(node) < _event_node_index(best_node):
            best_node = node

    if best_node is None:
        return None

    return str(INPUT_DEV_DIR / best_node.name)


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


def _read_proc_env(pid: int) -> dict:
    """Read a process's environment from ``/proc/<pid>/environ``.

    ``/proc/<pid>/environ`` separates entries with NUL bytes. Returns an
    empty dict if the file cannot be read (e.g. the process is gone or
    the caller lacks permission), so callers can safely merge the
    result.
    """
    environ_path = PROC_ENV / str(pid) / "environ"
    try:
        with open(environ_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return {}

    if not raw:
        return {}

    env = {}
    for entry in raw.split(ENV_NULL.encode("utf-8")):
        if not entry:
            continue
        try:
            key, _, value = entry.decode("utf-8", "replace").partition("=")
        except Exception:
            continue
        if key and key not in ENV_BLOCKLIST:
            env[key] = value
    return env


def _recover_session_env() -> Optional[dict]:
    """Recover the user session environment for idle-inhibitor subprocesses.

    The bridge is normally launched via ``sudo`` (it needs /dev/usbmon
    and hidraw access), and ``sudo`` strips the invoking user's
    environment. That leaves the inhibitor subprocesses with an empty
    env, so ``gnome-session-inhibit`` — the only command GNOME's screen
    lock actually honors — fails with "Failed to connect to session bus".

    We recover the needed keys from a running user session process by
    scanning ``/proc`` for processes that have a session bus address.
    The first one with a usable ``DBUS_SESSION_BUS_ADDRESS`` wins.

    The result is MERGED on top of ``os.environ`` (not a replacement),
    so the subprocess still has PATH and everything else it needs to
    resolve ``sleep`` and the other binaries. Returns None when no
    session env can be recovered (e.g. running as the user directly,
    or no graphical session), in which case the inhibitor uses the
    caller's own environment unchanged.
    """
    keys = set(SESSION_ENV_KEYS)

    for pid_dir in sorted(PROC_ENV.iterdir()):
        if not pid_dir.name.isdigit():
            continue

        env = _read_proc_env(int(pid_dir.name))
        if "DBUS_SESSION_BUS_ADDRESS" not in env:
            continue

        recovered = {key: env[key] for key in keys if key in env}
        # A session bus address alone is enough to connect; XDG_RUNTIME_DIR
        # is needed too for the autolaunch fallback, so require both.
        if "XDG_RUNTIME_DIR" in recovered:
            logger.debug(
                "Recovered session env from PID %s (%s)",
                pid_dir.name,
                recovered.get("DBUS_SESSION_BUS_ADDRESS"),
            )
            return recovered

    return None


# Cache of the recovered session env. Recovering it scans /proc, which
# is wasteful to repeat on every activity packet, and the session env
# does not change for the lifetime of the session.
_session_env_cache: Optional[Optional[dict]] = None


def _session_env() -> Optional[dict]:
    """Return the session env to pass to inhibitor subprocesses.

    Lazily recovers (and caches) the user session env, merging it on top
    of ``os.environ`` so PATH and other essentials are preserved. When
    the bridge is running as the user directly (no sudo), the cache is
    still valid — the recovered session env is a superset of what the
    process already has.
    """
    global _session_env_cache
    if _session_env_cache is not None:
        return _session_env_cache

    recovered = _recover_session_env()
    if recovered is None:
        _session_env_cache = None
        return None

    merged = dict(os.environ)
    merged.update(recovered)
    _session_env_cache = merged
    return merged


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

    def __init__(self, cmds=None, env=None) -> None:
        if cmds is None:
            cmds = list(INHIBIT_CMDS)
        elif isinstance(cmds, str):
            cmds = [cmds]
        else:
            cmds = [list(c) for c in cmds]
        self._cmds = cmds
        # Environment to pass to inhibitor subprocesses. When the bridge
        # is launched via ``sudo`` (the usual case) it inherits an empty
        # environment, so ``gnome-session-inhibit`` — the only command
        # GNOME's screen lock actually honors — fails to reach the
        # session bus. Recovering the user session env and passing it
        # through is what makes the GNOME path work.
        self._env = env
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

    @property
    def env(self) -> Optional[dict]:
        """Environment passed to inhibitor subprocesses (may be None)."""
        return self._env

    @env.setter
    def env(self, value: Optional[dict]) -> None:
        """Set the environment passed to inhibitor subprocesses."""
        self._env = value

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
                env=self._env,
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

    The user session environment is recovered from a running session
    process and passed through to the inhibitor subprocesses. This is
    what lets ``gnome-session-inhibit`` reach the session bus when the
    bridge is launched via ``sudo`` (which strips the invoking user's
    environment); without it the GNOME path silently fails and the
    screen lock is never actually inhibited.
    """
    if cmd is not None:
        global _inhibitor
        _inhibitor = GamepadIdleInhibitor(cmd, env=_session_env())
    else:
        _inhibitor.env = _session_env()
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
    active and restarts the idle inhibitor.

    The inhibitor is restarted on every real event (``start()`` is
    idempotent), not only on the idle->active transition. This is
    critical: this thread races with the usbmon loop in
    ``monitor.py`` to be the first to observe activity. If this thread
    wins the race it clears the idle flag, so the main loop's later
    ``mark_activity()`` returns ``False`` and would never restart the
    inhibitor — leaving the screen off even though the controller is
    in use.
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
                if idle.mark_activity():
                    logger.debug("DualSense active again after idle.")
                # start() is idempotent: restart whenever we see real
                # activity so the inhibitor is revived even if this
                # thread cleared the idle flag before the main loop ran.
                start_idle_inhibitor()
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


class EventReader:
    """Handle for a running DualSense input event reader thread.

    Pairs the worker thread with its stop signal so callers do not have
    to stash attributes on a ``threading.Thread`` (which does not support
    arbitrary attributes) or maintain a parallel module-level registry.
    """

    def __init__(self, thread: threading.Thread, stop_event: threading.Event) -> None:
        self._thread = thread
        self._stop_event = stop_event

    @property
    def is_alive(self) -> bool:
        """True while the reader thread is still running."""
        return self._thread.is_alive()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the reader to stop and wait for the thread to exit."""
        self._stop_event.set()
        self._thread.join(timeout=timeout)


def start_event_reader(
    device_path: str,
    idle: IdleTracker,
    on_error: Optional[Callable[[str], None]] = None,
) -> Optional[EventReader]:
    """Open the DualSense input device and drain events in a daemon thread.

    The thread feeds real input events into ``idle`` so the idle
    inhibitor can be toggled. Returns an :class:`EventReader` handle
    (already started), or None if the device could not be opened.
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
    thread.start()
    logger.info("Started evdev event reader for %s.", device_path)
    return EventReader(thread, stop_event)


def _run_event_reader(device, idle: IdleTracker, stop_event) -> None:
    """Thread body that wraps ``_drain_events`` with cleanup."""
    try:
        _drain_events(device, idle, stop_event)
    finally:
        try:
            device.close()
        except Exception:
            pass


def stop_event_reader(reader: Optional[EventReader]) -> None:
    """Signal an event reader to stop and wait for its thread to exit."""
    if reader is None:
        return
    reader.stop()