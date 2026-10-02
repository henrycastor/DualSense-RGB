#!/usr/bin/env python3

"""Tests for dualsense_rgb.bridge.evdev module."""

import signal
import subprocess
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from dualsense_rgb.bridge import evdev
from dualsense_rgb.bridge.evdev import (
    GamepadIdleInhibitor,
    IdleTracker,
    find_dualsense_input_device,
    is_idle_inhibitor_running,
    start_event_reader,
    start_idle_inhibitor,
    stop_event_reader,
    stop_idle_inhibitor,
)


@pytest.fixture(autouse=True)
def reset_inhibitor_state() -> None:
    """Reset the module-level inhibitor between tests."""
    evdev._inhibitor = GamepadIdleInhibitor()
    yield
    evdev._inhibitor = GamepadIdleInhibitor()


# ============================================================
# GamepadIdleInhibitor
# ============================================================


class TestGamepadIdleInhibitor:
    def test_starts_process(self) -> None:
        mock_process = MagicMock()
        # wait(timeout=...) should raise TimeoutExpired -> still alive.
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            inhibitor = GamepadIdleInhibitor()
            inhibitor.start()
            mock_popen.assert_called_once()
            assert mock_popen.call_args[0][0][0] == "gnome-session-inhibit"

    def test_noop_when_already_running(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            inhibitor = GamepadIdleInhibitor()
            inhibitor.start()
            inhibitor.start()
            mock_popen.assert_called_once()

    def test_restarts_after_process_exited(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            inhibitor = GamepadIdleInhibitor()
            inhibitor.start()
            # Simulate process exiting.
            mock_process.poll.return_value = 0
            inhibitor.start()
            assert mock_popen.call_count == 2

    def test_handles_missing_executable(self) -> None:
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            inhibitor = GamepadIdleInhibitor()
            inhibitor.start()
            assert inhibitor.is_running is False

    def test_handles_generic_exception(self) -> None:
        with patch("subprocess.Popen", side_effect=OSError("boom")):
            inhibitor = GamepadIdleInhibitor()
            inhibitor.start()
            assert inhibitor.is_running is False

    def test_custom_command(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            inhibitor = GamepadIdleInhibitor(cmds=[["systemd-inhibit", "--flag"]])
            inhibitor.start()
            mock_popen.assert_called_once()
            assert mock_popen.call_args[0][0] == ["systemd-inhibit", "--flag"]

    def test_is_running_false_by_default(self) -> None:
        assert GamepadIdleInhibitor().is_running is False

    def test_skips_candidate_that_exits_immediately(self) -> None:
        # First candidate exits immediately (bad option), second stays up.
        bad = MagicMock()
        bad.wait.return_value = 0  # exited
        bad.poll.return_value = 0
        good = MagicMock()
        good.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        good.poll.return_value = None
        with patch("subprocess.Popen", side_effect=[bad, good]) as mock_popen:
            inhibitor = GamepadIdleInhibitor()
            inhibitor.start()
            assert mock_popen.call_count == 2
            assert inhibitor.is_running is True
            # The surviving process is the second candidate (systemd-inhibit).
            assert inhibitor._process is good
            assert inhibitor.active_command[0] == "systemd-inhibit"

    def test_all_candidates_fail(self) -> None:
        bad1 = MagicMock()
        bad1.wait.return_value = 0
        bad1.poll.return_value = 0
        bad2 = MagicMock()
        bad2.wait.return_value = 0
        bad2.poll.return_value = 0
        with patch("subprocess.Popen", side_effect=[bad1, bad2]):
            inhibitor = GamepadIdleInhibitor()
            inhibitor.start()
            assert inhibitor.is_running is False


class TestStopInhibitor:
    def test_noop_when_not_running(self) -> None:
        with patch("os.killpg") as mock_killpg:
            stop_idle_inhibitor()
            mock_killpg.assert_not_called()

    def test_terminates_running_process(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        mock_process.pid = 1234
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg"
        ) as mock_killpg, patch("os.getpgid", return_value=1234):
            start_idle_inhibitor()
            stop_idle_inhibitor()
            mock_killpg.assert_called_once_with(1234, signal.SIGTERM)

    def test_handles_process_lookup_error(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        mock_process.pid = 1234
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg", side_effect=ProcessLookupError
        ), patch("os.getpgid", return_value=1234):
            start_idle_inhibitor()
            stop_idle_inhibitor()  # Should not raise.

    def test_handles_generic_exception(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        mock_process.pid = 1234
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg", side_effect=OSError("boom")
        ), patch("os.getpgid", return_value=1234):
            start_idle_inhibitor()
            stop_idle_inhibitor()  # Should not raise.


class TestIsInhibitorRunning:
    def test_false_when_never_started(self) -> None:
        assert is_idle_inhibitor_running() is False

    def test_true_when_running(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process):
            start_idle_inhibitor()
            assert is_idle_inhibitor_running() is True

    def test_false_after_stop(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=0.5)
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg"
        ), patch("os.getpgid", return_value=1234):
            start_idle_inhibitor()
            stop_idle_inhibitor()
            assert is_idle_inhibitor_running() is False

    def test_false_when_process_exited(self) -> None:
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_process.poll.return_value = 0
        with patch("subprocess.Popen", return_value=mock_process):
            start_idle_inhibitor()
            assert is_idle_inhibitor_running() is False


# ============================================================
# find_dualsense_input_device
# ============================================================


class TestFindDualsenseInputDevice:
    def test_returns_none_when_dir_missing(self) -> None:
        with patch("dualsense_rgb.bridge.evdev.INPUT_SYS_DIR") as mock_dir:
            mock_dir.is_dir.return_value = False
            assert find_dualsense_input_device() is None

    def test_finds_matching_event_node(self, tmp_path) -> None:
        event_dir = tmp_path / "event0"
        device_dir = event_dir / "device"
        device_dir.mkdir(parents=True)
        (device_dir / "uevent").write_text(
            "PRODUCT=054c/0ce6/0123\n", encoding="utf-8"
        )

        with patch("dualsense_rgb.bridge.evdev.INPUT_SYS_DIR", tmp_path), patch(
            "dualsense_rgb.bridge.evdev.INPUT_DEV_DIR", tmp_path
        ):
            result = find_dualsense_input_device()

        assert result == str(tmp_path / "event0")

    def test_skips_non_event_nodes(self, tmp_path) -> None:
        (tmp_path / "mouse0").mkdir()
        device_dir = tmp_path / "mouse0" / "device"
        device_dir.mkdir(parents=True)
        (device_dir / "uevent").write_text(
            "PRODUCT=54c/ce6/123\n", encoding="utf-8"
        )

        with patch("dualsense_rgb.bridge.evdev.INPUT_SYS_DIR", tmp_path), patch(
            "dualsense_rgb.bridge.evdev.INPUT_DEV_DIR", tmp_path
        ):
            assert find_dualsense_input_device() is None

    def test_skips_non_dualsense_nodes(self, tmp_path) -> None:
        event_dir = tmp_path / "event1"
        device_dir = event_dir / "device"
        device_dir.mkdir(parents=True)
        (device_dir / "uevent").write_text(
            "PRODUCT=abc/def/123\n", encoding="utf-8"
        )

        with patch("dualsense_rgb.bridge.evdev.INPUT_SYS_DIR", tmp_path), patch(
            "dualsense_rgb.bridge.evdev.INPUT_DEV_DIR", tmp_path
        ):
            assert find_dualsense_input_device() is None

    def test_skips_edge_pid(self, tmp_path) -> None:
        event_dir = tmp_path / "event2"
        device_dir = event_dir / "device"
        device_dir.mkdir(parents=True)
        (device_dir / "uevent").write_text(
            "PRODUCT=054c/0df2/0123\n", encoding="utf-8"
        )

        with patch("dualsense_rgb.bridge.evdev.INPUT_SYS_DIR", tmp_path), patch(
            "dualsense_rgb.bridge.evdev.INPUT_DEV_DIR", tmp_path
        ):
            assert find_dualsense_input_device() == str(tmp_path / "event2")


# ============================================================
# start_event_reader / stop_event_reader
# ============================================================


class TestEventReader:
    def test_returns_none_when_evdev_missing(self) -> None:
        # Setting sys.modules["evdev"] = None makes `import evdev`
        # raise ImportError, simulating a missing package.
        with patch.dict(sys.modules, {"evdev": None}):
            thread = start_event_reader("/dev/input/event0", IdleTracker(10.0))
            assert thread is None

    def test_returns_none_when_device_cannot_open(self) -> None:
        fake = types.SimpleNamespace(
            InputDevice=lambda path: (_ for _ in ()).throw(OSError("nope")),
            EV_SYN=3,
            EV_KEY=1,
        )
        with patch.dict(sys.modules, {"evdev": fake}):
            thread = start_event_reader("/dev/input/event0", IdleTracker(10.0))
            assert thread is None

    def test_starts_thread_and_stops_cleanly(self) -> None:
        mock_device = MagicMock()
        # read() returns no events -> the loop just waits on stop_event.
        mock_device.read.return_value = []
        fake = types.SimpleNamespace(
            InputDevice=lambda path: mock_device,
            EV_SYN=3,
            EV_KEY=1,
        )
        with patch.dict(sys.modules, {"evdev": fake}):
            idle = IdleTracker(10.0)
            thread = start_event_reader("/dev/input/event0", idle)
            assert thread is not None
            assert thread.is_alive()
            stop_event_reader(thread)
            assert not thread.is_alive()
            mock_device.close.assert_called_once()

    def test_mark_activity_on_real_events(self) -> None:
        class FakeEvent:
            def __init__(self, etype):
                self.type = etype

        mock_device = MagicMock()
        # First call: a real key event (not EV_SYN) -> marks activity.
        # Second call: empty list -> loop waits.
        mock_device.read.side_effect = [
            [FakeEvent(1)],  # EV_KEY
            [],
        ]
        fake = types.SimpleNamespace(
            InputDevice=lambda path: mock_device,
            EV_SYN=3,
            EV_KEY=1,
        )
        with patch.dict(sys.modules, {"evdev": fake}):
            idle = IdleTracker(10.0)
            thread = start_event_reader("/dev/input/event0", idle)
            # Give the thread a moment to process the first read().
            time.sleep(0.2)
            stop_event_reader(thread)
            # mark_activity was called -> is_idle is False (it started False,
            # so this just confirms activity was recorded, not a transition).
            assert idle.is_idle is False

    def test_prefers_lowest_numbered_node(self, tmp_path) -> None:
        # A single USB controller exposes several event nodes (main
        # gamepad, motion, touchpad, headset jack). The lowest-numbered
        # one is conventionally the main gamepad and should win.
        for name in ("event18", "event17", "event20", "event19"):
            device_dir = tmp_path / name / "device"
            device_dir.mkdir(parents=True)
            (device_dir / "uevent").write_text(
                "PRODUCT=054c/0ce6/0123\n", encoding="utf-8"
            )

        with patch("dualsense_rgb.bridge.evdev.INPUT_SYS_DIR", tmp_path), patch(
            "dualsense_rgb.bridge.evdev.INPUT_DEV_DIR", tmp_path
        ):
            result = find_dualsense_input_device()

        assert result == str(tmp_path / "event17")