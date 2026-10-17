"""Setting up a freshly flashed device over serial: tokens and keys.

A firmware's setup profile lists fields, each sent as the console line
">command=value", which the device answers with "@command ok", "@command
cleared" or "@command error <why>". The Muse SDK takes its SDK token this
way (esp32/main/sdk_token_console.h); its Qwen TTS fork adds tts_key/tts_voice.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    command: str
    label: str            # i18n key
    hint: str             # i18n key
    pattern: str
    secret: bool = True
    required: bool = False
    restarts: bool = False   # the device reboots after accepting it
    link: str = ""

    def valid(self, value: str) -> bool:
        return bool(re.fullmatch(self.pattern, value))


PROFILES: dict[str, list[Field]] = {
    "muse": [
        # Restarting fields go last, so the others land before the reboot.
        Field("tts_key", "field_tts_key", "hint_tts_key", r"sk-[A-Za-z0-9._-]{8,250}",
              link="https://bailian.console.aliyun.com/"),
        Field("tts_voice", "field_tts_voice", "hint_tts_voice", r"[A-Za-z0-9._-]{1,31}", secret=False),
        Field("sdk_token", "field_sdk_token", "hint_sdk_token", r"mgst_[A-Za-z0-9_-]{16,}",
              required=True, restarts=True, link="https://gadgets.muse.ai/settings/sdk-tokens"),
    ],
}

SECRET_PATTERNS = (re.compile(r"mgst_[A-Za-z0-9_-]+"), re.compile(r"sk-[A-Za-z0-9._-]{4,}"))


def redact(text: str) -> str:
    """Hides tokens and keys in anything that reaches the log."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(0)[:5] + "…", text)
    return text


class ProvisionError(Exception):
    """str() is an i18n key; .detail is extra text for the log."""

    def __init__(self, key: str, detail: str = ""):
        super().__init__(key)
        self.detail = detail


def send_setting(conn, command: str, value: str, timeout: float = 15.0, cancelled=lambda: False) -> str:
    """Sends one setting and waits for the device's answer: 'ok' or 'cleared'.
    `conn` is an open pyserial-like object (read, write, in_waiting)."""
    payload = f"\n>{command}={value}\n".encode("ascii")
    if conn.write(payload) != len(payload):
        raise ProvisionError("err_serial_write")
    prefix = f"@{command} ".encode()
    deadline = time.monotonic() + timeout
    buffer = b""
    while time.monotonic() < deadline:
        if cancelled():
            raise InterruptedError
        buffer += conn.read(max(1, min(getattr(conn, "in_waiting", 0) or 1, 4096)))
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(prefix):
                continue
            answer = line[len(prefix):].decode("ascii", "replace").strip()
            if answer in ("ok", "cleared"):
                return answer
            if answer.startswith("error"):
                raise ProvisionError("err_device_rejected", answer[5:].strip())
        buffer = buffer[-8192:]
    raise ProvisionError("err_no_answer")


def open_serial(port: str):
    """Opens the console without toggling DTR/RTS, which would reset the board."""
    import serial
    conn = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=2)
    conn.dtr = False
    conn.rts = False
    conn.port = port
    conn.open()
    return conn
