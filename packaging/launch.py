"""Entry point for the installed Bacchus application."""

import ctypes
import sys

from bacchus.server import main


if __name__ == "__main__":
    try:
        main(desktop=True)
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code in (0, None):
            raise
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, str(error), "Bacchus could not start", 0x10)
        elif sys.stderr:
            print(f"Bacchus could not start: {error}", file=sys.stderr)
        raise SystemExit(1) from error
