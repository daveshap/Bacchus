"""Build a native Bacchus application bundle on the current platform."""

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "native")
    args = parser.parse_args()
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", str(args.dist.resolve()),
        "--workpath", str(args.work.resolve()),
        str(ROOT / "packaging" / "Bacchus.spec"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
