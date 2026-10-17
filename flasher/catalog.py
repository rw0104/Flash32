"""The firmware catalog: open-source projects, and what each offers to flash.

data/catalog.json lists sources. A source's "kind" says how its options
differ: "chip" sources (WLED, Tasmota, ...) build one firmware per chip, so
the detected chip picks the build; "board" sources (Muse, ...) build one per
board, which the user has to confirm, since a chip doesn't know its board.

A source's options come from any mix of:
- "options": inline [{id, name, manifest_url | template, usb?, note?}];
  a template is a manifest whose strings may hold {placeholders} filled from
  the source's "version" (see resolve_version);
- "boards_url": a boards.json in the Muse web flasher's format
  ([{board, name, usb?, note?}]), with "manifest_url" a template holding {board};
- "index_url" + "index_key": a Tasmota-style index
  ({key: [{path, name, chipFamilies}]}).
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import manifest
from .manifest import USER_AGENT


def resource_dir() -> Path:
    """Bundled data: next to the package, or PyInstaller's unpack folder."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


@dataclass
class Option:
    """One thing a source offers: a board's firmware, or a firmware variant."""
    id: str
    name: str
    manifest_url: str = ""
    template: dict | None = None
    usb: list[str] = field(default_factory=list)
    note: str = ""
    chips: list[str] = field(default_factory=list)          # empty until known
    chip_flash: dict[str, int] = field(default_factory=dict)  # chip -> least flash any build needs
    min_flash: int = 0

    def learn(self, fw: manifest.Manifest) -> None:
        """Takes the chips and flash needs from the option's manifest."""
        self.chips, self.min_flash = fw.chip_families, fw.min_flash_bytes
        self.chip_flash = {chip: min(b.flash_bytes for b in fw.builds if b.chip_family == chip)
                           for chip in self.chips}

    def fits(self, family: str, flash_bytes: int) -> bool:
        if family not in self.chips:
            return False
        need = max(self.min_flash, self.chip_flash.get(family, 0))
        return not (need and flash_bytes and flash_bytes < need)


@dataclass
class Source:
    id: str
    name: dict
    description: dict
    homepage: str
    kind: str = "chip"                # "chip" or "board"
    setup: str = ""
    boards_url: str = ""
    manifest_url: str = ""
    index_url: str = ""
    index_key: str = ""
    version: dict = field(default_factory=dict)
    inline: list[Option] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)   # inline + listed, once loaded
    loaded: bool = False
    error: str = ""

    def text(self, attr: str, lang: str) -> str:
        value = getattr(self, attr)
        return value.get(lang) or value.get("en") or next(iter(value.values()), "")

    def fits(self, family: str, flash_bytes: int) -> bool:
        """Whether something here may suit the chip. Unknown counts as maybe."""
        if not self.loaded:
            return True
        return any(o.fits(family, flash_bytes) or not o.chips for o in self.options)


def _plain(note: str) -> str:
    """Board notes are HTML on the web flasher; show them as text."""
    return html.unescape(re.sub(r"<[^>]+>", "", note or "")).strip()


def _text(value) -> str:
    return _plain(value) if isinstance(value, str) else ""


def load_catalog(path: Path | None = None) -> list[Source]:
    path = path or resource_dir() / "data" / "catalog.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = []
    for raw in data.get("sources", []):
        source = Source(
            id=raw["id"], name=raw["name"], description=raw.get("description", {}),
            homepage=raw.get("homepage", ""), kind=raw.get("kind", "chip"), setup=raw.get("setup", ""),
            boards_url=raw.get("boards_url", ""), manifest_url=raw.get("manifest_url", ""),
            index_url=raw.get("index_url", ""), index_key=raw.get("index_key", ""),
            version=raw.get("version", {}),
        )
        source.inline = [parse_option(o, source.manifest_url) for o in raw.get("options", [])]
        sources.append(source)
    return sources


