"""Bundle with PyInstaller — faster build, encrypted bytecode."""

import os, shutil, subprocess, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist_pyinstaller"
APP_NAME = "voice-studio"


def main() -> None:
    print("Building with PyInstaller (folder mode)...\n")

    if DIST.exists():
        shutil.rmtree(DIST)

    # Generate .spec to control hidden imports and data
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",
        "--distpath", str(DIST),
        "--workpath", str(DIST / "_build"),
        "--specpath", str(DIST / "_build"),
        "--add-data", f"omnivoice{os.pathsep}omnivoice",
        "--hidden-import=omnivoice._license",
        "--collect-all=torch",
        "--collect-all=gradio",
        "--collect-all=transformers",
        "--collect-all=accelerate",
        "--collect-all=soundfile",
        "--collect-all=librosa",
        "--collect-submodules=scipy",
        "--collect-submodules=sklearn",
        "--exclude-module=matplotlib",
        "--exclude-module=test",
        "--exclude-module=pytest",
        "--windowed",
        "--key", "omniv0ice-build-key",  # AES encrypt bytecode
        str(PROJECT / "omnivoice" / "cli" / "demo.py"),
    ])

    # Copy pyproject for reference
    for f in ["pyproject.toml"]:
        src = PROJECT / f
        if src.exists():
            shutil.copy2(src, DIST / APP_NAME / f)

    exe = DIST / APP_NAME / f"{APP_NAME}.exe"
    print(f"\nSUCCESS: {exe}")
    print(f"Zip entire {DIST / APP_NAME}/ folder and deliver.")
    print(f"Customer unzips and runs {APP_NAME}.exe.")


if __name__ == "__main__":
    main()
