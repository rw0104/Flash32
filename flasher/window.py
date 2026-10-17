"""The main window: pick firmware, connect, flash, set up."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import __version__, catalog, devices, esptool_jobs, i18n, manifest, provision, workers
from .i18n import tr

CH340_DRIVER = "https://www.wch.cn/downloads/CH341SER_EXE.html"


def _label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _button(name: str = "", slot=None) -> QPushButton:
    button = QPushButton()
    if name:
        button.setObjectName(name)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot:
        button.clicked.connect(slot)
    return button


def _rule() -> QFrame:
    rule = QFrame()
    rule.setObjectName("Rule")
    return rule


class MainWindow(QMainWindow):
    def __init__(self, sources: list[catalog.Source]):
        super().__init__()
        self.settings = QSettings("flash32", "flash32")
        i18n.set_language(self.settings.value("language", i18n.system_language()))
        self.sources = sources
        self.boards: list[catalog.Board] = []
        self.firmware: manifest.Manifest | None = None
        self.profile = ""
        self.usb_ids: list[str] = []
        self.job: workers.Job | None = None        # flash, detect, setup, monitor: one at a time
        self.loader: workers.Job | None = None     # catalog and manifest loading
        self.detected: esptool_jobs.ChipInfo | None = None
        self._texts: list = []
        self._status = ("status_ready", "status_ready_detail", "idle", "")
        self._setup_inputs: list[tuple[provision.Field, QLineEdit]] = []
        self._closing = False

        self.setWindowTitle(tr("app_title"))
        icon = catalog.resource_dir() / "assets" / "app.ico"
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1180, 780)
        self.setMinimumSize(980, 660)

        root = QWidget()
        root.setObjectName("Workspace")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 20, 26, 14)
        outer.setSpacing(14)
        outer.addLayout(self._build_header())
        outer.addWidget(self._build_steps())
        body = QHBoxLayout()
        body.setSpacing(20)
        body.addWidget(self._build_left(), 0)
        body.addLayout(self._build_right(), 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(root)

        self._fill_sources()
        mode = self.settings.value("mode", "catalog")
        self._set_mode(mode if mode in ("catalog", "url", "local") else "catalog")
        self.refresh_ports()
        self._render_firmware()
        self._render_status()
        self._update_buttons()
        self.log_event(f"{tr('app_title')} {__version__}")

    # ---- small helpers ---------------------------------------------------

    def _t(self, setter, key: str, **values) -> None:
        """Sets a text now and again whenever the language changes."""
        setter(tr(key, **values))
        self._texts.append(lambda: setter(tr(key, **values)))

    def log_event(self, text: str) -> None:
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {provision.redact(text)}")

    def set_status(self, title: str, detail: str = "", kind: str = "idle", extra: str = "") -> None:
        self._status = (title, detail, kind, extra)
        self._render_status()

    def _render_status(self) -> None:
        title, detail, kind, extra = self._status
        self.status_title.setText(tr(title))
        text = tr(detail) if detail else ""
        if extra:
            text = f"{text}  {extra}" if text else extra
        self.status_detail.setText(provision.redact(text))
        self.status_dot.setProperty("kind", kind)
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    @property
    def busy(self) -> bool:
        return self.job is not None and self.job.isRunning()

    # ---- layout ------------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        mark = _label("F", "BrandMark")
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = _label(name="BrandTitle")
        self._t(title.setText, "app_title")
        self._t(self.setWindowTitle, "app_title")
        subtitle = _label(name="BrandSubtitle")
        self._t(subtitle.setText, "app_subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        row.addLayout(titles)
        row.addStretch(1)
        self.lang_combo = QComboBox()
        self.lang_combo.setObjectName("LangCombo")
        for code, name in i18n.LANGUAGES.items():
            self.lang_combo.addItem(name, code)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(i18n.language())))
        self.lang_combo.currentIndexChanged.connect(self._language_changed)
        row.addWidget(self.lang_combo)
        version = _label(f"v{__version__}", "VersionLabel")
        row.addWidget(version)
        return row

    def _build_steps(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Workflow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(20, 10, 20, 10)
        for i in range(1, 5):
            if i > 1:
                arrow = _label("⟶", "StepArrow")
                row.addWidget(arrow)
                row.addStretch(1)
            row.addWidget(_label(f"0{i}", "StepNumber"))
            texts = QVBoxLayout()
            texts.setSpacing(0)
            name, hint = _label(name="StepText"), _label(name="StepHint")
            self._t(name.setText, f"step{i}")
            self._t(hint.setText, f"step{i}_hint")
            texts.addWidget(name)
            texts.addWidget(hint)
            row.addLayout(texts)
            if i < 4:
                row.addStretch(1)
        return frame

    def _section(self, layout: QVBoxLayout, number: str, key: str) -> None:
        if layout.count():
            layout.addSpacing(6)
            layout.addWidget(_rule())
            layout.addSpacing(6)
        eyebrow = _label(number, "PanelEyebrow")
        title = _label(name="PanelTitle")
        self._t(title.setText, key)
        layout.addWidget(eyebrow)
        layout.addWidget(title)

    def _build_left(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("ConfigPanel")
        panel.setFixedWidth(420)
        scroll = QScrollArea()
        scroll.setObjectName("ConfigScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contents = QWidget()
        contents.setObjectName("ConfigContents")
        col = QVBoxLayout(contents)
        col.setContentsMargins(20, 18, 20, 18)
        col.setSpacing(7)

        self._section(col, "01 / FIRMWARE", "section_firmware")
        self._build_firmware(col)
        self._section(col, "02 / DEVICE", "section_device")
        self._build_device(col)
        self.setup_box = QWidget()
        self.setup_layout = QVBoxLayout(self.setup_box)
        self.setup_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_layout.setSpacing(7)
        col.addWidget(self.setup_box)
        col.addStretch(1)

        scroll.setWidget(contents)
        wrap = QVBoxLayout(panel)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(scroll)
        return panel

    def _field_label(self, key: str) -> QLabel:
        label = _label(name="FieldLabel")
        self._t(label.setText, key)
        return label

    def _hint(self, key: str, name: str = "FieldHint") -> QLabel:
        label = _label(name=name, wrap=True)
        self._t(label.setText, key)
        return label

    def _build_firmware(self, col: QVBoxLayout) -> None:
        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for i, (mode, key) in enumerate((("catalog", "tab_catalog"), ("url", "tab_url"), ("local", "tab_local"))):
            button = _button("SegButton")
            button.setCheckable(True)
            button.setProperty("pos", "first" if i == 0 else "last" if i == 2 else "mid")
            self._t(button.setText, key)
            button.clicked.connect(lambda _=False, m=mode: self._set_mode(m))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            seg.addWidget(button)
        seg.addStretch(1)
        col.addLayout(seg)

        self.mode_stack = QStackedWidget()
        self.mode_stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # Catalog
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._field_label("project"))
        self.source_combo = QComboBox()
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        lay.addWidget(self.source_combo)
        self.source_desc = _label(name="FieldHint", wrap=True)
        lay.addWidget(self.source_desc)
        self.homepage_btn = _button("LinkButton", self._open_homepage)
        self._t(self.homepage_btn.setText, "homepage")
        lay.addWidget(self.homepage_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._field_label("board"))
        row = QHBoxLayout()
        self.board_combo = QComboBox()
        self.board_combo.currentIndexChanged.connect(self._board_changed)
        row.addWidget(self.board_combo, 1)
        self.reload_btn = _button(slot=self._load_boards)
        self._t(self.reload_btn.setText, "reload")
        row.addWidget(self.reload_btn)
        lay.addLayout(row)
        self.board_note = _label(name="WarningHint", wrap=True)
        lay.addWidget(self.board_note)
        self.mode_stack.addWidget(page)

        # URL
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._field_label("url_label"))
        row = QHBoxLayout()
        self.url_edit = QLineEdit(self.settings.value("url", ""))
        self.url_edit.setObjectName("PathInput")
        self.url_edit.setPlaceholderText("https://…/manifest.json")
        self.url_edit.returnPressed.connect(self._load_url)
        row.addWidget(self.url_edit, 1)
        load = _button(slot=self._load_url)
        self._t(load.setText, "load")
        row.addWidget(load)
        lay.addLayout(row)
        lay.addWidget(self._hint("url_hint"))
        self.mode_stack.addWidget(page)

        # Local
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._field_label("local_label"))
        row = QHBoxLayout()
        self.local_edit = QLineEdit(self.settings.value("local", ""))
        self.local_edit.setObjectName("PathInput")
        self.local_edit.returnPressed.connect(self._load_local)
        row.addWidget(self.local_edit, 1)
        browse = _button(slot=self._browse_local)
        self._t(browse.setText, "browse")
        row.addWidget(browse)
        lay.addLayout(row)
        self.bin_box = QWidget()
        grid = QGridLayout(self.bin_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self._field_label("chip"), 0, 0)
        grid.addWidget(self._field_label("offset"), 0, 1)
        self.chip_combo = QComboBox()
        self.chip_combo.addItems(list(manifest.CHIP_FAMILIES))
        self.chip_combo.setCurrentText("ESP32-S3")
        grid.addWidget(self.chip_combo, 1, 0)
        self.offset_edit = QLineEdit("0x0")
        self.offset_edit.setObjectName("PathInput")
        grid.addWidget(self.offset_edit, 1, 1)
        use = _button(slot=self._use_bin)
        self._t(use.setText, "use_bin")
        grid.addWidget(use, 1, 2)
        grid.addWidget(self._hint("bin_hint"), 2, 0, 1, 3)
        lay.addWidget(self.bin_box)
        self.bin_box.setVisible(False)
        self.mode_stack.addWidget(page)
        col.addWidget(self.mode_stack)

        card = QFrame()
        card.setObjectName("BoardCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(2)
        self.fw_name = _label(name="FirmwareName", wrap=True)
        self.fw_meta = _label(name="FirmwareMeta", wrap=True)
        lay.addWidget(self.fw_name)
        lay.addWidget(self.fw_meta)
        col.addSpacing(4)
        col.addWidget(card)

    def _build_device(self, col: QVBoxLayout) -> None:
        col.addWidget(self._field_label("port"))
        row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.currentIndexChanged.connect(self._port_changed)
        row.addWidget(self.port_combo, 1)
        self.refresh_btn = _button(slot=self.refresh_ports)
        self._t(self.refresh_btn.setText, "refresh")
        row.addWidget(self.refresh_btn)
        self.detect_btn = _button(slot=self._detect)
        self._t(self.detect_btn.setText, "detect")
        row.addWidget(self.detect_btn)
        col.addLayout(row)
        self.no_ports = self._hint("no_ports", "WarningHint")
        col.addWidget(self.no_ports)
        self.driver_btn = _button("LinkButton", lambda: QDesktopServices.openUrl(QUrl(CH340_DRIVER)))
        self._t(self.driver_btn.setText, "driver_link")
        col.addWidget(self.driver_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.detected_label = _label(name="DetectedLabel", wrap=True)
        col.addWidget(self.detected_label)

        row = QHBoxLayout()
        row.addWidget(self._field_label("baud"))
        row.addStretch(1)
        self.baud_combo = QComboBox()
        for baud in esptool_jobs.BAUD_RATES:
            self.baud_combo.addItem(str(baud), baud)
        saved = int(self.settings.value("baud", esptool_jobs.DEFAULT_BAUD))
        self.baud_combo.setCurrentIndex(max(0, self.baud_combo.findData(saved)))
        self.baud_combo.setFixedWidth(130)
        row.addWidget(self.baud_combo)
        col.addLayout(row)
        col.addWidget(self._hint("baud_hint"))
        self.erase_check = QCheckBox()
        self._t(self.erase_check.setText, "erase_all")
        col.addSpacing(4)
        col.addWidget(self.erase_check)
        col.addWidget(self._hint("erase_hint"))

    def _build_right(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        status = QFrame()
        status.setObjectName("StatusPanel")
        row = QHBoxLayout(status)
        row.setContentsMargins(18, 14, 18, 14)
        self.status_dot = _label("●", "StatusDot")
        row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.status_title = _label(name="StatusTitle")
        self.status_detail = _label(name="StatusDetail", wrap=True)
        self.status_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(self.status_title)
        texts.addWidget(self.status_detail)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        texts.addSpacing(4)
        texts.addWidget(self.progress)
        row.addLayout(texts, 1)
        col.addWidget(status)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.flash_btn = _button("ActionButton", self._flash)
        self.flash_btn.setProperty("primary", True)
        self._t(self.flash_btn.setText, "flash")
        self.setup_btn = _button("ActionButton", self._write_setup)
        self._t(self.setup_btn.setText, "write_setup")
        self.monitor_btn = _button("ActionButton", self._toggle_monitor)
        self._t(self.monitor_btn.setText, "monitor")
        for button in (self.flash_btn, self.setup_btn, self.monitor_btn):
            actions.addWidget(button, 1)
        col.addLayout(actions)

        terminal = QFrame()
        terminal.setObjectName("TerminalPanel")
        lay = QVBoxLayout(terminal)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = QWidget()
        header.setObjectName("TerminalHeader")
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(16, 10, 12, 10)
        title = _label(name="TerminalTitle")
        self._t(title.setText, "log_title")
        hrow.addWidget(title)
        hrow.addWidget(_label("/ OUTPUT", "TerminalEyebrow"))
        hrow.addStretch(1)
        clear = _button("TerminalButton", lambda: self.log.clear())
        self._t(clear.setText, "clear")
        export = _button("TerminalButton", self._export_log)
        self._t(export.setText, "export")
        self.stop_btn = _button("StopButton", self._stop)
        self._t(self.stop_btn.setText, "stop")
        for button in (clear, export, self.stop_btn):
            hrow.addWidget(button)
        lay.addWidget(header)
        rule = QFrame()
        rule.setObjectName("TerminalRule")
        lay.addWidget(rule)
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogOutput")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(10000)
        lay.addWidget(self.log, 1)
        caption = _label(name="TerminalCaption")
        self._t(caption.setText, "log_caption")
        caption.setContentsMargins(16, 4, 16, 8)
        lay.addWidget(caption)
        col.addWidget(terminal, 1)
        return col

    # ---- language ----------------------------------------------------------

    def _language_changed(self) -> None:
        i18n.set_language(self.lang_combo.currentData())
        self.settings.setValue("language", i18n.language())
        for apply in self._texts:
            apply()
        index = self.source_combo.currentIndex()
        self._fill_sources(index)
        self._render_firmware()
        self._render_detected()
        self._render_status()
        self._build_setup(self.profile, keep=True)

    # ---- firmware: catalog -------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self.mode_buttons[mode].setChecked(True)
        self.mode_stack.setCurrentIndex(("catalog", "url", "local").index(mode))
        self.settings.setValue("mode", mode)
        if mode == "catalog" and not self.boards and self.sources:
            self._load_boards()

    def _fill_sources(self, index: int = -1) -> None:
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for source in self.sources:
            self.source_combo.addItem(source.text("name", i18n.language()), source.id)
        if index < 0:
            index = max(0, self.source_combo.findData(self.settings.value("source", "")))
        self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)
        self._render_source()

    def _source(self) -> catalog.Source | None:
        i = self.source_combo.currentIndex()
        return self.sources[i] if 0 <= i < len(self.sources) else None

    def _render_source(self) -> None:
        source = self._source()
        self.source_desc.setText(source.text("description", i18n.language()) if source else "")
        self.homepage_btn.setVisible(bool(source and source.homepage))

    def _source_changed(self) -> None:
        self._render_source()
        source = self._source()
        if source:
            self.settings.setValue("source", source.id)
        self.boards = []
        self._load_boards()

    def _open_homepage(self) -> None:
        source = self._source()
        if source and source.homepage:
            QDesktopServices.openUrl(QUrl(source.homepage))

    def _start_loader(self, job: workers.Job, on_done) -> None:
        if self.loader and self.loader.isRunning():
            self.loader.stop()
            self.loader.wait(3000)
        self.loader = job
        job.log.connect(self.log_event)
        job.done.connect(on_done)
        job.start()

    def _load_boards(self) -> None:
        source = self._source()
        if not source:
            return
        self.board_combo.blockSignals(True)
        self.board_combo.clear()
        self.board_combo.addItem(tr("boards_loading"))
        self.board_combo.setEnabled(False)
        self.board_combo.blockSignals(False)
        self._start_loader(workers.BoardsJob(source, self), self._boards_loaded)

    def _boards_loaded(self, ok: bool, key: str, detail: str, boards) -> None:
        self.board_combo.blockSignals(True)
        self.board_combo.clear()
        if not ok:
            self.board_combo.addItem(tr("boards_failed"))
            self.board_combo.blockSignals(False)
            self.log_event(f"{tr(key)} {detail}")
            return
        self.boards = boards
        for board in boards:
            self.board_combo.addItem(board.name, board.id)
        saved = self.settings.value("board", "")
        self.board_combo.setCurrentIndex(max(0, self.board_combo.findData(saved)))
        self.board_combo.setEnabled(True)
        self.board_combo.blockSignals(False)
        self.log_event(f"{tr(key)}: {len(boards)}")
        self._board_changed()

    def _board_changed(self) -> None:
        i = self.board_combo.currentIndex()
        if not (0 <= i < len(self.boards)):
            return
        board = self.boards[i]
        self.settings.setValue("board", board.id)
        self.board_note.setText(board.note)
        self.board_note.setVisible(bool(board.note))
        self.usb_ids = board.usb
        source = self._source()
        self._load_manifest(board.manifest_url, source.setup if source else "")

    # ---- firmware: URL and local -------------------------------------------

    def _load_url(self) -> None:
        url = self.url_edit.text().strip()
        if url:
            self.settings.setValue("url", url)
            self.usb_ids = []
            self._load_manifest(url, "")

    def _browse_local(self) -> None:
        start = self.local_edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, tr("browse"), start,
                                              "Firmware (*.json *.bin);;All files (*)")
        if path:
            self.local_edit.setText(path)
            self._load_local()

    def _load_local(self) -> None:
        path = self.local_edit.text().strip()
        if not path:
            return
        self.settings.setValue("local", path)
        self.usb_ids = []
        is_bin = path.lower().endswith(".bin")
        self.bin_box.setVisible(is_bin)
        if not is_bin:
            self._load_manifest(path, "")

    def _use_bin(self) -> None:
        try:
            offset = int(self.offset_edit.text().strip(), 0)
            fw = manifest.single_image(self.local_edit.text().strip(), offset, self.chip_combo.currentText())
        except ValueError as exc:
            key = str(exc) if str(exc) in i18n.STRINGS["en"] else "bad_value"
            self.set_status("need_firmware", key, "error")
            return
        self._set_firmware(fw, "")

    def _load_manifest(self, location: str, profile: str) -> None:
        self.firmware = None
        self._render_firmware()
        self._update_buttons()
        self.set_status("stage_loading_manifest", "", "busy")

        def done(ok, key, detail, fw):
            if ok:
                self._set_firmware(fw, profile)
            else:
                self.set_status(key, "", "error", detail)
                self.log_event(f"{tr(key)} {detail}")

        self._start_loader(workers.ManifestJob(location, self), done)

    def _set_firmware(self, fw: manifest.Manifest, profile: str) -> None:
        # Firmware from a URL or file gets Muse's setup when it says it's Muse.
        if not profile and "muse gadget" in fw.name.lower():
            profile = "muse"
        self.firmware = fw
        self.erase_check.setChecked(False)
        self._render_firmware()
        self._build_setup(profile)
        self.set_status("msg_manifest_loaded", "status_ready_detail", "success")
        self.log_event(f"{fw.name} {fw.version} · {', '.join(fw.chip_families)}")
        if self.port_combo.count():
            self._select_port(devices.pick_default(self._ports, self.usb_ids, self._current_port()))
        self._update_buttons()

    def _render_firmware(self) -> None:
        fw = self.firmware
        if not fw:
            self.fw_name.setText(tr("fw_none"))
            self.fw_meta.setText("")
            return
        self.fw_name.setText(tr("fw_summary", name=fw.name, version=fw.version or "—"))
        meta = [tr("fw_chips", chips=", ".join(fw.chip_families))]
        if fw.min_flash_bytes:
            meta.append(tr("fw_min_flash", mb=f"{fw.min_flash_bytes / (1 << 20):.1f}"))
        self.fw_meta.setText("  ·  ".join(meta))

    # ---- setup fields ------------------------------------------------------

    def _build_setup(self, profile: str, keep: bool = False) -> None:
        old = {f.command: e.text() for f, e in self._setup_inputs} if keep else {}
        while self.setup_layout.count():
            item = self.setup_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._setup_inputs = []
        self.profile = profile
        fields = provision.PROFILES.get(profile, [])
        self.setup_box.setVisible(bool(fields))
        if not fields:
            self._update_buttons()
            return
        self.setup_layout.addSpacing(6)
        self.setup_layout.addWidget(_rule())
        self.setup_layout.addSpacing(6)
        self.setup_layout.addWidget(_label("03 / SETUP", "PanelEyebrow"))
        self.setup_layout.addWidget(_label(tr("section_setup"), "PanelTitle"))
        self.setup_layout.addWidget(_label(tr("setup_hint"), "FieldHint", wrap=True))
        # Required fields first on screen; the send order stays the profile's.
        for field in sorted(fields, key=lambda f: not f.required):
            head = QHBoxLayout()
            head.addWidget(_label(tr(field.label), "FieldLabel"))
            head.addStretch(1)
            if field.link:
                link = _button("LinkButton", lambda _=False, u=field.link: QDesktopServices.openUrl(QUrl(u)))
                link.setText(tr("get_it"))
                head.addWidget(link)
            box = QWidget()
            box.setLayout(head)
            head.setContentsMargins(0, 4, 0, 0)
            self.setup_layout.addWidget(box)
            row = QHBoxLayout()
            edit = QLineEdit(old.get(field.command, ""))
            edit.setObjectName("PathInput")
            if field.secret:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
                eye = _button("EyeButton")
                eye.setText(tr("show"))
                eye.setFixedWidth(48)

                def toggle(_=False, e=edit, b=eye):
                    hidden = e.echoMode() == QLineEdit.EchoMode.Password
                    e.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
                    b.setText(tr("hide") if hidden else tr("show"))

                eye.clicked.connect(toggle)
            row.addWidget(edit, 1)
            if field.secret:
                row.addWidget(eye)
            line = QWidget()
            row.setContentsMargins(0, 0, 0, 0)
            line.setLayout(row)
            self.setup_layout.addWidget(line)
            self.setup_layout.addWidget(_label(tr(field.hint), "FieldHint", wrap=True))
            self._setup_inputs.append((field, edit))
        self._update_buttons()

    # ---- device ------------------------------------------------------------

    _ports: list[devices.Port] = []

    def _current_port(self) -> str:
        return self.port_combo.currentData() or ""

    def _select_port(self, device: str) -> None:
        index = self.port_combo.findData(device)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)

    def refresh_ports(self) -> None:
        if self.busy:
            return
        current = self._current_port() or self.settings.value("port", "")
        try:
            self._ports = devices.list_ports()
        except Exception as exc:
            self._ports = []
            self.log_event(str(exc))
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for port in self._ports:
            text = port.label() if port.likely_esp else f"{port.label()} {tr('port_other')}"
            self.port_combo.addItem(text, port.device)
        self.port_combo.blockSignals(False)
        self._select_port(devices.pick_default(self._ports, self.usb_ids, current))
        has = bool(self._ports)
        self.no_ports.setVisible(not has)
        self.driver_btn.setVisible(not has)
        self._port_changed()

    def _port_changed(self) -> None:
        self.detected = None
        self._render_detected()
        port = self._current_port()
        if port:
            self.settings.setValue("port", port)
        self._update_buttons()

    def _render_detected(self) -> None:
        info = self.detected
        if not info:
            self.detected_label.setText("")
            self.detected_label.setVisible(False)
            return
        flash = info.flash_bytes >> 20 if info.flash_bytes else "?"
        self.detected_label.setText(tr("detected", family=info.family, flash=flash, mac=info.mac or "?"))
        self.detected_label.setToolTip(info.features)
        self.detected_label.setVisible(True)

    # ---- jobs --------------------------------------------------------------

    def _update_buttons(self) -> None:
        busy = self.busy
        monitoring = busy and isinstance(self.job, workers.MonitorJob)
        has_port = bool(self._current_port())
        self.flash_btn.setEnabled(not busy and has_port and self.firmware is not None)
        self.setup_btn.setEnabled(not busy and has_port and bool(self._setup_inputs))
        self.monitor_btn.setEnabled(has_port and (not busy or monitoring))
        self.monitor_btn.setText(tr("stop") if monitoring else tr("monitor"))
        self.detect_btn.setEnabled(not busy and has_port)
        self.refresh_btn.setEnabled(not busy)
        self.port_combo.setEnabled(not busy)
        self.baud_combo.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def _start_job(self, job: workers.Job, on_done) -> None:
        self.job = job
        job.log.connect(self.log.appendPlainText)
        job.stage.connect(lambda key: self.set_status(key, "", "busy"))
        job.progress.connect(self._set_progress)
        job.done.connect(lambda ok, key, detail, result: self._job_done(job, ok, key, detail, result, on_done))
        job.start()
        self._update_buttons()

    def _set_progress(self, value: float) -> None:
        if value < 0:
            self.progress.setRange(0, 0)      # busy, no estimate
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(value * 1000))

    def _job_done(self, job, ok, key, detail, result, on_done) -> None:
        job.wait()
        if self.job is job:
            self.job = None
        self.progress.setRange(0, 1000)
        if not ok:
            self.progress.setValue(0)
        on_done(ok, key, detail, result)
        self._update_buttons()
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _fail(self, key: str, detail: str = "") -> None:
        kind = "idle" if key == "msg_stopped" else "error"
        self.set_status(key, "", kind, detail)
        self.log_event(f"{tr(key)} {detail}".strip())

    def _detect(self) -> None:
        port = self._current_port()
        if not port or self.busy:
            return

        def done(ok, key, detail, info):
            if not ok:
                self._fail(key, detail)
                return
            self.detected = info
            self._render_detected()
            self.set_status(key, "", "success")
            self.progress.setValue(1000)

        self._start_job(workers.DetectJob(port, self), done)

    def _flash(self) -> None:
        port, fw = self._current_port(), self.firmware
        if not fw:
            self.set_status("need_firmware", "", "warning")
            return
        if not port:
            self.set_status("need_port", "", "warning")
            return
        text = tr("confirm_flash", name=fw.name, port=port)
        if self.erase_check.isChecked():
            text += tr("confirm_erase")
        answer = QMessageBox.question(self, tr("confirm_flash_title"), text,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        baud = self.baud_combo.currentData()
        self.settings.setValue("baud", baud)
        self.log_event(f"{fw.name} → {port} @ {baud}")

        def done(ok, key, detail, info):
            if info:
                self.detected = info
                self._render_detected()
            if not ok:
                self._fail(key, detail)
                return
            follow = "msg_flashed_detail" if self._setup_inputs else "msg_flashed_plain"
            self.set_status(key, follow, "success")
            self.log_event(tr(key))

        self._start_job(workers.FlashJob(fw, port, baud, self.erase_check.isChecked(), self), done)

    def _write_setup(self) -> None:
        port = self._current_port()
        if not port or self.busy:
            return
        values = []
        for field, edit in self._setup_inputs:
            value = edit.text().strip()
            if not value:
                continue
            if not field.valid(value):
                self.set_status("bad_value", "", "warning")
                self.status_title.setText(tr("bad_value", label=tr(field.label)))
                return
            values.append((field, value))
        if not values:
            self.set_status("need_setup_value", "", "warning")
            return
        # The profile's order: anything that restarts the device goes last.
        order = [f.command for f in provision.PROFILES.get(self.profile, [])]
        values.sort(key=lambda fv: order.index(fv[0].command))
        restarts = any(f.restarts for f, _ in values)

        def done(ok, key, detail, _result):
            if not ok:
                self._fail(key, detail)
                return
            self.set_status(key, "msg_setup_restart" if restarts else "", "success")
            self.log_event(tr(key))
            self.progress.setValue(1000)

        self._start_job(workers.SetupJob(port, values, self), done)

    def _toggle_monitor(self) -> None:
        if self.busy and isinstance(self.job, workers.MonitorJob):
            self.job.stop()
            return
        port = self._current_port()
        if not port or self.busy:
            return

        def done(ok, key, detail, _result):
            if not ok:
                self._fail(key, detail)
            else:
                self.set_status(key, "", "idle")

        self._start_job(workers.MonitorJob(port, self), done)

    def _stop(self) -> None:
        if self.job and self.job.isRunning():
            self.job.stop()

    def _export_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("export"), "flash32.log", "Log (*.log *.txt)")
        if path:
            Path(path).write_text(provision.redact(self.log.toPlainText()), encoding="utf-8")
            self.log_event(tr("msg_saved_log"))

    def closeEvent(self, event) -> None:
        running = [j for j in (self.job, self.loader) if j and j.isRunning()]
        if running:
            self._closing = True
            for job in running:
                job.stop()
            for job in running:
                job.wait(5000)
        event.accept()
