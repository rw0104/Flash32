# Flash32

[中文](README.md)

An ESP32 flasher for people who don't use a command line: pick the firmware, plug in USB, click once. No Python, ESP-IDF or toolchain to install.

## What it does

- **Firmware catalog**: open-source projects' live firmware lists. Pick your board and flash. Ships with [Muse Gadget](https://github.com/facebookincubator/muse-gadget-sdk) (Meta's open-source voice gadget, nine official boards).
- **Any open-source firmware**: paste an ESP Web Tools manifest URL (`manifest.json`), the kind behind the "Install" buttons of ESPHome, WLED, Tasmota and others.
- **Local files**: a `manifest.json`, a firmware folder, or one `.bin` with a chip and offset.
- **Detection**: plug a board in and its port is picked and identified: chip, flash size, MAC and the firmware it runs now. Catalog boards that suit it are ticked ✓, and one is picked when it's clear which (a board flashed here before is recognised). Firmware built for something else is refused.
- **Backup and restore**: before a board's first flash, its whole flash is copied to `Documents\Flash32\backups` (can be turned off; manual backups any time). Restore backup puts the factory firmware back.
- **Device setup**: after flashing Muse firmware, writes its SDK token (and an optional Qwen TTS key) over serial. Secrets are hidden in the log.
- **Serial log** for troubleshooting.
- Chinese and English, following the system language or picked by hand.

## Use it

1. Download `Flash32-win64.zip`, unzip, run `Flash32.exe`. Keep the `_internal` folder next to it.
2. **Firmware**: pick a project and your board in the catalog, or switch to URL or Local file.
3. **Device**: connect the board with a **data-capable** USB cable, and it's picked and identified for you; if not, click Refresh or Detect.
4. Click **Flash** and keep the cable in until the bar finishes.
5. For Muse: fill in the SDK token under Device setup, click Write to device, then add the gadget in the Muse app.

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

It holds the images and an ESP Web Tools-compatible `manifest.json`. Host it (GitHub Pages, a release) and share the URL. To appear in the built-in catalog, add an entry to `data/catalog.json` in a PR, listing `boards` inline or pointing at a `boards.json` like Muse does.

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
- [Muse Gadget SDK](https://github.com/facebookincubator/muse-gadget-sdk) (Meta, Apache-2.0).

Not affiliated with Espressif or Meta. License: Apache-2.0.
