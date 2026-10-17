import json
import tempfile
import unittest
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
        muse = next(s for s in sources if s.id == "muse")
        self.assertEqual(muse.setup, "muse")
        self.assertIn("{board}", muse.manifest_url)
        self.assertTrue(muse.text("name", "zh"))
        self.assertIn(muse.setup, provision.PROFILES)

    def test_board_parsing_strips_html(self):
        board = catalog.parse_board({"board": "x", "name": "X", "usb": ["303a:1001"],
                                     "note": "Pick the <b>second</b> port &amp; back up"},
                                    "https://h/firmware/{board}/manifest.json")
        self.assertEqual(board.manifest_url, "https://h/firmware/x/manifest.json")
        self.assertEqual(board.note, "Pick the second port & back up")

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
        def board(id, chips, min_flash=0, usb=()):
            return catalog.Board(id, id.upper(), "", list(usb), "", list(chips), min_flash)

        boards = [board("box3", ["ESP32-S3"], 8 << 20, ["303a:1001"]),
                  board("watcher", ["ESP32-S3"], 8 << 20, ["1a86:55d2"]),
                  board("devkit-n8", ["ESP32-S3"], 12 << 20),
                  board("c5", ["ESP32-C5"]),
                  board("unread", [])]
        ids = lambda found: [b.id for b in found]
        self.assertEqual(ids(catalog.match_boards(boards, "ESP32-S3", 16 << 20)), ["box3", "watcher", "devkit-n8"])
        self.assertEqual(ids(catalog.match_boards(boards, "ESP32-S3", 8 << 20)), ["box3", "watcher"])
        self.assertEqual(ids(catalog.match_boards(boards, "ESP32-S3", 16 << 20, "1A86:55D2")), ["watcher"])
        # A USB id no board lists narrows nothing.
        self.assertEqual(len(catalog.match_boards(boards, "ESP32-S3", 16 << 20, "1a86:7523")), 3)
        self.assertEqual(ids(catalog.match_boards(boards, "ESP32-C5", 4 << 20)), ["c5"])
        self.assertEqual(catalog.match_boards(boards, "ESP32-C3", 4 << 20), [])


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


class I18nTests(unittest.TestCase):
    def test_every_key_in_both_languages(self):
        zh, en = set(i18n.STRINGS["zh"]), set(i18n.STRINGS["en"])
        self.assertEqual(zh - en, set())
        self.assertEqual(en - zh, set())

    def test_error_codes_are_translated(self):
        import re
        codes = set()
        for module in ("manifest.py", "provision.py", "workers.py", "esptool_jobs.py"):
            source = (Path(__file__).resolve().parent.parent / "flasher" / module).read_text(encoding="utf-8")
            codes |= set(re.findall(r'"((?:err|msg|stage|manifest|bin|part|download)_[a-z_]+)"', source))
        for field in provision.PROFILES["muse"]:
            codes |= {field.label, field.hint}
        missing = sorted(c for c in codes if c not in i18n.STRINGS["en"])
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
