#!/usr/bin/env python3

"""Tests for dualsense_rgb.cli module."""

import sys
from unittest.mock import patch

import pytest

from dualsense_rgb.cli import build_parser, main_cli


class TestBuildParser:
    def test_parser_creation(self) -> None:
        parser = build_parser()
        assert parser is not None

    def test_verbose_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--verbose"])
        assert args.verbose is True

    def test_no_args(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.verbose is False


class TestMainCli:
    @patch("dualsense_rgb.cli.main")
    def test_main_cli_success(self, mock_main) -> None:
        result = main_cli([])
        assert result == 0
        mock_main.assert_called_once()

    @patch("dualsense_rgb.cli.main")
    def test_main_cli_verbose(self, mock_main) -> None:
        result = main_cli(["--verbose"])
        assert result == 0
        mock_main.assert_called_once()
