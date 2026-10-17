"""Backups of a board's whole flash, so its original firmware can go back on.

Each backup is a raw image of the flash from 0x0 (`<chip>-<mac>-<time>.bin`)
with a `.json` beside it saying which chip and board it came from. A .bin
without one (made by another tool) can still be restored; its chip is then
whatever the connected board turns out to be.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import manifest


@dataclass
class Backup:
    path: Path
    chip: str = ""           # ESP Web Tools name, e.g. "ESP32-S3"
    flash_bytes: int = 0
    mac: str = ""
    created: str = ""        # ISO time
    board: str = ""          # catalog board id, if one was picked
    firmware: str = ""       # what was on the board, e.g. "handheld e82e8eb (IDF v5.2.3)"

    @property
    def size(self) -> int:
        return self.path.stat().st_size if self.path.is_file() else 0

    @property
    def complete(self) -> bool:
        """A copy of the whole flash, not a piece of it."""
        return bool(self.flash_bytes) and self.size == self.flash_bytes


def file_name(chip: str, mac: str, when: datetime) -> str:
    device = mac.replace(":", "").lower() or "unknown"
    return f"{chip.lower() or 'esp'}-{device}-{when:%Y%m%d-%H%M%S}.bin"


def _meta_path(path: Path) -> Path:
    return path.with_suffix(".json")


def write_meta(backup: Backup) -> None:
    data = asdict(backup)
    data.pop("path")
    _meta_path(backup.path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_meta(path: Path) -> Backup:
    backup = Backup(Path(path))
    try:
        data = json.loads(_meta_path(backup.path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        backup.firmware = _describe(backup.path)
        return backup
    if isinstance(data, dict):
        for key in ("chip", "mac", "created", "board", "firmware"):
            if isinstance(data.get(key), str):
                setattr(backup, key, data[key])
        if isinstance(data.get("flash_bytes"), int):
            backup.flash_bytes = data["flash_bytes"]
    return backup


def finish(path: Path, chip: str, flash_bytes: int, mac: str, board: str = "") -> Backup:
    """Writes the .json for a fresh image, noting what firmware was in it."""
    backup = Backup(path, chip, flash_bytes, mac, datetime.now().isoformat(timespec="seconds"),
                    board, _describe(path))
    write_meta(backup)
    return backup


def _describe(path: Path) -> str:
    """The app in an image, e.g. "handheld e82e8eb (IDF v5.2.3)", or ''."""
    try:
        with open(path, "rb") as image:
            # The table, and the header of an app placed in the first 4 MB.
            app = manifest.describe_image(image.read(0x400000 + manifest.APP_HEADER_SIZE))
    except OSError:
        return ""
    return app.summary() if app else ""


def list_backups(folder: Path) -> list[Backup]:
    """Backups in the folder, newest first."""
    if not folder.is_dir():
        return []
    found = [read_meta(p) for p in folder.glob("*.bin") if p.is_file()]
    return sorted(found, key=lambda b: (b.created, b.path.name), reverse=True)


def find_for_mac(folder: Path, mac: str) -> Backup | None:
    """The newest complete backup of this board, if there is one."""
    mac = mac.lower()
    for backup in list_backups(folder):
        if mac and backup.mac.lower() == mac and backup.complete:
            return backup
    return None
