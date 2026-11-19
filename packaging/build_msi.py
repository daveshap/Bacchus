"""Build a per-user Windows x64 MSI using Windows' MSI API and makecab."""

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4, uuid5

from bundle_resources import verify_bundled_resources


UPGRADE_CODE = UUID("8e3e6d31-68ca-45d1-9f52-2b6db07cbb6d")
PRODUCT = "Bacchus"
LANGUAGE = 1033
COMPONENT_REGISTRY_X64 = 4 | 256
FILE_COMPRESSED_VITAL = 16384 | 512

# Windows Installer table columns, followed by their primary-key columns.
TABLES = {
    "Property": ("Property:72! Value:0!", "Property"),
    "Directory": ("Directory:72! Directory_Parent:72 DefaultDir:255!", "Directory"),
    "Component": ("Component:72! ComponentId:38 Directory_:72! Attributes:i! Condition:255 KeyPath:72", "Component"),
    "Feature": (
        "Feature:38! Feature_Parent:38 Title:64 Description:255 Display:i Level:i! Directory_:72 Attributes:i!",
        "Feature",
    ),
    "FeatureComponents": ("Feature_:38! Component_:72!", "Feature_ Component_"),
    "File": ("File:72! Component_:72! FileName:255! FileSize:I! Version:72 Language:20 Attributes:i Sequence:i!", "File"),
    "Media": ("DiskId:i! LastSequence:i! DiskPrompt:64 Cabinet:255 VolumeLabel:32 Source:72", "DiskId"),
    "Registry": ("Registry:72! Root:i! Key:255! Name:255 Value:0 Component_:72!", "Registry"),
    "Shortcut": (
        "Shortcut:72! Directory_:72! Name:128! Component_:72! Target:255! Arguments:255 Description:255 "
        "Hotkey:i Icon_:72 IconIndex:i ShowCmd:i WkDir:72 DisplayResourceDLL:255 DisplayResourceId:i "
        "DescriptionResourceDLL:255 DescriptionResourceId:i",
        "Shortcut",
    ),
    "RemoveFile": ("FileKey:72! Component_:72! FileName:255 DirProperty:72! InstallMode:i!", "FileKey"),
    "Upgrade": (
        "UpgradeCode:38! VersionMin:20 VersionMax:20 Language:255 Attributes:I! Remove:255 ActionProperty:72!",
        "UpgradeCode VersionMin VersionMax Language Attributes",
    ),
    "LaunchCondition": ("Condition:255! Description:255!", "Condition"),
    "InstallUISequence": ("Action:72! Condition:255 Sequence:i", "Action"),
    "InstallExecuteSequence": ("Action:72! Condition:255 Sequence:i", "Action"),
    "AdminExecuteSequence": ("Action:72! Condition:255 Sequence:i", "Action"),
}


class BuildError(RuntimeError):
    pass


def guid(value):
    return "{" + str(value).upper() + "}"


def declared_columns(spec):
    for field in spec.split():
        name, type_code = field.split(":")
        required = type_code.endswith("!")
        type_code = type_code.rstrip("!")
        sql_type = {"i": "SHORT", "I": "LONG"}.get(type_code, f"CHAR({type_code})")
        yield name, sql_type + (" NOT NULL" if required else "")


def version_arg(value):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise argparse.ArgumentTypeError("Use a numeric major.minor.patch release.")
    fields = tuple(int(part) for part in value.split("."))
    if any(number > limit for number, limit in zip(fields, (255, 255, 65535), strict=True)):
        raise argparse.ArgumentTypeError("MSI release components must fit 255.255.65535.")
    return ".".join(map(str, fields))


