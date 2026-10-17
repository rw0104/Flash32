"""Background jobs. Each runs on its own thread, reports through signals, and
can be stopped; none touches a device until started."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import backups, catalog, esptool_jobs, manifest, provision


class JobFailed(Exception):
    """Ends a job with an i18n key, a detail, and what it found so far."""

    def __init__(self, key: str, detail: str = "", result=None):
        super().__init__(key)
        self.key, self.detail, self.result = key, detail, result


class Job(QThread):
    log = Signal(str)                    # a line for the log (already redacted)
    progress = Signal(float)             # 0..1, or -1 for "busy, no estimate"
    stage = Signal(str)                  # i18n key of what's happening now
    done = Signal(bool, str, str, object)  # ok, i18n key, detail, result

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.kill()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def emit_log(self, text: str) -> None:
        self.log.emit(provision.redact(text))

    def run(self) -> None:
        try:
            ok, key, detail, result = self.work()
        except InterruptedError:
            ok, key, detail, result = False, "msg_stopped", "", None
        except JobFailed as exc:
            ok, key, detail, result = False, exc.key, exc.detail, exc.result
        except (manifest.ManifestError, provision.ProvisionError) as exc:
            ok, key, detail, result = False, str(exc), getattr(exc, "detail", ""), None
        except Exception as exc:     # network, OS, serial: show it, don't crash
            ok, key, detail, result = False, "err_unexpected", f"{type(exc).__name__}: {exc}", None
        if self.stopped and not ok:
            key, detail = "msg_stopped", ""
        self.done.emit(ok, key, provision.redact(detail), result)

    def work(self):
        raise NotImplementedError

    def run_esptool(self, args: list[str], on_line=None) -> tuple[int, str]:
        """Runs esptool, streaming its lines (progress bars end in \\r)."""
        command = esptool_jobs.esptool_command(args)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", NO_COLOR="1")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.emit_log("$ esptool " + " ".join(args))
        self._proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, env=env, creationflags=flags)
        text, pending = [], ""
        assert self._proc.stdout
        while True:
            chunk = self._proc.stdout.read1(4096) if hasattr(self._proc.stdout, "read1") else self._proc.stdout.read(1)
            if not chunk:
                break
            pending += chunk.decode("utf-8", "replace")
            while True:
                cut = min((i for i in (pending.find("\n"), pending.find("\r")) if i >= 0), default=-1)
                if cut < 0:
                    break
                line, pending = pending[:cut], pending[cut + 1:]
                if line.strip():
                    text.append(line)
                    if on_line:
                        on_line(line)
                    # Progress lines repeat many times; the bar shows them.
                    if "%" not in line or "100.0%" in line:
                        self.emit_log(line)
        if pending.strip():
            text.append(pending)
            self.emit_log(pending)
        code = self._proc.wait()
        self._proc = None
        if self.stopped:
            raise InterruptedError
        return code, "\n".join(text)


    # ---- steps shared by the device jobs ----------------------------------

    def detect_chip(self, port: str, after: str = "hard-reset") -> esptool_jobs.ChipInfo:
        code, output = self.run_esptool(esptool_jobs.detect_args(port, after=after))
        info = esptool_jobs.parse_chip(output)
        if code != 0 or not info.family:
            raise JobFailed(explain_esptool_failure(output))
        return info

    def reset_board(self, port: str, info: esptool_jobs.ChipInfo) -> None:
        """Restarts the board into its app after steps that left it in the bootloader."""
        if not self.stopped:
            self.run_esptool(esptool_jobs.reset_args(port, manifest.CHIP_FAMILIES.get(info.family, "")))

    def read_flash(self, port: str, baud: int, info: esptool_jobs.ChipInfo, offset: int, size: int,
                   out: Path, after: str, show_progress: bool = False) -> None:
        def on_line(line):
            value = esptool_jobs.read_progress(line)
            if value is not None:
                self.progress.emit(value)

        chip = manifest.CHIP_FAMILIES.get(info.family, "")
        args = esptool_jobs.read_args(port, baud, chip, offset, size, str(out), after)
        code, output = self.run_esptool(args, on_line if show_progress else None)
        if code != 0 or not out.is_file():
            raise JobFailed(explain_esptool_failure(output), "", info)

    def read_firmware(self, port: str, info: esptool_jobs.ChipInfo) -> manifest.AppInfo | None:
        """What app the board holds now, from its partition table and the app's
        header. Leaves the board in its bootloader."""
        if info.family == "ESP8266":
            return None
        folder = Path(tempfile.mkdtemp(prefix="flash32-"))
        try:
            base = min(manifest.PARTITION_TABLE_OFFSETS)
            span = max(manifest.PARTITION_TABLE_OFFSETS) + manifest.PARTITION_TABLE_SIZE - base
            self.read_flash(port, 115200, info, base, span, folder / "table.bin", "no-reset")
            blob = (folder / "table.bin").read_bytes()
            app = manifest.first_app(manifest.find_partition_table(blob, base))
            if not app:
                return None
            start = app.offset - base
            if start + manifest.APP_HEADER_SIZE <= len(blob):
                return manifest.app_description(blob[start:start + manifest.APP_HEADER_SIZE])
            self.read_flash(port, 115200, info, app.offset, manifest.APP_HEADER_SIZE, folder / "app.bin", "no-reset")
            return manifest.app_description((folder / "app.bin").read_bytes())
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def backup_flash(self, port: str, baud: int, info: esptool_jobs.ChipInfo, folder: Path,
                     board: str, after: str) -> backups.Backup:
        """Copies the whole flash into `folder`."""
        if not info.flash_bytes:
            raise JobFailed("err_flash_size_unknown", "", info)
        self.stage.emit("stage_backing_up")
        self.progress.emit(0.0)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / backups.file_name(info.family, info.mac, datetime.now())
        temp = path.with_name(path.name + ".part")
        try:
            self.read_flash(port, baud, info, 0, info.flash_bytes, temp, after, show_progress=True)
            if temp.stat().st_size != info.flash_bytes:
                raise JobFailed("err_backup_incomplete", f"{temp.stat().st_size} / {info.flash_bytes}", info)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        backup = backups.finish(path, info.family, info.flash_bytes, info.mac, board)
        self.emit_log(f"backup → {path}")
        return backup


def explain_esptool_failure(output: str) -> str:
    if any(h in output for h in esptool_jobs.PORT_BUSY_HINTS):
        return "err_port_busy"
    if any(h in output for h in esptool_jobs.BOOTLOADER_HINTS):
        return "err_no_bootloader"
    return "err_esptool"


class CatalogJob(Job):
    """Lists every source's options, then learns which chips each suits."""
    listed = Signal()          # options listed; chips may still be coming

    def __init__(self, sources: list[catalog.Source], parent=None):
        super().__init__(parent)
        self.sources = sources

    def work(self):
        self.stage.emit("stage_loading_boards")
        catalog.load_sources(self.sources, self.listed.emit, lambda: self.stopped)
        return True, "msg_boards_loaded", "", self.sources


