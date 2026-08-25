#!/usr/bin/env python3

"""Command-line interface for DualSense RGB bridge."""

import argparse
import logging
import sys
from typing import List, Optional

from dualsense_rgb.monitor import main


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="dualsense-rgb",
        description="Bridge DualSense lightbar colors to OpenRGB via usbmon",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0",
    )
    return parser


def configure_logging(verbose: bool = False) -> None:
    """Configure logging based on CLI arguments."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )


def main_cli(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(args.verbose)

    main()

    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
