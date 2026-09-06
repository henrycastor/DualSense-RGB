#!/usr/bin/env python3

"""Tests for dualsense_rgb.bridge.wljoywake module."""

import os
import signal
from unittest.mock import MagicMock, patch

import pytest

from dualsense_rgb.bridge import wljoywake
from dualsense_rgb.bridge.wljoywake import (
    is_wljoywake_running,
    start_wljoywake,
    stop_wljoywake,
)


@pytest.fixture(autouse=True)
def reset_wljoywake_state() -> None:
    """Reset the module-level process state between tests."""
    wljoywake._wljoywake_process = None
    yield
    wljoywake._wljoywake_process = None


class TestStartWljoywake:
    def test_starts_process(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            start_wljoywake()
            mock_popen.assert_called_once()
            assert mock_popen.call_args[0][0] == ["wljoywake"]

    def test_noop_when_already_running(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            start_wljoywake()
            start_wljoywake()
            mock_popen.assert_called_once()

    def test_restarts_after_process_exited(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            start_wljoywake()
            # Simulate process exiting.
            mock_process.poll.return_value = 0
            start_wljoywake()
            assert mock_popen.call_count == 2

    def test_handles_missing_executable(self) -> None:
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            start_wljoywake()

    def test_handles_generic_exception(self) -> None:
        with patch("subprocess.Popen", side_effect=OSError("boom")):
            start_wljoywake()

    def test_custom_command(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            start_wljoywake(cmd=["wljoywake", "--flag"])
            mock_popen.assert_called_once()
            assert mock_popen.call_args[0][0] == ["wljoywake", "--flag"]


class TestStopWljoywake:
    def test_noop_when_not_running(self) -> None:
        with patch("os.killpg") as mock_killpg:
            stop_wljoywake()
            mock_killpg.assert_not_called()

    def test_terminates_running_process(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 1234
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg"
        ) as mock_killpg, patch("os.getpgid", return_value=1234):
            start_wljoywake()
            stop_wljoywake()
            mock_killpg.assert_called_once_with(1234, signal.SIGTERM)

    def test_handles_process_lookup_error(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 1234
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg", side_effect=ProcessLookupError
        ), patch("os.getpgid", return_value=1234):
            start_wljoywake()
            stop_wljoywake()  # Should not raise.

    def test_handles_generic_exception(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 1234
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg", side_effect=OSError("boom")
        ), patch("os.getpgid", return_value=1234):
            start_wljoywake()
            stop_wljoywake()  # Should not raise.


class TestIsWljoywakeRunning:
    def test_false_when_never_started(self) -> None:
        assert is_wljoywake_running() is False

    def test_true_when_running(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process):
            start_wljoywake()
            assert is_wljoywake_running() is True

    def test_false_after_stop(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        with patch("subprocess.Popen", return_value=mock_process), patch(
            "os.killpg"
        ), patch("os.getpgid", return_value=1234):
            start_wljoywake()
            stop_wljoywake()
            assert is_wljoywake_running() is False

    def test_false_when_process_exited(self) -> None:
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        with patch("subprocess.Popen", return_value=mock_process):
            start_wljoywake()
            assert is_wljoywake_running() is False
