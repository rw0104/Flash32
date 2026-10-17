# Flash32

[中文](README.md)

An ESP32 flasher for people who don't use a command line: plug in a board, it's identified, the open-source firmware that suits it is listed, click once. No Python, ESP-IDF or toolchain to install.

## What it does

- **Starts from the board**: plug in USB and its port is picked and identified: chip, flash size, in-package PSRAM, MAC, and the firmware it runs now. The catalog lists only projects that suit the board and picks the right build (a 16 MB ESP32-S3 with octal PSRAM gets that build, for example).
- **Built-in catalog**, reading each project's latest release:
  - [WLED](https://kno.wled.ge): LED strip control
  - [Tasmota](https://tasmota.github.io/docs/): smart plugs, switches and sensors (with display, IR, Bluetooth and other variants)
  - [ESPHome](https://esphome.io): starter firmware for Home Assistant devices
  - [MicroPython](https://micropython.org): Python on the board
  - [Muse Gadget](https://github.com/facebookincubator/muse-gadget-sdk): a voice assistant built per board (nine official boards, plus a community port for the LCKFB SZPI ESP32-S3)
- **Per chip vs per board**: WLED, Tasmota, ESPHome and MicroPython build one firmware per chip, so the detected chip is enough. Firmware with screens and audio, like Muse, differs per board, and a chip can't tell which board it's on, so the list is narrowed to the boards that fit and you confirm yours (it's picked for you when only one fits, or when this board was flashed here before).
- **Any open-source firmware**: paste an ESP Web Tools manifest URL (`manifest.json`).
- **Local files**: a `manifest.json`, a firmware folder, or one `.bin` with a chip and offset.
- **Backup and restore**: before a board's first flash, its whole flash is copied to `Documents\Flash32\backups` (can be turned off; manual backups any time). Restore backup puts the factory firmware back.
- **Checks before writing**: firmware for another chip, or needing more flash, is refused.
- **Device setup**: when firmware needs it (Muse's SDK token, say), it's written over serial after flashing. Secrets are hidden in the log.
- **Serial log** for troubleshooting.
- Chinese and English, following the system language or picked by hand.

## Use it

1. Download `Flash32-win64.zip`, unzip, run `Flash32.exe`. Keep the `_internal` folder next to it.
2. **Connect**: plug the board in with a **data-capable** USB cable; it's picked and identified for you. If not, click Refresh or Detect.
3. **Firmware**: the catalog now shows what suits the board; pick a project. For per-board firmware, also pick your board. Or switch to URL or Local file.
4. Click **Flash** and keep the cable in until the bar finishes. A board's first flash backs up its original firmware first.
5. Finish setup as the firmware says: WLED and Tasmota open a Wi-Fi hotspot after flashing; Muse needs its SDK token under Device setup.

### Troubleshooting

- **No port**: try another cable (some only charge). CH340 boards may need the [driver](https://www.wch.cn/downloads/CH341SER_EXE.html).
- **Can't reach the bootloader**: hold BOOT, tap RESET (or replug), release BOOT, retry; or use 115200.
- **Port in use**: close other serial tools, including this app's serial log.
- **Clean install** erases the whole flash: do it when switching firmware, not when updating the same one (that keeps Wi-Fi and pairing).

## For firmware authors

Export an ESP-IDF build directory as a firmware folder:

```bash
python tools/export_manifest.py <build dir> <out dir> "Firmware name" VERSION
```

It holds the images and an ESP Web Tools-compatible `manifest.json`. Host it (GitHub Pages, a release) and share the URL.

To appear in the built-in catalog, add an entry to `data/catalog.json` in a PR. Set `kind` to `chip` (one firmware per chip) or `board` (one per board). Options can come from:

- `options`: listed inline, each with a `manifest_url`, or a manifest `template` holding `{placeholders}` filled from `version` (`{"github": "owner/repo"}` for the latest release, or `{"page": url, "regex": pattern with named groups}`);
- `boards_url`: a `boards.json`, as Muse publishes;
- `index_url` + `index_key`: a manifest index, as Tasmota publishes.

A chip can have several builds: mark each with `"flash32": {"flash": "8MB", "psram": "octal"}` and the detected board picks one. `"detect_flash_size": true` has esptool set the bootloader's flash size to the real one.

Images are written at their own offsets like `idf.py flash`, not merged, so updates keep the device's saved settings.

## Run from source / build

Python 3.12+, on Windows, macOS or Linux:

```bash
python -m pip install -r requirements.txt
python app.py
python -m unittest discover -s tests -t .
```

Windows package (creates its own build environment):

```powershell
.\build.ps1          # dist\Flash32\Flash32.exe
```

## Credits

- [esptool](https://github.com/espressif/esptool) (Espressif) does the flashing.
- Manifest format: [ESP Web Tools](https://esphome.github.io/esp-web-tools/).
- The catalog's firmware belongs to the WLED, Tasmota, ESPHome, MicroPython and Muse Gadget SDK projects, under their own licenses; this tool only downloads and flashes it.

Not affiliated with Espressif or any of those projects. License: Apache-2.0.
