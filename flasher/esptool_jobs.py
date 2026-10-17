"""esptool command lines, and reading what esptool prints."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

BAUD_RATES = (921600, 460800, 230400, 115200)
DEFAULT_BAUD = 460800

_CHIP = re.compile(r"^Chip type:\s+(ESP[0-9A-Z-]+)", re.M)
_CHIP_OLD = re.compile(r"^Chip is (ESP[0-9A-Z-]+)", re.M)
_DETECTED = re.compile(r"Detecting chip type\.\.\.\s*(ESP[0-9A-Z-]+)")
_FLASH = re.compile(r"Detected flash size:\s*(\d+)\s*([KM])B")
_MAC = re.compile(r"^MAC:\s+([0-9a-f:]{17})", re.M | re.I)
_FEATURES = re.compile(r"^Features:\s+(.+)$", re.M)
_PSRAM = re.compile(r"Embedded PSRAM (\d+)\s*MB", re.I)
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
_WRITING_AT = re.compile(r"Writing at (0x[0-9a-f]+)", re.I)
_READING = re.compile(r"Reading from 0x[0-9a-f]+", re.I)


@dataclass
class ChipInfo:
    family: str = ""         # ESP Web Tools name, e.g. "ESP32-S3"
    flash_bytes: int = 0
    mac: str = ""
    features: str = ""
    firmware: object = None  # manifest.AppInfo of what's on it now, if read

    @property
    def psram(self) -> str:
        """PSRAM inside the chip package: "octal" (ESP32-S3R8/R16V), "quad",
        or "" when there's none or it sits outside the chip, which esptool can't see."""
        match = _PSRAM.search(self.features)
        if not match:
            return ""
        return "octal" if self.family == "ESP32-S3" and int(match.group(1)) >= 8 else "quad"


def esptool_command(args: list[str]) -> list[str]:
    """How to start esptool: ourselves in a frozen build, `python -m esptool` otherwise."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-esptool", *args]
    return [sys.executable, "-m", "esptool", *args]


def detect_args(port: str, baud: int = 115200, after: str = "hard-reset") -> list[str]:
    return ["--port", port, "--baud", str(baud), "--after", after, "flash-id"]


def read_args(port: str, baud: int, chip: str, offset: int, size: int, out: str,
              after: str = "hard-reset") -> list[str]:
    args = ["--chip", chip] if chip else []
    return args + ["--port", port, "--baud", str(baud), "--after", after,
                   "read-flash", hex(offset), hex(size), out]


def reset_args(port: str, chip: str = "") -> list[str]:
    """A harmless command whose only effect is restarting the board into its app."""
    args = ["--chip", chip] if chip else []
    return args + ["--port", port, "--after", "hard-reset", "read-mac"]


def flash_args(port: str, baud: int, chip: str, parts: list[tuple[int, str]], erase_all: bool,
               detect_flash_size: bool = False) -> list[str]:
    args = ["--chip", chip, "--port", port, "--baud", str(baud),
            "--before", "default-reset", "--after", "hard-reset", "write-flash"]
    if erase_all:
        args.append("--erase-all")
    if detect_flash_size:
        args += ["--flash-size", "detect"]
    for offset, path in sorted(parts):
        args += [hex(offset), path]
    return args


def parse_chip(text: str) -> ChipInfo:
    info = ChipInfo()
    match = _CHIP.search(text) or _CHIP_OLD.search(text) or _DETECTED.search(text)
    if match:
        family = match.group(1).rstrip("-")
        info.family = family
    match = _FLASH.search(text)
    if match:
        info.flash_bytes = int(match.group(1)) * (1024 if match.group(2) == "K" else 1024 * 1024)
    match = _MAC.search(text)
    if match:
        info.mac = match.group(1).lower()
    match = _FEATURES.search(text)
    if match:
        info.features = match.group(1).strip()
    return info


class FlashProgress:
    """Overall progress across the parts, weighted by size, from esptool's
    "Writing at 0xADDR [===>  ] 45.2%" lines: the address says which part."""

    def __init__(self, sizes: dict[int, int]):
        self._sizes = dict(sorted(sizes.items()))
        self._total = sum(sizes.values()) or 1
        self._best = 0.0

    def feed(self, line: str) -> float | None:
        """A fraction 0..1 if the line moved the bar, else None."""
        match = _WRITING_AT.search(line)
        pct = _PERCENT.search(line)
        if not match or not pct:
            return None
        address = int(match.group(1), 16)
        starts = [o for o in self._sizes if o <= address]
        if not starts:
            return None
        current = max(starts)
        before = sum(size for offset, size in self._sizes.items() if offset < current)
        value = (before + min(float(pct.group(1)), 100.0) / 100.0 * self._sizes[current]) / self._total
        # Never step back (a part's 100% line carries the next part's address).
        self._best = max(self._best, value)
        return self._best


def read_progress(line: str) -> float | None:
    """A fraction 0..1 from esptool's "Reading from 0xADDR [==>  ] 45.2%" lines."""
    pct = _PERCENT.search(line)
    if not _READING.search(line) or not pct:
        return None
    return min(float(pct.group(1)), 100.0) / 100.0


# Lines that mean the board never entered its bootloader: tell the user how.
BOOTLOADER_HINTS = ("Failed to connect", "No serial data received", "Wrong boot mode",
                    "Could not connect to an Espressif device")
PORT_BUSY_HINTS = ("PermissionError", "Access is denied", "could not open port", "Resource busy")