class Msi:
    def __init__(self):
        self.api = ctypes.WinDLL("msi.dll")
        self.handle = wintypes.UINT
        string, pointer = wintypes.LPCWSTR, ctypes.POINTER(self.handle)
        signatures = {
            "MsiOpenDatabaseW": (string, ctypes.c_void_p, pointer),
            "MsiDatabaseOpenViewW": (self.handle, string, pointer),
            "MsiViewExecute": (self.handle, self.handle),
            "MsiViewClose": (self.handle,),
            "MsiCloseHandle": (self.handle,),
            "MsiCreateRecord": (wintypes.UINT,),
            "MsiRecordSetStringW": (self.handle, wintypes.UINT, string),
            "MsiRecordSetInteger": (self.handle, wintypes.UINT, ctypes.c_int),
            "MsiRecordSetStreamW": (self.handle, wintypes.UINT, string),
            "MsiDatabaseCommit": (self.handle,),
            "MsiGetSummaryInformationW": (self.handle, string, wintypes.UINT, pointer),
            "MsiSummaryInfoSetPropertyW": (
                self.handle, wintypes.UINT, wintypes.UINT, ctypes.c_int, ctypes.c_void_p, string,
            ),
            "MsiSummaryInfoPersist": (self.handle,),
            "MsiGetLastErrorRecord": (),
            "MsiFormatRecordW": (self.handle, self.handle, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)),
        }
        for name, args in signatures.items():
            method = getattr(self.api, name)
            method.argtypes = args
            method.restype = self.handle

    def check(self, operation, *args):
        code = getattr(self.api, operation)(*args)
        if code:
            detail = ""
            record = self.api.MsiGetLastErrorRecord()
            if record:
                with self.owned(record):
                    buffer = ctypes.create_unicode_buffer(2048)
                    size = wintypes.DWORD(len(buffer))
                    if not self.api.MsiFormatRecordW(0, record, buffer, ctypes.byref(size)):
                        detail = f" {buffer.value}"
            raise BuildError(f"{operation} failed with Windows Installer error {code}.{detail}")

    @contextmanager
    def owned(self, handle):
        try:
            yield handle
        finally:
            self.api.MsiCloseHandle(handle)

    @contextmanager
    def open_database(self, path):
        handle = self.handle()
        self.check("MsiOpenDatabaseW", str(path), 3, ctypes.byref(handle))  # MSIDBOPEN_CREATE
        with self.owned(handle):
            yield handle

    @contextmanager
    def view(self, database, sql):
        handle = self.handle()
        self.check("MsiDatabaseOpenViewW", database, sql, ctypes.byref(handle))
        with self.owned(handle):
            try:
                yield handle
            finally:
                self.api.MsiViewClose(handle)

    def execute(self, database, sql):
        with self.view(database, sql) as view:
            self.check("MsiViewExecute", view, 0)

    def rows(self, database, table, columns, rows):
        fields = ", ".join(f"`{name}`" for name in columns)
        parameters = ", ".join("?" for _ in columns)
        with self.view(database, f"INSERT INTO `{table}` ({fields}) VALUES ({parameters})") as view:
            for row in rows:
                with self.owned(self.api.MsiCreateRecord(len(columns))) as record:
                    for index, item in enumerate(row, start=1):
                        if isinstance(item, int):
                            self.check("MsiRecordSetInteger", record, index, item)
                        elif isinstance(item, Path):
                            self.check("MsiRecordSetStreamW", record, index, str(item))
                        elif item is not None:
                            self.check("MsiRecordSetStringW", record, index, item)
                    self.check("MsiViewExecute", view, record)

    def summary(self, database):
        handle = self.handle()
        self.check("MsiGetSummaryInformationW", database, None, 12, ctypes.byref(handle))
        values = {
            1: (2, 1252, None),                   # Code page (VT_I2).
            2: (30, 0, "Installation Database"),
            3: (30, 0, "Bacchus Animation Studio"),
            4: (30, 0, PRODUCT),
            5: (30, 0, "Installer, MSI"),
            6: (30, 0, "Per-user Windows x64 installer."),
            7: (30, 0, f"x64;{LANGUAGE}"),
            9: (30, 0, guid(uuid4())),
            14: (3, 500, None),                  # Windows Installer 5.0 (VT_I4).
            15: (3, 2 | 8, None),                # Compressed source, no elevation.
            19: (3, 2, None),                    # Read-only recommended.
        }
        with self.owned(handle):
            for key, (kind, number, text) in values.items():
                self.check("MsiSummaryInfoSetPropertyW", handle, key, kind, number, None, text)
            self.check("MsiSummaryInfoPersist", handle)


def short_long(short, long):
    if any(char in long for char in '<>:"/\\|?*') or len(long) > 230:
        raise BuildError(f"Cannot represent the bundle filename {long!r} in an installer.")
    return f"{short}|{long}"


