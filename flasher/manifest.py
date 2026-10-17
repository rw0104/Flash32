"""ESP Web Tools manifests: what to flash, where, and for which chip.

The format is the one ESPHome, WLED, Tasmota and the Muse SDK publish:
https://esphome.github.io/esp-web-tools/ (manifest.json). A local .bin with
an offset is turned into the same shape so the rest of the app has one path.
"""

from __future__ import annotations

import hashlib
import json
import struct
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

USER_AGENT = "flash32"
MAX_MANIFEST_BYTES = 1 << 20
MAX_PART_BYTES = 32 << 20

# ESP Web Tools' chipFamily names, and esptool's --chip names for them.
CHIP_FAMILIES = {
    "ESP32": "esp32",
    "ESP32-S2": "esp32s2",
    "ESP32-S3": "esp32s3",
    "ESP32-C2": "esp32c2",
    "ESP32-C3": "esp32c3",
    "ESP32-C5": "esp32c5",
    "ESP32-C6": "esp32c6",
    "ESP32-C61": "esp32c61",
    "ESP32-H2": "esp32h2",
    "ESP32-P4": "esp32p4",
    "ESP8266": "esp8266",
}


class ManifestError(ValueError):
    """A manifest that can't be used; str() is shown to the user."""


@dataclass
class Part:
    path: str          # URL or local path, already resolved
    offset: int


@dataclass
class Build:
    chip_family: str
    parts: list[Part]

    @property
    def esptool_chip(self) -> str:
        return CHIP_FAMILIES[self.chip_family]


@dataclass
class Manifest:
    name: str
    version: str
    builds: list[Build]
    source: str                      # where it came from, for the log
    erase_prompt: bool = False       # new_install_prompt_erase
    min_flash_bytes: int = 0         # Muse SDK extension: flash the partitions need
    extra: dict = field(default_factory=dict)

    def build_for(self, chip_family: str) -> Build | None:
        for build in self.builds:
            if build.chip_family == chip_family:
                return build
        return None

    @property
    def chip_families(self) -> list[str]:
        return [b.chip_family for b in self.builds]


def _is_url(text: str) -> bool:
    return urllib.parse.urlparse(text).scheme in ("http", "https")


def _resolve(base: str, path: str) -> str:
    if _is_url(path):
        return path
    if _is_url(base):
        return urllib.parse.urljoin(base, path)
    return str((Path(base).parent / path).resolve())


