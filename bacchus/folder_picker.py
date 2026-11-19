"""Display the system's directory chooser for the local studio."""

import shutil
import subprocess
import sys

from .media_tools import process_options
from .workspace import WorkspaceError


def _windows_folder(initial):
    import ctypes
    from ctypes import wintypes

    shell, ole, user = ctypes.windll.shell32, ctypes.windll.ole32, ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT, wintypes.LPARAM, wintypes.LPARAM)

    class BrowseInfo(ctypes.Structure):
        _fields_ = [("owner", wintypes.HWND), ("root", ctypes.c_void_p), ("displayName", wintypes.LPWSTR),
                    ("title", wintypes.LPCWSTR), ("flags", wintypes.UINT), ("callback", callback_type),
                    ("data", wintypes.LPARAM), ("image", ctypes.c_int)]

    user.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    shell.SHBrowseForFolderW.argtypes = (ctypes.POINTER(BrowseInfo),)
    shell.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell.SHGetPathFromIDListEx.argtypes = (ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    initial_path = ctypes.create_unicode_buffer(str(initial))

    @callback_type
    def select_initial(window, message, value, data):
        if message == 1:  # BFFM_INITIALIZED
            user.SendMessageW(window, 0x467, 1, ctypes.addressof(initial_path))  # BFFM_SETSELECTIONW
        return 0

    if ole.CoInitializeEx(None, 2) < 0:  # COINIT_APARTMENTTHREADED
        raise WorkspaceError("The folder chooser could not open. Enter the full folder path.")
    item = None
    try:
        name = ctypes.create_unicode_buffer(260)
        info = BrowseInfo(None, None, ctypes.cast(name, wintypes.LPWSTR), "Choose a Bacchus workspace",
                          0x51, select_initial, 0, 0)  # BIF_RETURNONLYFSDIRS | BIF_USENEWUI
        item = shell.SHBrowseForFolderW(ctypes.byref(info))
        if not item:
            return ""
        path = ctypes.create_unicode_buffer(32768)
        if not shell.SHGetPathFromIDListEx(item, path, len(path), 0):
            raise WorkspaceError("Choose a folder on a filesystem, or enter the full folder path.")
        return path.value
    finally:
        if item:
            ole.CoTaskMemFree(item)
        ole.CoUninitialize()


def choose_folder(initial):
    if sys.platform == "win32":
        return _windows_folder(initial)
    if sys.platform == "darwin":
        command = ["/usr/bin/osascript", "-e", 'POSIX path of (choose folder with prompt "Choose a Bacchus workspace")']
    elif picker := shutil.which("zenity"):
        command = [picker, "--file-selection", "--directory", "--title=Choose a Bacchus workspace",
                   f"--filename={initial}/"]
    elif picker := shutil.which("kdialog"):
        command = [picker, "--getexistingdirectory", str(initial), "--title", "Choose a Bacchus workspace"]
    else:
        raise WorkspaceError("A system folder chooser is unavailable. Enter the full folder path.")
    try:
        result = subprocess.run(command, capture_output=True, **process_options())
    except OSError as error:
        raise WorkspaceError("The folder chooser could not open. Enter the full folder path.") from error
    if result.returncode == 0:
        return result.stdout.decode("utf-8").rstrip("\r\n")
    if result.returncode == 1 and (not result.stderr.strip() or b"(-128)" in result.stderr):
        return ""
    raise WorkspaceError("The folder chooser could not open. Enter the full folder path.")
