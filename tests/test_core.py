import json
import tempfile
import unittest
import sys
from pathlib import Path

from flasher import catalog, devices, esptool_jobs, i18n, manifest, provision

MUSE_MANIFEST = {
    "name": "Muse Gadget (Espressif ESP32-S3-BOX-3)",
    "version": "999.0.0+f60347a",
    "new_install_prompt_erase": True,
    "min_flash_bytes": 8527872,
    "builds": [{"chipFamily": "ESP32-S3", "parts": [
        {"path": "muse-gadget.bin", "offset": 131072},
        {"path": "bootloader.bin", "offset": 0},
    ]}],
}


class ManifestTests(unittest.TestCase):
    def test_parts_resolve_against_a_url_and_sort_by_offset(self):
        url = "https://example.org/fw/box3/manifest.json"
        fw = manifest.parse_manifest(MUSE_MANIFEST, url)
        build = fw.build_for("ESP32-S3")
        self.assertEqual([p.offset for p in build.parts], [0, 131072])
        self.assertEqual(build.parts[0].path, "https://example.org/fw/box3/bootloader.bin")
        self.assertEqual(build.esptool_chip, "esp32s3")
        self.assertEqual(fw.min_flash_bytes, 8527872)
        self.assertTrue(fw.erase_prompt)
        self.assertIsNone(fw.build_for("ESP32-C3"))

    def test_unknown_chips_are_skipped_but_none_left_is_an_error(self):
        data = {"builds": [{"chipFamily": "RP2040", "parts": [{"path": "a.bin", "offset": 0}]}]}
        with self.assertRaisesRegex(manifest.ManifestError, "manifest_no_builds"):
            manifest.parse_manifest(data, "https://x/manifest.json")

    def test_bad_parts_are_rejected(self):
        for part in ({"path": "a.bin"}, {"path": "", "offset": 0}, {"path": "a.bin", "offset": -1}):
            data = {"builds": [{"chipFamily": "ESP32", "parts": [part]}]}
            with self.assertRaises(manifest.ManifestError):
                manifest.parse_manifest(data, "https://x/manifest.json")

    def test_local_folder_and_single_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "manifest.json").write_text(json.dumps(MUSE_MANIFEST), encoding="utf-8")
            (folder / "bootloader.bin").write_bytes(b"\xe9" * 16)
            fw = manifest.load_manifest(str(folder))
            part = fw.builds[0].parts[0]
            self.assertEqual(Path(part.path), (folder / "bootloader.bin").resolve())
            self.assertEqual(manifest.fetch_part(part, folder / "cache"), Path(part.path))
            single = manifest.single_image(str(folder / "bootloader.bin"), 0x1000, "ESP32-C3")
            self.assertEqual(single.builds[0].parts[0].offset, 0x1000)
            with self.assertRaisesRegex(manifest.ManifestError, "manifest_not_found"):
                manifest.load_manifest(str(folder / "missing"))

    def test_partitions_end(self):
        import struct
        table = struct.pack("<HBBII16sI", 0x50AA, 1, 2, 0x9000, 0x6000, b"nvs", 0)
        table += struct.pack("<HBBII16sI", 0x50AA, 0, 0x10, 0x20000, 0x400000, b"ota_0", 0)
        table += b"\xff" * 32
        self.assertEqual(manifest.partitions_end(table), 0x420000)


def app_header(project: bytes, version: bytes, idf: bytes = b"v5.2.3") -> bytes:
    """The start of an ESP-IDF app image: image header, segment header, esp_app_desc_t."""
    import struct
    head = b"\xe9" + b"\x00" * 31 + struct.pack("<III", 0xABCD5432, 0, 0) + b"\x00" * 4
    desc = version.ljust(32, b"\0") + project.ljust(32, b"\0") + b"18:11:59".ljust(16, b"\0")
    desc += b"Mar  3 2025".ljust(16, b"\0") + idf.ljust(32, b"\0")
    return (head + desc).ljust(manifest.APP_HEADER_SIZE, b"\xff")


