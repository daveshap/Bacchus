"""Desktop startup, single-instance ownership, and shutdown."""

import argparse
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
import traceback
import webbrowser

from . import media_tools
from .runtime import APP_VERSION, open_running
from .server import StudioServer
from .settings import Settings
from .storage_paths import writable_path
from .workflow import Studio, write_json
from .workspace import WorkspaceLocation


class AlreadyRunning(Exception):
    pass


@contextmanager
def data_lock(root):
    root = writable_path(root)
    path = writable_path(root / ".instance.lock")
    root.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise AlreadyRunning(f"Another Bacchus process already uses {root}.") from error
        yield


class _DesktopLog:
    def __init__(self, stream, path):
        self.stream = stream
        self.path = path

    def write(self, text):
        writable_path(self.path)
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextmanager
def desktop_log(data):
    previous = sys.stdout, sys.stderr
    path = writable_path(data / "bacchus.log")
    mode = "w" if path.is_file() and path.stat().st_size >= 1024 * 1024 else "a"
    with path.open(mode, encoding="utf-8", buffering=1) as stream:
        log = _DesktopLog(stream, path)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
        try:
            yield
        except Exception:
            traceback.print_exc(file=log)
            raise
        finally:
            sys.stdout, sys.stderr = previous


def _serve(options, workspace):
    data = workspace.active
    with data_lock(data), desktop_log(data):
        media_tools.configure(data)
        library = options.library.expanduser() if options.library is not None else None
        studio = Studio(data, library)
        with StudioServer(options.port, studio, Settings(data), workspace) as server:
            url = f"http://127.0.0.1:{server.server_port}"
            runtime = writable_path(data / "runtime.json")
            write_json(runtime, {"url": url, "instance": server.instance_id})
            try:
                print(f"Bacchus {APP_VERSION} is running at {url}", flush=True)
                if not options.no_browser:
                    browser = threading.Timer(0.3, webbrowser.open, args=(url,))
                    browser.daemon = True
                    browser.start()
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nClosing Bacchus.", flush=True)
            finally:
                writable_path(runtime).unlink(missing_ok=True)
            for job in tuple(studio.jobs):
                job.join()


def main():
    parser = argparse.ArgumentParser(description="Open the Bacchus animation studio.")
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data", type=Path,
                        help="Use this workspace for this launch. Settings remembers the normal workspace.")
    parser.add_argument("--library", type=Path, help="Copy an existing Bacchus library into its internal style libraries.")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    options = parser.parse_args()
    workspace = WorkspaceLocation(options.data)
    try:
        _serve(options, workspace)
    except AlreadyRunning as error:
        if options.no_browser or not open_running(workspace.active):
            raise RuntimeError(str(error)) from error


def run():
    try:
        main()
    except Exception as error:
        if sys.platform == "win32" and getattr(sys, "frozen", False) and "--no-browser" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "Bacchus could not start", 0x10)
        elif sys.stderr:
            print(f"Bacchus could not start: {error}", file=sys.stderr)
        raise SystemExit(1) from error
