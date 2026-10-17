"""Background jobs. Each runs on its own thread, reports through signals, and
can be stopped; none touches a device until started."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import catalog, esptool_jobs, manifest, provision


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
                    if "%" not in line or "100" in line:
                        self.emit_log(line)
        if pending.strip():
            text.append(pending)
            self.emit_log(pending)
        code = self._proc.wait()
        self._proc = None
        if self.stopped:
            raise InterruptedError
        return code, "\n".join(text)


def explain_esptool_failure(output: str) -> str:
    if any(h in output for h in esptool_jobs.PORT_BUSY_HINTS):
        return "err_port_busy"
    if any(h in output for h in esptool_jobs.BOOTLOADER_HINTS):
        return "err_no_bootloader"
    return "err_esptool"


class BoardsJob(Job):
    def __init__(self, source: catalog.Source, parent=None):
        super().__init__(parent)
        self.source = source

    def work(self):
        self.stage.emit("stage_loading_boards")
        return True, "msg_boards_loaded", "", catalog.fetch_boards(self.source)


class ManifestJob(Job):
    def __init__(self, location: str, parent=None):
        super().__init__(parent)
        self.location = location

    def work(self):
        self.stage.emit("stage_loading_manifest")
        self.emit_log(self.location)
        return True, "msg_manifest_loaded", "", manifest.load_manifest(self.location)


class DetectJob(Job):
    def __init__(self, port: str, parent=None):
        super().__init__(parent)
        self.port = port

    def work(self):
        self.stage.emit("stage_detecting")
        self.progress.emit(-1)
        code, output = self.run_esptool(esptool_jobs.detect_args(self.port))
        info = esptool_jobs.parse_chip(output)
        if code != 0 or not info.family:
            return False, explain_esptool_failure(output), "", None
        return True, "msg_detected", "", info


class FlashJob(Job):
    """Detect the chip, check the firmware fits it, fetch the images, write them."""

    def __init__(self, fw: manifest.Manifest, port: str, baud: int, erase_all: bool, parent=None):
        super().__init__(parent)
        self.fw, self.port, self.baud, self.erase_all = fw, port, baud, erase_all

    def work(self):
        self.stage.emit("stage_detecting")
        self.progress.emit(-1)
        code, output = self.run_esptool(esptool_jobs.detect_args(self.port))
        info = esptool_jobs.parse_chip(output)
        if code != 0 or not info.family:
            return False, explain_esptool_failure(output), "", None
        build = self.fw.build_for(info.family)
        if not build:
            return False, "err_wrong_chip", f"{info.family} ≠ {', '.join(self.fw.chip_families)}", info
        if self.fw.min_flash_bytes and info.flash_bytes and info.flash_bytes < self.fw.min_flash_bytes:
            need = self.fw.min_flash_bytes / (1 << 20)
            return False, "err_flash_too_small", f"{info.flash_bytes >> 20} MB < {need:.1f} MB", info

        self.stage.emit("stage_downloading")
        cache = Path(tempfile.gettempdir()) / "flash32-cache"
        files = []
        for i, part in enumerate(build.parts):
            def report(done, total, i=i):
                if total:
                    self.progress.emit((i + done / total) / len(build.parts))
            files.append((part.offset, str(manifest.fetch_part(part, cache, report, lambda: self.stopped))))
            self.emit_log(f"{hex(part.offset)}  {Path(files[-1][1]).name}")

        self.stage.emit("stage_flashing")
        self.progress.emit(0.0)
        bar = esptool_jobs.FlashProgress({off: Path(f).stat().st_size for off, f in files})

        def on_line(line):
            value = bar.feed(line)
            if value is not None:
                self.progress.emit(value)

        args = esptool_jobs.flash_args(self.port, self.baud, build.esptool_chip, files, self.erase_all)
        code, output = self.run_esptool(args, on_line)
        if code != 0:
            return False, explain_esptool_failure(output), "", info
        self.progress.emit(1.0)
        return True, "msg_flashed", "", info


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