def flash_image(table_at: int, apps: list[tuple[int, int, bytes]]) -> bytes:
    """A flash image with a partition table at `table_at` and (subtype, offset, header) apps."""
    import struct
    size = max(offset for _, offset, _ in apps) + 0x1000
    image = bytearray(b"\xff" * size)
    table = struct.pack("<HBBII16sI", 0x50AA, 1, 2, 0x9000, 0x6000, b"nvs", 0)
    for subtype, offset, header in apps:
        table += struct.pack("<HBBII16sI", 0x50AA, 0, subtype, offset, 0x100000, b"app", 0)
        image[offset:offset + len(header)] = header
    image[table_at:table_at + len(table)] = table
    return bytes(image)


class FirmwareInfoTests(unittest.TestCase):
    def test_app_in_a_factory_layout(self):
        image = flash_image(0x8000, [(0, 0x10000, app_header(b"handheld", b"e82e8eb"))])
        app = manifest.describe_image(image)
        self.assertEqual((app.project, app.version, app.idf), ("handheld", "e82e8eb", "v5.2.3"))
        self.assertEqual(app.summary(), "handheld e82e8eb (IDF v5.2.3)")

    def test_table_after_a_large_bootloader_and_ota_slots(self):
        image = flash_image(0x10000, [(0x11, 0x120000, app_header(b"other", b"2")),
                                      (0x10, 0x20000, app_header(b"muse-gadget", b"999.0.0"))])
        parts = manifest.find_partition_table(image, 0)
        self.assertEqual(manifest.first_app(parts).offset, 0x20000)
        self.assertEqual(manifest.describe_image(image).project, "muse-gadget")
        # Read from 0x8000, as the detect job does.
        self.assertEqual(len(manifest.find_partition_table(image[0x8000:0x11000], 0x8000)), 3)
        # The factory firmware's table, left at 0x8000, doesn't fool it.
        old = flash_image(0x8000, [(0, 0x10000, b"")])
        mixed = old[0x8000:0x9000].join([image[:0x8000], image[0x9000:]])
        self.assertEqual(manifest.describe_image(mixed).project, "muse-gadget")

    def test_blank_flash_and_non_app_data(self):
        self.assertIsNone(manifest.describe_image(b"\xff" * 0x20000))
        self.assertIsNone(manifest.app_description(b"\xe9" + b"\x00" * 255))
        self.assertIsNone(manifest.first_app([]))


ESPTOOL_FLASH_ID = """esptool v5.1.0
Connecting...
Detecting chip type... ESP32-S3
Chip type:          ESP32-S3 (QFN56) (revision v0.2)
Features:           Wi-Fi, BT 5 (LE), Dual Core + LP Core, 240MHz, Embedded PSRAM 8MB (AP_3v3)
MAC:                30:ed:a0:a6:b5:80
Detected flash size: 16MB
"""


