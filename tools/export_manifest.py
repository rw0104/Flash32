"""Turn an ESP-IDF build directory into a firmware folder Flash32 (and ESP
Web Tools) can flash: the images plus a manifest.json.

    python tools/export_manifest.py BUILD_DIR OUT_DIR "Name" VERSION

Images are flashed one by one at their own offsets, like `idf.py flash`, not
merged: a merged image pads the NVS gap with 0xFF and would wipe the device's
saved settings on every update. Host OUT_DIR anywhere (GitHub Pages, a
release) and give users its manifest.json URL, or the folder itself.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flasher.manifest import CHIP_FAMILIES, partitions_end  # noqa: E402

ESPTOOL_TO_FAMILY = {chip: family for family, chip in CHIP_FAMILIES.items()}


def export(build: Path, out: Path, name: str, version: str) -> Path:
    args = json.loads((build / "flasher_args.json").read_text(encoding="utf-8"))
    chip = args["extra_esptool_args"]["chip"]
    out.mkdir(parents=True, exist_ok=True)
    parts = []
    for offset, file in sorted(args["flash_files"].items(), key=lambda kv: int(kv[0], 16)):
        dest = Path(file).name
        shutil.copyfile(build / file, out / dest)
        parts.append({"path": dest, "offset": int(offset, 16)})
    table = (build / args["partition-table"]["file"]).read_bytes()
    manifest = {
        "name": name,
        "version": version,
        "new_install_prompt_erase": True,
        "min_flash_bytes": partitions_end(table),
        "builds": [{"chipFamily": ESPTOOL_TO_FAMILY[chip], "parts": parts}],
    }
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    print(export(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]))