class ManifestJob(Job):
    """A manifest from a URL or file, or a catalog option's (source, option)."""

    def __init__(self, location, parent=None):
        super().__init__(parent)
        self.location = location

    def work(self):
        self.stage.emit("stage_loading_manifest")
        if isinstance(self.location, tuple):
            source, option = self.location
            self.emit_log(option.manifest_url or f"{source.id}: {option.name}")
            return True, "msg_manifest_loaded", "", catalog.load_option(source, option)
        self.emit_log(self.location)
        return True, "msg_manifest_loaded", "", manifest.load_manifest(self.location)


class DetectJob(Job):
    """The chip, its flash and MAC, and which firmware it runs now."""

    def __init__(self, port: str, parent=None):
        super().__init__(parent)
        self.port = port

    def work(self):
        self.stage.emit("stage_detecting")
        self.progress.emit(-1)
        info = self.detect_chip(self.port, after="no-reset")
        try:
            info.firmware = self.read_firmware(self.port, info)
        except JobFailed:
            pass        # an unreadable table only means we can't name the firmware
        finally:
            self.reset_board(self.port, info)
        return True, "msg_detected", "", info


class FlashJob(Job):
    """Detect the chip, check the firmware fits it, back the board up the first
    time it's seen (if asked), fetch the images, write them."""

    def __init__(self, fw: manifest.Manifest | None, port: str, baud: int, erase_all: bool,
                 backup_folder: Path | None = None, board: str = "", parent=None):
        super().__init__(parent)
        self.fw, self.port, self.baud, self.erase_all = fw, port, baud, erase_all
        self.backup_folder, self.board = backup_folder, board
        self.backup_made: backups.Backup | None = None

    def choose_build(self, info: esptool_jobs.ChipInfo) -> manifest.Build:
        if info.family not in self.fw.chip_families:
            raise JobFailed("err_wrong_chip", f"{info.family} ≠ {', '.join(self.fw.chip_families)}", info)
        build = self.fw.build_for(info.family, info.flash_bytes, info.psram)
        if not build:
            least = min(b.flash_bytes for b in self.fw.builds if b.chip_family == info.family)
            raise JobFailed("err_flash_too_small", f"{info.flash_bytes >> 20} MB < {least / (1 << 20):g} MB", info)
        variant = " ".join(t for t in (f"{build.flash_bytes >> 20} MB" if build.flash_bytes >= 1 << 20 else "",
                                       build.psram) if t)
        if variant:
            self.emit_log(f"{info.family} build: {variant}")
        if self.fw.min_flash_bytes and info.flash_bytes and info.flash_bytes < self.fw.min_flash_bytes:
            need = self.fw.min_flash_bytes / (1 << 20)
            raise JobFailed("err_flash_too_small", f"{info.flash_bytes >> 20} MB < {need:.1f} MB", info)
        return build

    def work(self):
        self.stage.emit("stage_detecting")
        self.progress.emit(-1)
        # The board stays in its bootloader until the write resets it.
        info = self.detect_chip(self.port, after="no-reset")
        try:
            build = self.choose_build(info)
            if self.backup_folder and not backups.find_for_mac(self.backup_folder, info.mac):
                self.backup_made = self.backup_flash(self.port, self.baud, info, self.backup_folder,
                                                     self.board, "no-reset")
            files = self.fetch(build)
        except JobFailed as exc:
            exc.result = info
            self.reset_board(self.port, info)
            raise

        self.stage.emit("stage_flashing")
        self.progress.emit(0.0)
        bar = esptool_jobs.FlashProgress({off: Path(f).stat().st_size for off, f in files})

        def on_line(line):
            value = bar.feed(line)
            if value is not None:
                self.progress.emit(value)

        args = esptool_jobs.flash_args(self.port, self.baud, build.esptool_chip, files, self.erase_all,
                                       build.detect_flash_size)
        code, output = self.run_esptool(args, on_line)
        if code != 0:
            return False, explain_esptool_failure(output), "", info
        self.progress.emit(1.0)
        return True, "msg_flashed", "", info

    def fetch(self, build: manifest.Build) -> list[tuple[int, str]]:
        self.stage.emit("stage_downloading")
        self.progress.emit(0.0)
        cache = Path(tempfile.gettempdir()) / "flash32-cache"
        files = []
        for i, part in enumerate(build.parts):
            def report(done, total, i=i):
                if total:
                    self.progress.emit((i + done / total) / len(build.parts))
            files.append((part.offset, str(manifest.fetch_part(part, cache, report, lambda: self.stopped))))
            self.emit_log(f"{hex(part.offset)}  {Path(files[-1][1]).name}")
        return files


