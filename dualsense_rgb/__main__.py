#!/usr/bin/env python3

"""Allow running the package as a module: python -m dualsense_rgb"""

import subprocess
import sys
from pathlib import Path

# Ensure the project root is on sys.path so this file can be run
# directly (e.g. python3 dualsense_rgb/__main__.py) as well as via
# python -m dualsense_rgb.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _ensure_dependencies() -> None:
    """Install missing runtime dependencies before importing the package.

    The only third-party dependency is ``openrgb-python`` (imported as
    ``openrgb``).  If it is not available, attempt to install it via pip
    so the user can run ``python -m dualsense_rgb`` without manually
    setting up a virtual environment first.
    """
    try:
        import openrgb  # noqa: F401
    except ImportError:
        print(
            "openrgb-python is not installed. "
            "Attempting automatic installation...",
            file=sys.stderr,
        )
        install_cmd = [
            sys.executable, "-m", "pip", "install", "openrgb-python>=0.3.6"
        ]
        try:
            subprocess.check_call(install_cmd)
        except subprocess.CalledProcessError:
            # PEP 668: externally-managed environments (e.g. Debian/Ubuntu
            # system Python) reject system-wide pip installs.  Retry with
            # --break-system-packages so the user doesn't have to set up a
            # virtual environment manually.
            print(
                "Standard pip install failed (externally-managed "
                "environment). Retrying with --break-system-packages...",
                file=sys.stderr,
            )
            subprocess.check_call(
                install_cmd + ["--break-system-packages"]
            )


_ensure_dependencies()

from dualsense_rgb.cli import main_cli

if __name__ == "__main__":
    sys.exit(main_cli())
