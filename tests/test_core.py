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
