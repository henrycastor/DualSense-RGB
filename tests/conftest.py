#!/usr/bin/env python3

"""Shared pytest fixtures for dualsense_rgb tests."""

import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def disable_logging() -> None:
    """Disable logging output during tests."""
    logging.disable(logging.CRITICAL)