class BackupJob(Job):
    """Copies the board's whole flash to a file."""

    def __init__(self, port: str, baud: int, folder: Path, board: str = "", parent=None):
        super().__init__(parent)
        self.port, self.baud, self.folder, self.board = port, baud, folder, board

    def work(self):
        self.stage.emit("stage_detecting")
        self.progress.emit(-1)
        info = self.detect_chip(self.port, after="no-reset")
        try:
            backup = self.backup_flash(self.port, self.baud, info, self.folder, self.board, "hard-reset")
        except JobFailed as exc:
            self.reset_board(self.port, info)
            exc.result = None
            raise
        self.progress.emit(1.0)
        return True, "msg_backup_done", str(backup.path), backup


class RestoreJob(FlashJob):
    """Writes a backup back from 0x0, after checking it suits the board."""

    def __init__(self, backup: backups.Backup, port: str, baud: int, parent=None):
        super().__init__(None, port, baud, False, parent=parent)
        self.backup = backup

    def choose_build(self, info: esptool_jobs.ChipInfo) -> manifest.Build:
        backup = self.backup
        if backup.chip and backup.chip != info.family:
            raise JobFailed("err_wrong_chip", f"{backup.chip} ≠ {info.family}", info)
        if not backup.size:
            raise JobFailed("bin_not_found", str(backup.path), info)
        if info.flash_bytes and backup.size > info.flash_bytes:
            raise JobFailed("err_flash_too_small", f"{info.flash_bytes >> 20} MB < {backup.size / (1 << 20):.1f} MB", info)
        if backup.mac and info.mac and backup.mac.lower() != info.mac:
            self.emit_log(f"! backup of {backup.mac}, board is {info.mac}")
        return manifest.Build(info.family, [manifest.Part(str(backup.path.resolve()), 0)])


class SetupJob(Job):
    """Sends setup values (field, value) in order over the console."""

    def __init__(self, port: str, values: list[tuple[provision.Field, str]], parent=None):
        super().__init__(parent)
        self.port, self.values = port, values

    def work(self):
        self.stage.emit("stage_setup")
        self.progress.emit(-1)
        conn = provision.open_serial(self.port)
        results = []
        try:
            for field, value in self.values:
                if self.stopped:
                    raise InterruptedError
                self.emit_log(f"> {field.command}=" + ("…" if field.secret and value else value))
                answer = provision.send_setting(conn, field.command, value, cancelled=lambda: self.stopped)
                self.emit_log(f"@{field.command} {answer}")
                results.append((field.command, answer))
        finally:
            conn.close()
        return True, "msg_setup_done", "", results


class MonitorJob(Job):
    """Shows the device's console until stopped."""

    def __init__(self, port: str, parent=None):
        super().__init__(parent)
        self.port = port

    def work(self):
        self.stage.emit("stage_monitor")
        self.progress.emit(-1)
        conn = provision.open_serial(self.port)
        pending = b""
        try:
            while not self.stopped:
                pending += conn.read(max(1, min(conn.in_waiting or 1, 4096)))
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    text = line.decode("utf-8", "replace").rstrip("\r")
                    # Drop ANSI colour codes from ESP-IDF logs.
                    self.emit_log(_strip_ansi(text))
                pending = pending[-65536:]
        finally:
            conn.close()
        return True, "msg_monitor_closed", "", None


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)