class EsptoolTests(unittest.TestCase):
    def test_parse_chip(self):
        info = esptool_jobs.parse_chip(ESPTOOL_FLASH_ID)
        self.assertEqual(info.family, "ESP32-S3")
        self.assertEqual(info.flash_bytes, 16 << 20)
        self.assertEqual(info.mac, "30:ed:a0:a6:b5:80")
        self.assertIn("PSRAM", info.features)

    def test_parse_chip_older_esptool(self):
        info = esptool_jobs.parse_chip("Chip is ESP32-C3 (QFN32) (revision v0.4)\nDetected flash size: 4MB\n")
        self.assertEqual((info.family, info.flash_bytes), ("ESP32-C3", 4 << 20))

    def test_flash_args(self):
        args = esptool_jobs.flash_args("COM4", 460800, "esp32s3", [(0x20000, "app.bin"), (0, "boot.bin")], True)
        self.assertEqual(args[:4], ["--chip", "esp32s3", "--port", "COM4"])
        self.assertIn("--erase-all", args)
        self.assertEqual(args[-4:], ["0x0", "boot.bin", "0x20000", "app.bin"])

    def test_progress_follows_addresses_and_never_steps_back(self):
        bar = esptool_jobs.FlashProgress({0: 20000, 0x10000: 3072, 0x20000: 3000000})
        values = [bar.feed(line) for line in (
            "Compressed 20000 bytes to 13000...",
            "Writing at 0x00000000 [                    ]   0.0% 0/13000 bytes... ",
            "Writing at 0x00005000 [====================] 100.0% 13000/13000 bytes... ",
            "Writing at 0x00010000 [====================] 100.0% 150/150 bytes... ",
            "Writing at 0x00021000 [=>                  ]   3.2% 65536/2011014 bytes... ",
            "Writing at 0x00191000 [=========>          ]  50.0% 1005507/2011014 bytes... ",
            "Writing at 0x00301000 [====================] 100.0% 2011014/2011014 bytes... ",
        )]
        self.assertIsNone(values[0])
        numbers = [v for v in values if v is not None]
        self.assertEqual(numbers, sorted(numbers))
        self.assertAlmostEqual(numbers[-1], 1.0)
        self.assertGreater(numbers[-2], 0.4)

    def test_read_and_reset_args(self):
        self.assertEqual(esptool_jobs.detect_args("COM4", after="no-reset")[-3:], ["--after", "no-reset", "flash-id"])
        args = esptool_jobs.read_args("COM4", 921600, "esp32s3", 0, 16 << 20, "out.bin")
        self.assertEqual(args[:2], ["--chip", "esp32s3"])
        self.assertEqual(args[-4:], ["read-flash", "0x0", "0x1000000", "out.bin"])
        self.assertIn("hard-reset", esptool_jobs.reset_args("COM4"))

    def test_psram_kind(self):
        self.assertEqual(esptool_jobs.parse_chip(ESPTOOL_FLASH_ID).psram, "octal")
        quad = esptool_jobs.parse_chip("Chip type: ESP32-S3 (QFN56)\nFeatures: Wi-Fi, Embedded PSRAM 2MB (AP_3v3)\n")
        self.assertEqual(quad.psram, "quad")
        self.assertEqual(esptool_jobs.parse_chip("Chip type: ESP32-C3\nFeatures: Wi-Fi, BLE\n").psram, "")

    def test_flash_size_detect_only_when_asked(self):
        plain = esptool_jobs.flash_args("COM4", 460800, "esp32", [(0x1000, "b.bin")], False)
        self.assertNotIn("--flash-size", plain)
        args = esptool_jobs.flash_args("COM4", 460800, "esp32", [(0x1000, "b.bin")], False, True)
        self.assertEqual(args[args.index("write-flash") + 1:][:2], ["--flash-size", "detect"])

    def test_sizes(self):
        self.assertEqual([manifest.parse_size(t) for t in ("16MB", "512KB", "4M", 1024, "", None, "big")],
                         [16 << 20, 512 << 10, 4 << 20, 1024, 0, 0, 0])

    def test_read_progress(self):
        line = "Reading from 0x00800000 [==============>               ]  50.0% 8388608/16777216 bytes... "
        self.assertAlmostEqual(esptool_jobs.read_progress(line), 0.5)
        self.assertIsNone(esptool_jobs.read_progress("Writing at 0x00010000 [=>  ] 3.2%"))
        self.assertIsNone(esptool_jobs.read_progress("Read 16777216 bytes from 0x00000000 in 180.2 seconds"))

    def test_failure_hints(self):
        from flasher import workers
        self.assertEqual(workers.explain_esptool_failure("could not open port 'COM4': PermissionError"), "err_port_busy")
        self.assertEqual(workers.explain_esptool_failure("Failed to connect to ESP32-S3: No serial data received."),
                         "err_no_bootloader")
        self.assertEqual(workers.explain_esptool_failure("something else"), "err_esptool")


class FakeSerial:
    def __init__(self, replies: bytes):
        self.replies = replies
        self.written = b""
        self.in_waiting = 0

    def write(self, data):
        self.written += data
        return len(data)

    def read(self, n):
        chunk, self.replies = self.replies[:n], self.replies[n:]
        return chunk


