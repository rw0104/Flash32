"""The look: calm greens on paper, a dark terminal for the log."""

APP_STYLE = """
QMainWindow { background: #eeeee8; }
QWidget {
    color: #223a38;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 12px;
}
QWidget#Workspace { background: #eeeee8; }
QLabel { background: transparent; }
QLabel#BrandMark {
    color: #f1f3e8; background: #284a45; border-radius: 10px;
    font-family: "Bahnschrift"; font-size: 27px; font-weight: 600;
}
QLabel#BrandTitle {
    font-family: "Bahnschrift", "Microsoft YaHei UI";
    font-size: 25px; font-weight: 600; color: #243e39;
}
QLabel#BrandSubtitle { color: #697771; font-size: 12px; }
QLabel#LocalBadge {
    color: #46645b; border: 1px solid #c7d0c4; border-radius: 13px;
    padding: 5px 12px; background: #e7ebe1;
}
QLabel#VersionLabel, QPushButton#VersionButton {
    color: #7c857c; font-family: "Consolas"; font-size: 11px;
}
QPushButton#VersionButton { background: transparent; border: none; padding: 4px 2px; }
QPushButton#VersionButton:hover { color: #2f5d50; text-decoration: underline; }
QFrame#Rule { background: #d7dcd1; border: none; max-height: 1px; }
QFrame#Workflow {
    background: #e6e9df; border: 1px solid #d7dcd1; border-radius: 10px;
}
QLabel#WorkflowCaption {
    color: #6e7d70; font-size: 11px; font-weight: 600;
}
QLabel#StepNumber {
    color: #527367; font-family: "Consolas"; font-size: 11px;
}
QLabel#StepText { color: #354f45; font-size: 12px; font-weight: 600; }
QLabel#StepHint { color: #7b867b; font-size: 10px; }
QLabel#StepArrow { color: #92a08f; font-size: 18px; }
QFrame#ConfigPanel {
    background: #fbfbf5; border: 1px solid #d8ded0; border-radius: 12px;
}
QWidget#ConfigContents, QScrollArea#ConfigScroll { background: transparent; border: 0; }
QScrollArea#ConfigScroll > QWidget > QWidget { background: transparent; }
QLabel#PanelEyebrow {
    color: #7e8a7d; font-family: "Consolas"; font-size: 10px;
}
QLabel#PanelTitle { color: #29473e; font-size: 17px; font-weight: 600; }
QLabel#FieldLabel { color: #40594d; font-size: 11px; font-weight: 600; }
QLabel#FieldHint { color: #7b867a; font-size: 10px; }
QFrame#BoardCard {
    background: #edf1e7; border: 1px solid #dce4d2; border-radius: 8px;
}
QLabel#BoardName { color: #395748; font-size: 12px; font-weight: 600; }
QLabel#BoardChip {
    color: #6e7e69; font-family: "Consolas"; font-size: 10px;
}
QLabel#HardwareNotice { color: #8c7351; font-size: 10px; }
QLineEdit, QComboBox {
    background: #ffffff; color: #354d42; border: 1px solid #d4dccd;
    border-radius: 6px; min-height: 32px; padding: 0px 9px;
    selection-background-color: #48766b; selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #5d8c79; background: #ffffff; }
QLineEdit:disabled, QComboBox:disabled {
    color: #939d91; background: #eeefe8; border-color: #e0e3d9;
}
QLineEdit#PathInput { font-family: "Consolas", "Microsoft YaHei UI"; font-size: 11px; }
QComboBox { padding-right: 20px; }
QComboBox::drop-down {
    border: none; width: 22px; background: transparent;
}
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView {
    background: #ffffff; color: #354d42; border: 1px solid #c8d2c1;
    selection-background-color: #e0e9db; selection-color: #244d3e;
    outline: none; padding: 4px;
}
QPushButton {
    background: #f5f7ef; border: 1px solid #cfd9c7; border-radius: 6px;
    color: #46604f; min-height: 32px; padding: 0px 10px;
}
QPushButton:hover { background: #e7eee0; border-color: #b3c7ac; }
QPushButton:pressed { background: #dce8d4; }
QPushButton:focus { border-color: #568872; }
QPushButton:disabled {
    background: #eeefe8; color: #a0aa99; border-color: #dfe4d6;
}
QPushButton#BrowseButton { font-size: 14px; padding: 0px; }
QPushButton#EyeButton { padding: 0px 6px; font-size: 10px; }
QPushButton#SaveButton { background: #edf1e5; color: #48623f; }
QPushButton#AdvancedToggle {
    text-align: left; border: none; border-radius: 0; background: transparent;
    color: #64785c; min-height: 24px; padding: 0;
}
QPushButton#AdvancedToggle:hover { color: #2a6550; }
QLabel#AdvancedHint { color: #83907b; font-size: 10px; }
QLabel#SectionTitle { color: #3d5549; font-size: 12px; font-weight: 600; }
QLabel#SectionMeta { color: #87917e; font-size: 10px; }
QFrame#StatusPanel {
    background: #f5f6ef; border: 1px solid #d7ddcf; border-radius: 10px;
}
QLabel#StatusTitle { color: #2d4a3c; font-size: 17px; font-weight: 600; }
QLabel#StatusDetail { color: #7a8772; font-size: 11px; }
QLabel#StatusDot { color: #76906c; font-size: 19px; }
QLabel#StatusDot[kind="busy"] { color: #468c7a; }
QLabel#StatusDot[kind="success"] { color: #4b865d; }
QLabel#StatusDot[kind="warning"] { color: #ad8744; }
QLabel#StatusDot[kind="error"] { color: #b26a58; }
QLabel#ActivityLabel {
    background: #e9ede1; color: #6c7d60; border-radius: 5px;
    padding: 5px 9px; font-size: 10px;
}
QLabel#ActivityLabel[busy="true"] { background: #dcebe1; color: #346c55; }
QPushButton#UtilityButton {
    background: transparent; color: #6c8060; border: 1px solid #d3ddc9;
    min-height: 28px; font-size: 11px;
}
QPushButton#UtilityButton:hover { background: #e3eadb; }
QPushButton#QuietButton {
    background: transparent; border: 0; color: #7a8c6e; min-height: 28px;
    font-size: 10px; padding: 0px 5px;
}
QPushButton#QuietButton:hover { color: #315c44; background: #e4eadb; }
QPushButton#ActionButton {
    background: #f8faf2; color: #385b43; border: 1px solid #c7d4bc;
    min-height: 50px; font-size: 12px; font-weight: 600; border-radius: 8px;
}
QPushButton#ActionButton:hover { background: #e6eede; border-color: #99b58e; }
QPushButton#ActionButton:pressed { background: #d9e7cd; }
QPushButton#ActionButton:disabled {
    background: #e8ece1; color: #9ba791; border-color: #d5ddcc;
}
QPushButton#ActionButton[primary="true"] {
    background: #366858; color: #f3f8ec; border-color: #366858;
}
QPushButton#ActionButton[primary="true"]:hover { background: #2a5b4b; }
QPushButton#ActionButton[primary="true"]:pressed { background: #214c3e; }
QPushButton#ActionButton[primary="true"]:disabled {
    background: #a4b9a9; color: #e5ede0; border-color: #a4b9a9;
}
QFrame#TerminalPanel {
    background: #192c2a; border: 1px solid #223d37; border-radius: 11px;
}
QWidget#TerminalHeader { background: transparent; }
QLabel#TerminalEyebrow {
    color: #7c9b8b; font-family: "Consolas"; font-size: 10px;
}
QLabel#TerminalTitle { color: #dbe8d8; font-size: 12px; font-weight: 600; }
QLabel#TerminalCaption { color: #789286; font-size: 10px; }
QFrame#TerminalRule { background: #2e433d; border: none; max-height: 1px; }
QPushButton#TerminalButton {
    background: transparent; border: 1px solid #3c5448; color: #aec0a7;
    min-height: 25px; padding: 0 9px; font-size: 10px; border-radius: 5px;
}
QPushButton#TerminalButton:hover { background: #2e493c; border-color: #6b876b; }
QPushButton#StopButton {
    background: #49382f; color: #e2b7a0; border: 1px solid #745342;
    min-height: 25px; padding: 0 11px; font-size: 10px; border-radius: 5px;
}
QPushButton#StopButton:hover { background: #644338; border-color: #a7745b; }
QPushButton#StopButton:disabled {
    color: #68796a; background: #24392f; border-color: #354a3b;
}
QPlainTextEdit#LogOutput {
    background: transparent; color: #ccd9c7; border: none;
    font-family: "Consolas", "Microsoft YaHei UI"; font-size: 12px;
    selection-background-color: #4b6a54; selection-color: #ffffff;
    padding: 8px 13px;
}
QScrollBar:vertical {
    background: transparent; width: 7px; margin: 3px 1px;
}
QScrollBar::handle:vertical { background: #b5c1ac; border-radius: 3px; min-height: 28px; }
QPlainTextEdit#LogOutput QScrollBar::handle:vertical { background: #4f6856; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QPlainTextEdit#LogOutput QScrollBar:horizontal {
    background: #192c2a; height: 7px; margin: 1px 3px;
}
QPlainTextEdit#LogOutput QScrollBar::handle:horizontal {
    background: #4f6856; border-radius: 3px; min-width: 28px;
}
QPlainTextEdit#LogOutput QScrollBar::add-line:horizontal,
QPlainTextEdit#LogOutput QScrollBar::sub-line:horizontal { width: 0px; }
QPlainTextEdit#LogOutput QScrollBar::add-page:horizontal,
QPlainTextEdit#LogOutput QScrollBar::sub-page:horizontal { background: transparent; }
QPlainTextEdit#LogOutput::corner { background: #192c2a; border: 0; }
QLabel#Footer { color: #7e8e76; font-size: 10px; }
QLabel#FooterMono { color: #87937b; font-family: "Consolas"; font-size: 10px; }
QPushButton#SegButton {
    background: transparent; border: 1px solid #d3ddc9; border-radius: 0;
    color: #5f7458; min-height: 28px; padding: 0 12px; font-size: 11px;
}
QPushButton#SegButton[pos="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton#SegButton[pos="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton#SegButton:checked { background: #366858; color: #f3f8ec; border-color: #366858; }
QPushButton#LinkButton {
    background: transparent; border: 0; color: #3f7a62; min-height: 20px;
    padding: 0; font-size: 11px; text-align: left;
}
QPushButton#LinkButton:hover { color: #1f5a43; text-decoration: underline; }
QLabel#FirmwareName { color: #2f4c40; font-size: 13px; font-weight: 600; }
QLabel#FirmwareMeta { color: #6e7e69; font-family: "Consolas"; font-size: 10px; }
QLabel#DetectedLabel { color: #3f6a55; font-family: "Consolas"; font-size: 11px; }
QLabel#WarningHint { color: #8c7351; font-size: 10px; }
QCheckBox { spacing: 7px; color: #40594d; font-size: 11px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border: 1px solid #b9c6b1; border-radius: 4px; background: #ffffff;
}
QCheckBox::indicator:checked { background: #366858; border-color: #366858; }
QProgressBar {
    background: #e3e8dc; border: 0; border-radius: 3px; max-height: 6px; min-height: 6px;
}
QProgressBar::chunk { background: #468c7a; border-radius: 3px; }
QComboBox#LangCombo { min-height: 26px; font-size: 11px; }
QToolTip {
    color: #eef4e7; background: #284a3b; border: 1px solid #53745a;
    padding: 5px 7px; font-size: 11px;
}
"""
