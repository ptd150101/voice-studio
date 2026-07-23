"""Bundle with PyInstaller — faster build, encrypted bytecode."""

import os, shutil, subprocess, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist_pyinstaller"
APP_NAME = "voice-studio"
ENTRY = str(PROJECT / "omnivoice" / "cli" / "demo.py")


def main() -> None:
    print("Building with PyInstaller (one-dir mode)...\n")

    if DIST.exists():
        shutil.rmtree(DIST)

    # Build spec first — then modify it to include the package properly
    spec_path = DIST / "_build" / f"{APP_NAME}.spec"
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",
        "--distpath", str(DIST),
        "--workpath", str(DIST / "_build"),
        "--specpath", str(DIST / "_build"),
        "--add-data", f"{str(PROJECT / 'omnivoice')};omnivoice",
        "--hidden-import=omnivoice._license",
        "--hidden-import=omnivoice.models.omnivoice",
        "--hidden-import=accelerate",
        "--collect-all=torch",
        "--collect-all=gradio",
        "--collect-all=transformers",
        "--collect-all=accelerate",
        "--collect-all=soundfile",
        "--collect-all=librosa",
        "--exclude-module=matplotlib",
        "--exclude-module=test",
        "--exclude-module=pytest",
        "--windowed",
        ENTRY,
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