class ProvisionTests(unittest.TestCase):
    def test_ok_after_noise(self):
        conn = FakeSerial(b"I (1) boot: noise\r\n@sdk_token ok\r\n")
        self.assertEqual(provision.send_setting(conn, "sdk_token", "mgst_" + "a" * 20, timeout=1), "ok")
        self.assertEqual(conn.written, b"\n>sdk_token=mgst_" + b"a" * 20 + b"\n")

    def test_error_and_silence(self):
        with self.assertRaises(provision.ProvisionError) as ctx:
            provision.send_setting(FakeSerial(b"@tts_key error invalid\n"), "tts_key", "sk-x", timeout=1)
        self.assertEqual((str(ctx.exception), ctx.exception.detail), ("err_device_rejected", "invalid"))
        with self.assertRaisesRegex(provision.ProvisionError, "err_no_answer"):
            provision.send_setting(FakeSerial(b"@other ok\n"), "tts_key", "sk-x", timeout=0.3)

    def test_field_validation_and_restart_last(self):
        fields = {f.command: f for f in provision.PROFILES["muse"]}
        self.assertTrue(fields["sdk_token"].valid("mgst_" + "A1_-" * 8))
        self.assertFalse(fields["sdk_token"].valid("mgst_short"))
        self.assertTrue(fields["tts_key"].valid("sk-ws-abc.DEF_123-xyz"))
        self.assertFalse(fields["tts_key"].valid("sk-has space"))
        for rate in ("0.5", "1", "1.0", "1.1", "1.25", "2.0"):
            self.assertTrue(fields["tts_rate"].valid(rate), rate)
        for rate in ("0.4", "2.1", "110", "1.234", "-1", ""):
            self.assertFalse(fields["tts_rate"].valid(rate), rate)
        self.assertEqual(provision.PROFILES["muse"][-1].command, "sdk_token")
        self.assertTrue(all(not f.restarts for f in provision.PROFILES["muse"][:-1]))

    def test_redact(self):
        text = provision.redact("stored mgst_EXAMPLEabcdefghijkl and key sk-test-1234567.abc")
        self.assertNotIn("abcdefghijkl", text)
        self.assertNotIn("1234567", text)
        self.assertIn("mgst_…", text)


