from __future__ import annotations

import sys


def main() -> int:
    try:
        from app.qt_window import run_qt_app
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            print("Missing dependency: PySide6. Run `pip install -r requirements.txt` first.")
            return 1
        raise
    return run_qt_app()


if __name__ == "__main__":
    raise SystemExit(main())