def parse_option(raw: dict, url_template: str = "") -> Option:
    """An inline option, or a boards.json entry ("board" for its id)."""
    option_id = str(raw.get("id") or raw["board"])
    template = raw.get("template") if isinstance(raw.get("template"), dict) else None
    url = raw.get("manifest_url") or ("" if template else url_template.replace("{board}", option_id))
    usb = [u for u in raw.get("usb", []) if isinstance(u, str)]
    option = Option(option_id, str(raw.get("name") or option_id), url, template, usb, _text(raw.get("note")))
    if template:
        # A template's chips are known without fetching anything.
        option.learn(manifest.parse_manifest(template, ""))
    return option


def _get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read(4 << 20).decode("utf-8"))


def list_options(source: Source) -> list[Option]:
    """The source's options: inline ones, then its live lists."""
    options = list(source.inline)
    known = {o.id for o in options}
    if source.boards_url:
        listed = _get_json(source.boards_url)
        options += [parse_option(b, source.manifest_url) for b in listed
                    if isinstance(b, dict) and b.get("board") and str(b["board"]) not in known]
    if source.index_url:
        for entry in _get_json(source.index_url).get(source.index_key, []):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            path = entry["path"]
            option = Option(Path(urllib.parse.urlparse(path).path).name, str(entry.get("name") or path), path)
            option.chips = list(dict.fromkeys(c for c in entry.get("chipFamilies", [])
                                              if c in manifest.CHIP_FAMILIES))
            if option.id not in known:
                options.append(option)
                known.add(option.id)
    return options


def describe_options(options: list[Option], cancelled=lambda: False) -> None:
    """Reads the manifests of options whose chips aren't known yet. One that
    can't be read keeps empty chips: unknown, not unsuitable."""
    def read(option: Option) -> None:
        if cancelled():
            return
        try:
            option.learn(manifest.load_manifest(option.manifest_url))
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(read, [o for o in options if not o.chips and o.manifest_url]))


def load_sources(sources: list[Source], on_listed=None, cancelled=lambda: False) -> None:
    """Lists every source's options (calling on_listed once all are listed),
    then reads what's needed to know which chips each option suits."""
    def list_one(source: Source) -> None:
        try:
            source.options = list_options(source)
            source.error = ""
        except Exception as exc:
            source.options = list(source.inline)
            source.error = f"{type(exc).__name__}: {exc}"
        source.loaded = True

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(list_one, sources))
    if on_listed:
        on_listed()
    describe_options([o for s in sources for o in s.options], cancelled)


def resolve_version(spec: dict) -> dict[str, str]:
    """The placeholders a template's latest release fills in.

    {"github": "owner/repo"} gives {tag} and {version} (the tag without a
    leading v) of the latest release; {"page": url, "regex": pattern} gives
    the named groups of the pattern's first match on the page.
    """
    if spec.get("github"):
        tag = str(_get_json(f"https://api.github.com/repos/{spec['github']}/releases/latest")["tag_name"])
        return {"tag": tag, "version": tag[1:] if tag[:1] in "vV" else tag}
    if spec.get("page"):
        request = urllib.request.Request(spec["page"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            page = response.read(4 << 20).decode("utf-8", "replace")
        match = re.search(spec["regex"], page)
        if not match:
            raise manifest.ManifestError("err_no_release")
        return {k: v for k, v in match.groupdict().items() if v is not None}
    return {}


def load_option(source: Source, option: Option) -> manifest.Manifest:
    """The option's manifest, with a template filled from the latest release."""
    if option.template:
        data = manifest.fill_template(option.template, resolve_version(source.version))
        fw = manifest.parse_manifest(data, source.homepage)
    else:
        fw = manifest.load_manifest(option.manifest_url)
    option.learn(fw)
    return fw


def match_options(options: list[Option], family: str, flash_bytes: int, usb_id: str = "") -> list[Option]:
    """Options that suit the detected chip and flash. When some also list the
    port's USB id, only those."""
    fits = [o for o in options if o.fits(family, flash_bytes)]
    by_usb = [o for o in fits if usb_id and usb_id.lower() in (u.lower() for u in o.usb)]
    return by_usb or fits