class CatalogAndPortTests(unittest.TestCase):
    def test_bundled_catalog(self):
        sources = catalog.load_catalog()
        by_id = {s.id: s for s in sources}
        self.assertEqual(list(by_id), ["wled", "tasmota", "esphome", "micropython", "muse"])
        for source in sources:
            self.assertTrue(source.text("name", "zh") and source.text("description", "en"), source.id)
            self.assertIn(source.kind, ("chip", "board"))
        muse = by_id["muse"]
        self.assertEqual((muse.kind, muse.setup), ("board", "muse"))
        self.assertIn("{board}", muse.manifest_url)
        self.assertIn(muse.setup, provision.PROFILES)
        self.assertEqual(muse.inline[0].id, "lckfb-szpi-esp32-s3")
        # Templates are parsed up front, so their chips are known offline.
        wled = by_id["wled"].inline[0]
        self.assertIn("ESP32-S3", wled.chips)
        self.assertTrue(wled.fits("ESP32-S3", 16 << 20))
        self.assertFalse(wled.fits("ESP32-S3", 2 << 20))
        self.assertFalse(wled.fits("ESP32-C6", 4 << 20))
        self.assertTrue(by_id["micropython"].inline[0].fits("ESP8266", 1 << 20))

    def test_templates_pick_the_build_for_the_board(self):
        sources = {s.id: s for s in catalog.load_catalog()}
        wled = manifest.parse_manifest(manifest.fill_template(
            sources["wled"].inline[0].template, {"tag": "v16.0.1", "version": "16.0.1"}), "")
        self.assertEqual(wled.version, "16.0.1")

        def app(fw, *board):
            build = fw.build_for(*board)
            return build and build.parts[-1].path.rsplit("/", 1)[-1]

        self.assertEqual(app(wled, "ESP32-S3", 16 << 20, "octal"), "WLED_16.0.1_ESP32-S3_16MB_opi.bin")
        self.assertEqual(app(wled, "ESP32-S3", 8 << 20, ""), "WLED_16.0.1_ESP32-S3_8MB_none.bin")
        self.assertEqual(app(wled, "ESP32-S3", 8 << 20, "octal"), "WLED_16.0.1_ESP32-S3_8MB_opi.bin")
        self.assertEqual(app(wled, "ESP32-S3", 32 << 20, "octal"), "WLED_16.0.1_ESP32-S3_16MB_opi.bin")
        self.assertEqual(app(wled, "ESP8266", 1 << 20, ""), "WLED_16.0.1_ESP01.bin")
        self.assertIsNone(app(wled, "ESP32", 2 << 20, ""))
        self.assertTrue(wled.build_for("ESP32", 4 << 20).detect_flash_size)
        self.assertIn("partitions_c3_4m.bin", wled.build_for("ESP32", 4 << 20).parts[1].path)

        mp = manifest.parse_manifest(manifest.fill_template(
            sources["micropython"].inline[0].template, {"date": "20260824", "version": "v1.29.0"}), "")
        self.assertEqual(app(mp, "ESP32-S3", 16 << 20, "octal"), "ESP32_GENERIC_S3-SPIRAM_OCT-20260824-v1.29.0.bin")
        self.assertEqual(app(mp, "ESP32-S3", 4 << 20, ""), "ESP32_GENERIC_S3-20260824-v1.29.0.bin")
        self.assertEqual(app(mp, "ESP32", 4 << 20, ""), "ESP32_GENERIC-20260824-v1.29.0.bin")
        self.assertEqual(mp.build_for("ESP32", 4 << 20).parts[0].offset, 0x1000)
        self.assertEqual(app(mp, "ESP8266", 1 << 20, ""), "ESP8266_GENERIC-FLASH_1M-20260824-v1.29.0.bin")
        self.assertEqual(app(mp, "ESP8266", 4 << 20, ""), "ESP8266_GENERIC-20260824-v1.29.0.bin")

    def test_option_parsing_strips_html(self):
        option = catalog.parse_option({"board": "x", "name": "X", "usb": ["303a:1001"],
                                       "note": "Pick the <b>second</b> port &amp; back up"},
                                      "https://h/firmware/{board}/manifest.json")
        self.assertEqual(option.manifest_url, "https://h/firmware/x/manifest.json")
        self.assertEqual(option.note, "Pick the second port & back up")

    def test_tasmota_style_index(self):
        source = catalog.Source("t", {"en": "T"}, {}, "", index_url="https://x/manifests.json", index_key="release")
        index = {"release": [
            {"path": "https://x/manifest_ext/release.tasmota.manifest.json", "name": "Tasmota",
             "chipFamilies": ["ESP32", "ESP32-S3", "ESP32-P4", "ESP32-P4", "RP2040"]},
            {"path": "https://x/manifest_ext/release.tasmota-ir.manifest.json", "name": "Tasmota IR",
             "chipFamilies": ["ESP32", "ESP8266"]},
            {"name": "broken"}]}
        original = catalog._get_json
        catalog._get_json = lambda url: index
        try:
            options = catalog.list_options(source)
        finally:
            catalog._get_json = original
        self.assertEqual([o.name for o in options], ["Tasmota", "Tasmota IR"])
        self.assertEqual(options[0].chips, ["ESP32", "ESP32-S3", "ESP32-P4"])
        self.assertEqual([o.name for o in catalog.match_options(options, "ESP8266", 4 << 20)], ["Tasmota IR"])

    def test_source_fits_only_once_loaded(self):
        source = catalog.Source("s", {"en": "S"}, {}, "")
        self.assertTrue(source.fits("ESP32-C6", 4 << 20))     # not loaded: maybe
        source.loaded = True
        source.options = [catalog.Option("a", "A", chips=["ESP32"])]
        self.assertFalse(source.fits("ESP32-C6", 4 << 20))
        source.options.append(catalog.Option("b", "B", "https://x/manifest.json"))
        self.assertTrue(source.fits("ESP32-C6", 4 << 20))     # an unread option: maybe

    def test_port_preference(self):
        ports = [devices.Port("COM1", "Communications Port", None, None),
                 devices.Port("COM3", "USB", 0x303A, 0x1001),
                 devices.Port("COM4", "USB-SERIAL CH340K", 0x1A86, 0x7522)]
        self.assertEqual(ports[2].bridge, "CH340K")
        self.assertFalse(ports[0].likely_esp)
        self.assertEqual(devices.pick_default(ports, ["1a86:7522"]), "COM4")
        self.assertEqual(devices.pick_default(ports), "COM3")
        self.assertEqual(devices.pick_default(ports, current="COM1"), "COM1")
        self.assertEqual(devices.pick_default([]), "")

    def test_board_matching(self):
        def option(id, chips, min_flash=0, usb=()):
            return catalog.Option(id, id.upper(), usb=list(usb), chips=list(chips), min_flash=min_flash)

        options = [option("box3", ["ESP32-S3"], 8 << 20, ["303a:1001"]),
                   option("watcher", ["ESP32-S3"], 8 << 20, ["1a86:55d2"]),
                   option("devkit-n8", ["ESP32-S3"], 12 << 20),
                   option("c5", ["ESP32-C5"]),
                   option("unread", [])]
        ids = lambda found: [o.id for o in found]
        self.assertEqual(ids(catalog.match_options(options, "ESP32-S3", 16 << 20)), ["box3", "watcher", "devkit-n8"])
        self.assertEqual(ids(catalog.match_options(options, "ESP32-S3", 8 << 20)), ["box3", "watcher"])
        self.assertEqual(ids(catalog.match_options(options, "ESP32-S3", 16 << 20, "1A86:55D2")), ["watcher"])
        # A USB id no option lists narrows nothing.
        self.assertEqual(len(catalog.match_options(options, "ESP32-S3", 16 << 20, "1a86:7523")), 3)
        self.assertEqual(ids(catalog.match_options(options, "ESP32-C5", 4 << 20)), ["c5"])
        self.assertEqual(catalog.match_options(options, "ESP32-C3", 4 << 20), [])


