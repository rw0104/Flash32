"""Serial ports, and which of them look like an ESP32 board."""

from __future__ import annotations

from dataclasses import dataclass

# USB devices ESP32 boards commonly expose their console through.
KNOWN_USB = {
    (0x303A, None): "Espressif USB",
    (0x1A86, 0x7522): "CH340K",
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x55D2): "CH342",
    (0x1A86, 0x55D3): "CH343",
    (0x1A86, 0x55D4): "CH9102",
    (0x1A86, None): "WCH",
    (0x10C4, 0xEA60): "CP210x",
    (0x0403, None): "FTDI",
}


@dataclass
class Port:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial: str = ""

    @property
    def usb_id(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}" if self.vid is not None and self.pid is not None else ""

    @property
    def bridge(self) -> str:
        """The USB chip's name if it's one ESP32 boards use, else ''."""
        if self.vid is None:
            return ""
        return KNOWN_USB.get((self.vid, self.pid)) or KNOWN_USB.get((self.vid, None), "")

    @property
    def likely_esp(self) -> bool:
        return bool(self.bridge)

    def label(self) -> str:
        if self.bridge:
            return f"{self.device} · {self.bridge}"
        return f"{self.device} · {self.description}" if self.description else self.device


def list_ports() -> list[Port]:
    from serial.tools import list_ports as lp
    ports = [Port(p.device, p.description or "", p.vid, p.pid, p.serial_number or "") for p in lp.comports()]
    # Likely boards first, then by name (COM3 before COM10).
    return sorted(ports, key=lambda p: (not p.likely_esp, len(p.device), p.device))


def pick_default(ports: list[Port], usb_ids: list[str] | None = None, current: str = "") -> str:
    """The port to preselect: the current one if still there, then one matching
    the firmware's USB ids, then the first likely board."""
    if current and any(p.device == current for p in ports):
        return current
    wanted = {u.lower() for u in usb_ids or []}
    for port in ports:
        if wanted and port.usb_id in wanted:
            return port.device
    for port in ports:
        if port.likely_esp:
            return port.device
    return ports[0].device if ports else ""
