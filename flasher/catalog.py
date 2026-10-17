"""The firmware catalog: open-source projects, and the boards each one builds.

data/catalog.json lists sources. A source either names its boards inline
("boards") or points at a boards.json in the Muse web flasher's format
("boards_url": [{board, name, usb?, note?}]), with "manifest_url" a template
holding {board}.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import USER_AGENT


def resource_dir() -> Path:
    """Bundled data: next to the package, or PyInstaller's unpack folder."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


@dataclass
class Board:
    id: str
    name: str
    manifest_url: str
    usb: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Source:
    id: str
    name: dict
    description: dict
    homepage: str
    setup: str = ""
    boards_url: str = ""
    manifest_url: str = ""
    boards: list[Board] = field(default_factory=list)

    def text(self, attr: str, lang: str) -> str:
        value = getattr(self, attr)
        return value.get(lang) or value.get("en") or next(iter(value.values()), "")


def _plain(note: str) -> str:
    """Board notes are HTML on the web flasher; show them as text."""
    return html.unescape(re.sub(r"<[^>]+>", "", note or "")).strip()


def load_catalog(path: Path | None = None) -> list[Source]:
    path = path or resource_dir() / "data" / "catalog.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = []
    for raw in data.get("sources", []):
        source = Source(
            id=raw["id"], name=raw["name"], description=raw.get("description", {}),
            homepage=raw.get("homepage", ""), setup=raw.get("setup", ""),
            boards_url=raw.get("boards_url", ""), manifest_url=raw.get("manifest_url", ""),
        )
        source.boards = [parse_board(b, source.manifest_url) for b in raw.get("boards", [])]
        sources.append(source)
    return sources


def parse_board(raw: dict, template: str) -> Board:
    board_id = str(raw["board"])
    url = raw.get("manifest_url") or template.replace("{board}", board_id)
    usb = [u for u in raw.get("usb", []) if isinstance(u, str)]
    return Board(board_id, str(raw.get("name") or board_id), url, usb, _plain(raw.get("note", "")))


def fetch_boards(source: Source) -> list[Board]:
    """The source's boards: inline ones plus its live boards.json."""
    boards = list(source.boards)
    if source.boards_url:
        request = urllib.request.Request(source.boards_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            listed = json.loads(response.read(1 << 20).decode("utf-8"))
        known = {b.id for b in boards}
        boards += [parse_board(b, source.manifest_url) for b in listed
                   if isinstance(b, dict) and b.get("board") and b["board"] not in known]
    return boards