def parse_manifest(data: dict, source: str) -> Manifest:
    """Validates a manifest dict and resolves its parts against `source`."""
    if not isinstance(data, dict):
        raise ManifestError("manifest_not_object")
    builds = []
    for raw in data.get("builds") or []:
        family = raw.get("chipFamily") if isinstance(raw, dict) else None
        if family not in CHIP_FAMILIES:
            continue        # an unknown chip; others in the manifest may still fit
        parts = []
        for part in raw.get("parts") or []:
            try:
                path, offset = part["path"], int(part["offset"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ManifestError("manifest_bad_part") from exc
            if not isinstance(path, str) or not path or offset < 0:
                raise ManifestError("manifest_bad_part")
            parts.append(Part(_resolve(source, path), offset))
        if parts:
            builds.append(Build(family, sorted(parts, key=lambda p: p.offset)))
    if not builds:
        raise ManifestError("manifest_no_builds")
    return Manifest(
        name=str(data.get("name") or "Firmware"),
        version=str(data.get("version") or ""),
        builds=builds,
        source=source,
        erase_prompt=bool(data.get("new_install_prompt_erase")),
        min_flash_bytes=int(data.get("min_flash_bytes") or 0),
    )


def _fetch(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ManifestError("download_too_large")
    return data


def load_manifest(location: str) -> Manifest:
    """A manifest from a URL, a manifest.json, or a folder holding one."""
    location = location.strip()
    if _is_url(location):
        raw = _fetch(location, MAX_MANIFEST_BYTES)
    else:
        path = Path(location)
        if path.is_dir():
            path = path / "manifest.json"
        if not path.is_file():
            raise ManifestError("manifest_not_found")
        location = str(path.resolve())
        raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest_not_json") from exc
    return parse_manifest(data, location)


def single_image(path: str, offset: int, chip_family: str) -> Manifest:
    """One local .bin at `offset`, for a chip the user picked."""
    file = Path(path)
    if not file.is_file() or not file.stat().st_size:
        raise ManifestError("bin_not_found")
    if chip_family not in CHIP_FAMILIES:
        raise ManifestError("manifest_no_builds")
    return Manifest(name=file.name, version="", source=str(file.resolve()),
                    builds=[Build(chip_family, [Part(str(file.resolve()), offset)])])


def fetch_part(part: Part, cache: Path, progress=None, cancelled=lambda: False) -> Path:
    """The part as a local file: itself, or downloaded into `cache`."""
    if not _is_url(part.path):
        file = Path(part.path)
        if not file.is_file() or not file.stat().st_size:
            raise ManifestError("part_missing")
        return file
    cache.mkdir(parents=True, exist_ok=True)
    name = Path(urllib.parse.urlparse(part.path).path).name or "part.bin"
    target = cache / (hashlib.sha256(part.path.encode()).hexdigest()[:16] + "-" + name)
    temp = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(part.path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response, open(temp, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancelled():
                raise InterruptedError
            chunk = response.read(65536)
            if not chunk:
                break
            done += len(chunk)
            if done > MAX_PART_BYTES:
                raise ManifestError("download_too_large")
            out.write(chunk)
            if progress:
                progress(done, total)
    if not done:
        temp.unlink(missing_ok=True)
        raise ManifestError("part_missing")
    temp.replace(target)
    return target


@dataclass
class Partition:
    label: str
    type: int
    subtype: int
    offset: int
    size: int

    @property
    def is_app(self) -> bool:
        return self.type == 0


# Where ESP-IDF puts the partition table: the default, and after a large bootloader.
PARTITION_TABLE_OFFSETS = (0x8000, 0x10000)
PARTITION_TABLE_SIZE = 0xC00


def partition_table(table: bytes) -> list[Partition]:
    """The entries of a binary partition table, in order."""
    parts = []
    for i in range(0, len(table) - 31, 32):
        magic, ptype, subtype, offset, size = struct.unpack_from("<HBBII", table, i)
        if magic != 0x50AA:
            break
        label = table[i + 12:i + 28].split(b"\0", 1)[0].decode("utf-8", "replace")
        parts.append(Partition(label, ptype, subtype, offset, size))
    return parts


def find_partition_table(blob: bytes, base: int) -> list[Partition]:
    """The partition table in flash read from `base`, at either usual place.
    The later place wins: firmware with a large bootloader leaves an earlier
    firmware's table at 0x8000 behind, but a table at 0x10000 would have been
    overwritten by the app of firmware that uses 0x8000."""
    for offset in sorted(PARTITION_TABLE_OFFSETS, reverse=True):
        start = offset - base
        if 0 <= start < len(blob):
            parts = partition_table(blob[start:start + PARTITION_TABLE_SIZE])
            if parts:
                return parts
    return []


def first_app(parts: list[Partition]) -> Partition | None:
    """The app a fresh boot would run: factory, else the first OTA slot."""
    apps = [p for p in parts if p.is_app]
    factory = [p for p in apps if p.subtype == 0]
    return (factory or sorted(apps, key=lambda p: p.subtype) or [None])[0]


def partitions_end(table: bytes) -> int:
    """Where the last partition of a binary partition table ends."""
    return max((p.offset + p.size for p in partition_table(table)), default=0)


@dataclass
class AppInfo:
    project: str
    version: str
    idf: str
    date: str

    def summary(self) -> str:
        return " ".join(t for t in (self.project, self.version) if t) + (f" (IDF {self.idf})" if self.idf else "")


APP_HEADER_SIZE = 0x100
_APP_DESC_MAGIC = 0xABCD5432


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def app_description(image: bytes) -> AppInfo | None:
    """esp_app_desc_t from the start of an app image (it follows the image
    header and the first segment header), or None if there's no app there."""
    if len(image) < 176 or image[0] != 0xE9:
        return None
    if struct.unpack_from("<I", image, 32)[0] != _APP_DESC_MAGIC:
        return None
    return AppInfo(project=_text(image[80:112]), version=_text(image[48:80]),
                   idf=_text(image[144:176]), date=_text(image[128:144]))


def describe_image(image: bytes) -> AppInfo | None:
    """The app in a full flash image (a backup), found via its partition table."""
    app = first_app(find_partition_table(image, 0))
    return app_description(image[app.offset:app.offset + APP_HEADER_SIZE]) if app else None