def check_bundle(bundle):
    launcher = bundle / f"{PRODUCT}.exe"
    try:
        with launcher.open("rb") as file:
            header = file.read(64)
            if not header.startswith(b"MZ") or len(header) != 64:
                raise BuildError("The Bacchus launcher is not a Windows executable.")
            file.seek(int.from_bytes(header[60:64], "little"))
            machine = file.read(6)
    except OSError as error:
        raise BuildError("The bundle must contain Bacchus.exe.") from error
    if machine != b"PE\x00\x00\x64\x86":
        raise BuildError("The MSI requires an x64 Windows Bacchus executable.")
    try:
        verify_bundled_resources(bundle / "_internal" / "bacchus" / "resources")
    except ValueError as error:
        raise BuildError(str(error)) from error


def bundle_tables(bundle, version, cabinet, staging):
    product_code = guid(uuid5(UPGRADE_CODE, version))
    props = {
        "ProductCode": product_code,
        "ProductName": PRODUCT,
        "Manufacturer": PRODUCT,
        "ProductVersion": version,
        "ProductLanguage": str(LANGUAGE),
        "UpgradeCode": guid(UPGRADE_CODE),
        "ARPNOMODIFY": "1",
        "SecureCustomProperties": "OLDPRODUCTS;NEWERPRODUCTFOUND",
        "MSIFASTINSTALL": "1",
    }
    rows = {name: [] for name in TABLES}
    rows["Property"] = list(props.items())
    rows["Directory"] = [
        ("TARGETDIR", None, "SourceDir"),
        ("LocalAppDataFolder", "TARGETDIR", "."),
        ("ProgramsDir", "LocalAppDataFolder", "Programs"),
        ("INSTALLDIR", "ProgramsDir", PRODUCT),
        ("ProgramMenuFolder", "TARGETDIR", "."),
    ]
    rows["Feature"] = [("Main", None, PRODUCT, "Bacchus Animation Studio", 1, 1, "INSTALLDIR", 0)]
    rows["Upgrade"] = [
        (guid(UPGRADE_CODE), None, version, None, 0, None, "OLDPRODUCTS"),
        (guid(UPGRADE_CODE), version, None, None, 2, None, "NEWERPRODUCTFOUND"),
    ]
    rows["LaunchCondition"] = [
        ("NOT NEWERPRODUCTFOUND", "A newer version of Bacchus is already installed."),
        ("VersionNT64", "Bacchus needs 64-bit Windows."),
    ]
    costs = [("CostInitialize", None, 800), ("FileCost", None, 900), ("CostFinalize", None, 1000)]
    start = [("FindRelatedProducts", None, 25), ("LaunchConditions", None, 100), *costs]
    rows["InstallUISequence"] = [*start, ("ExecuteAction", None, 1300)]
    rows["InstallExecuteSequence"] = [
        *start, ("InstallValidate", None, 1400), ("InstallInitialize", None, 1500),
        ("RemoveExistingProducts", "OLDPRODUCTS", 1520), ("ProcessComponents", None, 1600),
        ("UnpublishFeatures", None, 1800), ("RemoveRegistryValues", None, 2600),
        ("RemoveShortcuts", None, 3200), ("RemoveFiles", None, 3500),
        ("RemoveFolders", None, 3600), ("InstallFiles", None, 4000),
        ("CreateShortcuts", None, 4500), ("WriteRegistryValues", None, 5000),
        ("RegisterUser", None, 6000), ("RegisterProduct", None, 6100),
        ("PublishFeatures", None, 6300), ("PublishProduct", None, 6400),
        ("InstallFinalize", None, 6600),
    ]
    rows["AdminExecuteSequence"] = [
        *costs, ("InstallValidate", None, 1400), ("InstallInitialize", None, 1500),
        ("InstallAdminPackage", None, 3900), ("InstallFiles", None, 4000), ("InstallFinalize", None, 6600),
    ]

    directories = {Path("."): "INSTALLDIR"}
    directory_components = {}
    packed_sources = []
    package_files = sorted(
        (file for file in bundle.rglob("*") if file.is_file()),
        key=lambda file: file.relative_to(bundle).as_posix().casefold(),
    )
    if len(package_files) > 32767:
        raise BuildError("The Bacchus bundle exceeds the Windows Installer file sequence limit.")
    for index, file in enumerate(package_files, start=1):
        relative = file.relative_to(bundle)
        lineage = [path for path in reversed(relative.parent.parents) if path != Path(".")]
        if relative.parent != Path("."):
            lineage.append(relative.parent)
        for path in lineage:
            if path not in directories:
                number = len(directories)
                identifier = f"D{number:07X}"
                rows["Directory"].append((identifier, directories[path.parent], short_long(identifier, path.name)))
                directories[path] = identifier

        identity = relative.as_posix().casefold()
        component_guid = uuid5(UPGRADE_CODE, identity)
        component = f"C{component_guid.hex}"
        registry = f"R{component_guid.hex}"
        directory = directories[relative.parent]
        key = f"F{index:07X}"
        rows["Component"].append((component, guid(component_guid), directory, COMPONENT_REGISTRY_X64, None, registry))
        rows["FeatureComponents"].append(("Main", component))
        rows["Registry"].append((registry, 1, "Software\\Bacchus\\InstallComponents", component, "#1", component))
        rows["File"].append((key, component, short_long(key, relative.name), file.stat().st_size,
                             None, None, FILE_COMPRESSED_VITAL, index))
        directory_components.setdefault(directory, component)
        if relative == Path("Bacchus.exe"):
            rows["Shortcut"].append((
                "LaunchBacchus", "ProgramMenuFolder", PRODUCT, component, f"[#{key}]", None,
                "Open Bacchus Animation Studio", None, None, None, None, "INSTALLDIR",
                None, None, None, None,
            ))
        copied = staging / key
        shutil.copyfile(file, copied)
        packed_sources.append(key)

    for directory, component in directory_components.items():
        rows["RemoveFile"].append((f"Remove{directory}", component, None, directory, 2))
    rows["Media"] = [(1, len(packed_sources), None, f"#{cabinet.name}", None, None)]
    return rows, packed_sources