class BackupTests(unittest.TestCase):
    def test_meta_round_trip_and_finding_by_mac(self):
        from datetime import datetime
        from flasher import backups
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = flash_image(0x8000, [(0, 0x10000, app_header(b"handheld", b"e82e8eb"))])
            name = backups.file_name("ESP32-S3", "30:ED:A0:A6:B5:80", datetime(2026, 10, 17, 9, 5))
            self.assertEqual(name, "esp32-s3-30eda0a6b580-20261017-090500.bin")
            path = folder / name
            path.write_bytes(image)
            made = backups.finish(path, "ESP32-S3", len(image), "30:ed:a0:a6:b5:80", "box3")
            self.assertEqual(made.firmware, "handheld e82e8eb (IDF v5.2.3)")
            read = backups.read_meta(path)
            self.assertEqual((read.chip, read.mac, read.board, read.flash_bytes),
                             ("ESP32-S3", "30:ed:a0:a6:b5:80", "box3", len(image)))
            self.assertTrue(read.complete)
            self.assertEqual(backups.find_for_mac(folder, "30:ED:A0:A6:B5:80").path, path)
            self.assertIsNone(backups.find_for_mac(folder, "aa:bb:cc:dd:ee:ff"))
            # A partial copy isn't a backup to rely on.
            (folder / "partial.bin").write_bytes(image[:100])
            backups.write_meta(backups.Backup(folder / "partial.bin", "ESP32-S3", len(image), "aa:bb:cc:dd:ee:ff"))
            self.assertIsNone(backups.find_for_mac(folder, "aa:bb:cc:dd:ee:ff"))

    def test_a_bin_from_another_tool(self):
        from flasher import backups
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "factory.bin"
            path.write_bytes(flash_image(0x8000, [(0, 0x10000, app_header(b"handheld", b"1"))]))
            backup = backups.read_meta(path)
            self.assertEqual((backup.chip, backup.mac, backup.complete), ("", "", False))
            self.assertTrue(backup.firmware.startswith("handheld 1"))
            self.assertEqual(len(backups.list_backups(Path(tmp))), 1)


