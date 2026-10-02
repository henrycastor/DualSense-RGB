# DualSense RGB

Bridge DualSense lightbar colors to OpenRGB via usbmon, with libevdev
idle inhibition.

## Overview

DualSense RGB monitors the USB HID output reports sent to a DualSense
controller, extracts the lightbar RGB color, and forwards it to OpenRGB.
While the controller is in use it also keeps the session awake so the
screen does not lock or sleep.

Idle inhibition is handled in-process with **libevdev** (via the Python
[`pyevdev`](https://github.com/gvalkov/python-evdev) package) for event
reading and **`systemd-inhibit`** for idle inhibition.

## How it works

1. **Discovery** — the DualSense hidraw node is found in
   `/sys/class/hidraw`, and the USB bus/device address is resolved from
   the sysfs hierarchy.
2. **Capture** — the binary usbmon interface (`/dev/usbmonN`) is opened
   and interrupt OUT transfers on endpoint 3 are decoded. The full
   transfer data is captured (the text API truncates before the RGB
   bytes at the end of the report).
3. **RGB extraction** — the lightbar color is parsed from the DualSense
   output report (report ID `0x02`, bytes 45/46/47 = R/G/B).
4. **OpenRGB** — the color is applied to all LEDs on all OpenRGB devices.
5. **Idle inhibition** — the DualSense's `/dev/input/eventX` node is
   opened read-only via libevdev. Any real input event marks the
   controller as active; after `idle_timeout` seconds of silence the
   controller is considered idle and the session is allowed to sleep
   again. The inhibitor is started via `gnome-session-inhibit --inhibit
   idle` (the tool GNOME's screen lock actually honors) with a portable
   `systemd-inhibit --what=idle:sleep --mode=block` fallback. The first
   candidate that stays alive wins.

## Requirements

- Linux with `usbmon` available (`sudo modprobe usbmon`).
- Root privileges (required for `/dev/usbmon` and hidraw access).
- `systemd` (for `systemd-inhibit`).
- The `libevdev` system library (pulled in by `pyevdev`).
- OpenRGB with the SDK server enabled.

### Install dependencies

```bash
# System packages (Debian/Ubuntu)
sudo apt install libevdev-dev systemd gnome-session

# Arch
sudo pacman -S libevdev systemd gnome-session

# Python packages
pip install dualsense-rgb
# or
pip install openrgb-python pyevdev
```

`pyevdev` requires the `libevdev` development headers to build. If your
distribution ships a prebuilt wheel it can be installed without the
headers.

## Running

```bash
sudo dualsense-rgb
```

Optional flags:

```bash
dualsense-rgb --help
dualsense-rgb --verbose
dualsense-rgb --idle-timeout 30
```

### Enable the OpenRGB SDK server

OpenRGB → Settings → SDK Server → Enable

## Architecture

```
dualsense_rgb/
├── __init__.py        # package metadata
├── __main__.py        # `python -m dualsense_rgb` entry + dep bootstrap
├── cli.py             # argparse CLI
├── constants.py       # shared constants (usbmon layout, ids, inhibit flags)
├── monitor.py         # usbmon capture loop + OpenRGB forwarding
├── bridge/
│   ├── __init__.py
│   ├── openrgb.py     # OpenRGB client connection + color update
│   └── evdev.py       # libevdev event reader + IdleTracker + systemd-inhibit
└── device/
    ├── __init__.py
    ├── dualsense.py   # hidraw discovery, USB topology, RGB extraction
    └── usbmon.py      # low-level usbmon binary API
```

### Idle inhibition

`dualsense_rgb/bridge/evdev.py` exposes:

- `IdleTracker` — activity-timeout state machine driven by real input
  events (or, as a fallback, by usbmon traffic).
- `find_dualsense_input_device` — resolves the DualSense
  `/dev/input/eventX` node from sysfs.
- `GamepadIdleInhibitor` — manages a `systemd-inhibit` subprocess.
- `start_event_reader` / `stop_event_reader` — daemon thread that drains
  libevdev events and feeds `IdleTracker`.

## Testing

```bash
pip install -e .[dev]   # or: pip install pytest
pytest