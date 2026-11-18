from importlib.metadata import distribution
from pathlib import Path
import runpy
import sys
import sysconfig


packaging = Path(SPECPATH).resolve()
project = packaging.parent
version = runpy.run_path(str(project / "bacchus" / "runtime.py"))["APP_VERSION"]
windows_icon = packaging / "Bacchus.ico"
mac_icon = packaging / "Bacchus.icns"


def notices(package, destination):
    installed = distribution(package)
    return [
        (str(installed.locate_file(file)), f"licenses/{destination}")
        for file in installed.files or ()
        if file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")) and installed.locate_file(file).is_file()
    ]


data = [
    (str(project / "bacchus" / "resources"), "bacchus/resources"),
    (str(project / "LICENSE"), "licenses/Bacchus"),
]
data.extend(notices("Pillow", "Pillow"))
data.extend(notices("PyInstaller", "PyInstaller"))
stdlib = Path(sysconfig.get_path("stdlib"))
for directory in (Path(sys.base_prefix), stdlib, stdlib.parent):
    python_license = next((directory / name for name in ("LICENSE.txt", "LICENSE") if (directory / name).is_file()), None)
    if python_license:
        data.append((str(python_license), "licenses/CPython"))
        break

analysis = Analysis(
    [str(project / "bacchus" / "__main__.py")],
    pathex=[str(project)],
    datas=data,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Bacchus",
    console=False,
    upx=False,
    icon=str(windows_icon) if sys.platform == "win32" and windows_icon.is_file() else None,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    upx=False,
    name="Bacchus",
)
if sys.platform == "darwin":
    app = BUNDLE(
        bundle,
        name="Bacchus.app",
        bundle_identifier="app.bacchus.studio",
        version=version,
        icon=str(mac_icon) if mac_icon.is_file() else None,
        info_plist={"NSHighResolutionCapable": True},
    )
