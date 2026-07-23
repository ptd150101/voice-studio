"""Build OmniVoice with Nuitka."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist"
ENTRY = PROJECT / "omnivoice" / "cli" / "demo.py"
APP_NAME = "voice-studio"


def build_args(mode: str, jobs: int | None) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--windows-console-mode=disable",
        "--company-name=Voice Studio",
        "--product-name=Voice Studio",
        "--file-version=1.0.0",
        "--product-version=1.0.0",
        "--copyright=Voice Studio",
        "--include-package=omnivoice",
        "--include-data-dir=omnivoice=omnivoice",
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-setuptools-mode=nofollow",
        "--noinclude-numba-mode=nofollow",
        "--module-parameter=torch-disable-jit=yes",
        "--module-parameter=numba-disable-jit=yes",
        "--assume-yes-for-downloads",
        f"--output-dir={DIST}",
        f"--output-filename={APP_NAME}.exe",
    ]
    if jobs:
        args.append(f"--jobs={jobs}")
    if mode == "onefile":
        args.append("--onefile")
    args.append(str(ENTRY))
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Voice Studio executable with Nuitka.")
    parser.add_argument(
        "--mode",
        choices=("folder", "onefile"),
        default="folder",
        help="folder = faster cached dev build, onefile = slow release build",
    )
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--clean", action="store_true", help="delete prior Nuitka outputs before build")
    args = parser.parse_args()

    print(f"Building Voice Studio ({args.mode})...")
    print("First build is slow; later folder builds reuse Nuitka/C compiler cache.\n")

    if not shutil.which("nuitka"):
        print("Installing Nuitka...")
        subprocess.check_call(["uv", "pip", "install", "nuitka", "zstandard"])

    if args.clean:
        for path in [DIST / f"{APP_NAME}.build", DIST / f"{APP_NAME}.dist", DIST / f"{APP_NAME}.onefile-build"]:
            if path.exists():
                shutil.rmtree(path)

    os.chdir(PROJECT)
    cmd = build_args(args.mode, args.jobs)
    print("Command: " + " ".join(str(a) for a in cmd))
    print()
    subprocess.check_call(cmd)

    if args.mode == "folder":
        exe = DIST / f"{APP_NAME}.dist" / f"{APP_NAME}.exe"
        print(f"\nSUCCESS: {exe}")
        print("Zip this entire .dist folder for customers.")
    else:
        exe = DIST / f"{APP_NAME}.exe"
        print(f"\nSUCCESS: {exe}")
    if exe.exists():
        print(f"Size: {exe.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
