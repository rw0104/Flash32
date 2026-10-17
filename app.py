"""Flash32 entry point.

`app.py --run-esptool ARGS...` runs esptool itself: the packaged app starts
itself that way for flashing, so users need no Python of their own.
"""

from __future__ import annotations

import sys


def _run_esptool(args: list[str]) -> int:
    if sys.stdout is None or sys.stderr is None:
        # A windowed build has no console streams; use the pipes the parent gave.
        import io
        import os
        sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", buffering=0), encoding="utf-8", write_through=True)
        sys.stderr = sys.stdout
    import esptool
    try:
        esptool.main(args)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:      # esptool reports failures as exceptions
        print(f"A fatal error occurred: {exc}", flush=True)
        return 2
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--run-esptool":
        return _run_esptool(sys.argv[2:])

    from PySide6.QtWidgets import QApplication

    from flasher import catalog
    from flasher.styles import APP_STYLE
    from flasher.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Flash32")
    app.setOrganizationName("flash32")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow(catalog.load_catalog())
    window.show()
    if "--screenshot" in sys.argv:
        # For docs and CI: render once, save, quit.
        from PySide6.QtCore import QTimer
        out = sys.argv[sys.argv.index("--screenshot") + 1]
        QTimer.singleShot(2500, lambda: (window.grab().save(out), app.quit()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
