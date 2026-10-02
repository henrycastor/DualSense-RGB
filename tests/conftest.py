#!/usr/bin/env python3

"""Shared pytest fixtures for dualsense_rgb tests."""

import logging

import pytest


@pytest.fixture(autouse=True)
def disable_logging() -> None:
    """Disable logging output during tests."""
    logging.disable(logging.CRITICAL)
