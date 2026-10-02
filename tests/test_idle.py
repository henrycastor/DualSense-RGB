#!/usr/bin/env python3

"""Tests for dualsense_rgb.bridge.evdev.IdleTracker."""

from dualsense_rgb.bridge.evdev import IdleTracker


class TestIdleTracker:
    def test_starts_active(self) -> None:
        clock = [0.0]
        tracker = IdleTracker(10.0, clock=lambda: clock[0])
        assert tracker.is_idle is False

    def test_no_idle_before_timeout(self) -> None:
        clock = [0.0]
        tracker = IdleTracker(10.0, clock=lambda: clock[0])
        clock[0] = 5.0
        assert tracker.update() is False
        assert tracker.is_idle is False

    def test_becomes_idle_after_timeout(self) -> None:
        clock = [0.0]
        tracker = IdleTracker(10.0, clock=lambda: clock[0])
        clock[0] = 11.0
        assert tracker.update() is True
        assert tracker.is_idle is True

    def test_update_when_already_idle_returns_false(self) -> None:
        clock = [0.0]
        tracker = IdleTracker(10.0, clock=lambda: clock[0])
        clock[0] = 11.0
        assert tracker.update() is True
        assert tracker.update() is False

    def test_mark_activity_when_active_returns_false(self) -> None:
        clock = [0.0]
        tracker = IdleTracker(10.0, clock=lambda: clock[0])
        clock[0] = 5.0
        assert tracker.mark_activity() is False
        assert tracker.is_idle is False

    def test_mark_activity_clears_idle(self) -> None:
        clock = [0.0]
        tracker = IdleTracker(10.0, clock=lambda: clock[0])
        clock[0] = 11.0
        assert tracker.update() is True  # becomes idle
        clock[0] = 12.0
        assert tracker.mark_activity() is True  # was idle -> active
        assert tracker.is_idle is False

    def test_activity_resets_idle_timer(self) -> None:
        clock = [0.0]
        tracker = IdleTracker(10.0, clock=lambda: clock[0])
        clock[0] = 9.0
        tracker.mark_activity()  # resets last_activity to 9.0
        clock[0] = 18.0
        # 18 - 9 = 9 < 10, so still active
        assert tracker.update() is False
        assert tracker.is_idle is False
        clock[0] = 20.0
        # 20 - 9 = 11 >= 10, now idle
        assert tracker.update() is True
        assert tracker.is_idle is True