def make_cabinet(staging, cabinet, sources):
    makecab = shutil.which("makecab.exe")
    if not makecab:
        raise BuildError("The Windows makecab utility is required.")
    flags = [
        ".OPTION EXPLICIT",
        f".Set CabinetNameTemplate={cabinet.name}",
        ".Set DiskDirectory1=.",
        ".Set Cabinet=on",
        ".Set Compress=on",
        ".Set CompressionType=LZX",
        ".Set CompressionMemory=21",
        ".Set MaxDiskSize=0",
        ".Set MaxCabinetSize=0",
        ".Set MaxDiskFileCount=0",
        ".Set InfFileName=setup.inf",
        ".Set RptFileName=setup.rpt",
    ]
    ddf = staging / "payload.ddf"
    ddf.write_text("\n".join([*flags, *sources, ""]), encoding="ascii")
    result = subprocess.run([makecab, "/V1", "/F", ddf.name], cwd=staging, capture_output=True, text=True)
    if result.returncode or not cabinet.is_file():
        detail = (result.stderr or result.stdout).strip()[-1800:]
        raise BuildError(f"Could not build the installer cabinet. {detail}")


def build(bundle, version, output):
    if sys.platform != "win32":
        raise BuildError("Build the MSI on a Windows machine.")
    if output.is_relative_to(bundle):
        raise BuildError("Write the MSI outside the bundle directory.")
    check_bundle(bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".bacchus-msi-", dir=output.parent) as work:
        staging = Path(work)
        cabinet = staging / "payload.cab"
        database = staging / "Bacchus.msi"
        rows, sources = bundle_tables(bundle, version, cabinet, staging)
        make_cabinet(staging, cabinet, sources)
        msi = Msi()
        with msi.open_database(database) as handle:
            for table, (spec, keys) in TABLES.items():
                definitions = list(declared_columns(spec))
                columns = ", ".join(f"`{name}` {sql_type}" for name, sql_type in definitions)
                primary = ", ".join(f"`{key}`" for key in keys.split())
                msi.execute(handle, f"CREATE TABLE `{table}` ({columns} PRIMARY KEY {primary})")
                msi.rows(handle, table, [name for name, _ in definitions], rows[table])
            msi.rows(handle, "_Streams", ["Name", "Data"], [(cabinet.name, cabinet)])
            msi.summary(handle)
            msi.check("MsiDatabaseCommit", handle)
        # The private build directory has a restrictive Windows ACL. Create
        # the delivery file in its destination directory so it inherits access.
        artifact = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
        try:
            with database.open("rb") as source, artifact.open("xb") as target:
                shutil.copyfileobj(source, target)
            artifact.replace(output)
        finally:
            artifact.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="PyInstaller dist/Bacchus directory")
    parser.add_argument("--version", type=version_arg, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.bundle.resolve(), args.version, args.output.resolve())
    except (BuildError, OSError) as error:
        parser.exit(1, f"{error}\n")
    with args.output.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    print(f"{args.output.resolve()}\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