class UpdaterTests(unittest.TestCase):
    RELEASE = {
        "tag_name": "v0.4.0", "html_url": "https://github.com/rw0104/Flash32/releases/tag/v0.4.0",
        "body": "notes", "assets": [
            {"name": "other.zip", "browser_download_url": "https://x/other.zip", "digest": "sha256:" + "1" * 64},
            {"name": "Flash32-win64.zip", "browser_download_url": "https://x/Flash32-win64.zip", "size": 3,
             "digest": "sha256:" + "A" * 64}]}

    def test_versions(self):
        from flasher import updater
        self.assertEqual(updater.parse_version("v0.10.2"), (0, 10, 2))
        self.assertTrue(updater.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(updater.is_newer("v1.0", "0.9"))
        self.assertFalse(updater.is_newer("0.3.0", "0.3.0"))
        self.assertFalse(updater.is_newer("garbage", "0.3.0"))

    def test_release_and_its_checksum(self):
        from flasher import updater
        release = updater.parse_release(self.RELEASE)
        self.assertEqual((release.version, release.asset_url, release.sha256),
                         ("0.4.0", "https://x/Flash32-win64.zip", "a" * 64))
        # Older releases only state it in the notes.
        old = dict(self.RELEASE, body="SHA-256  Flash32-win64.zip  " + "B" * 64,
                   assets=[{"name": "Flash32-win64.zip", "browser_download_url": "https://x/z"}])
        self.assertEqual(updater.parse_release(old).sha256, "b" * 64)
        self.assertEqual(updater.parse_release(dict(old, body="")).sha256, "")

    def test_download_is_verified(self):
        import hashlib
        from flasher import updater
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.zip"
            source.write_bytes(b"zip")
            good = updater.Release("0.4.0", "v0.4.0", "", "", source.as_uri(), 3, hashlib.sha256(b"zip").hexdigest())
            self.assertEqual(updater.download(good, Path(tmp) / "a").read_bytes(), b"zip")
            bad = updater.Release("0.4.0", "v0.4.0", "", "", source.as_uri(), 3, "0" * 64)
            with self.assertRaisesRegex(updater.UpdateError, "err_update_checksum"):
                updater.download(bad, Path(tmp) / "b")
            self.assertFalse((Path(tmp) / "b" / updater.ASSET).exists())
            unchecked = updater.Release("0.4.0", "v0.4.0", "", "", source.as_uri(), 3, "")
            with self.assertRaisesRegex(updater.UpdateError, "err_update_checksum"):
                updater.download(unchecked, Path(tmp) / "c")

    def test_unpack_finds_the_app(self):
        import zipfile
        from flasher import updater
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("Flash32/Flash32.exe", "exe")
                zf.writestr("Flash32/_internal/base_library.zip", "lib")
            self.assertEqual(updater.unpack(archive, Path(tmp) / "out"), Path(tmp) / "out" / "Flash32")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("Flash32/readme.txt", "no app")
            with self.assertRaisesRegex(updater.UpdateError, "err_update_bad_zip"):
                updater.unpack(archive, Path(tmp) / "out2")

    @unittest.skipUnless(sys.platform == "win32", "the swap script is PowerShell")
    def test_swap_script_replaces_only_the_app_and_rolls_back(self):
        import shutil
        import subprocess
        from flasher import updater
        exe = Path(shutil.which("hostname.exe") or r"C:\Windows\System32\hostname.exe")

        def run(new_has_internal: bool) -> Path:
            tmp = Path(tempfile.mkdtemp())
            target, new, work = tmp / "Desk top's", tmp / "new", tmp / "work"
            for folder, tag in ((target, b"old"), (new, b"new")):
                (folder / "_internal").mkdir(parents=True)
                shutil.copy(exe, folder / "Flash32.exe")
                (folder / "_internal" / "lib.bin").write_bytes(tag)
            (target / "_internal" / "stale.bin").write_bytes(b"old only")
            (target / "notes.txt").write_text("the user's file")
            if not new_has_internal:
                shutil.rmtree(new / "_internal")
            work.mkdir()
            script = work / "install.ps1"
            script.write_text(updater.installer_script(new, target, "Flash32.exe", 999999, work), encoding="utf-8-sig")
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                           capture_output=True, timeout=120)
            return target

        target = run(True)
        self.assertEqual((target / "_internal" / "lib.bin").read_bytes(), b"new")
        self.assertFalse((target / "_internal" / "stale.bin").exists())
        self.assertEqual((target / "notes.txt").read_text(), "the user's file")
        self.assertTrue((target / "Flash32.exe").is_file())

        target = run(False)       # the copy fails: the old version comes back
        self.assertEqual((target / "_internal" / "lib.bin").read_bytes(), b"old")
        self.assertTrue((target / "_internal" / "stale.bin").exists())


class I18nTests(unittest.TestCase):
    def test_every_key_in_both_languages(self):
        zh, en = set(i18n.STRINGS["zh"]), set(i18n.STRINGS["en"])
        self.assertEqual(zh - en, set())
        self.assertEqual(en - zh, set())

    def test_error_codes_are_translated(self):
        import re
        codes = set()
        for module in ("manifest.py", "provision.py", "workers.py", "esptool_jobs.py", "catalog.py", "updater.py"):
            source = (Path(__file__).resolve().parent.parent / "flasher" / module).read_text(encoding="utf-8")
            codes |= set(re.findall(r'"((?:err|msg|stage|manifest|bin|part|download)_[a-z_]+)"', source))
        for field in provision.PROFILES["muse"]:
            codes |= {field.label, field.hint}
        codes.discard("manifest_url")       # a catalog field, not a message
        missing = sorted(c for c in codes if c not in i18n.STRINGS["en"])
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
