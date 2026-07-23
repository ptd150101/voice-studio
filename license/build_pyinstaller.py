"""Bundle with PyInstaller — faster build, encrypted bytecode."""

import os, shutil, subprocess, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist_pyinstaller"
APP_NAME = "voice-studio"
ENTRY = str(PROJECT / "omnivoice" / "cli" / "demo.py")


def _site_packages() -> Path:
    """Locate the venv site-packages directory."""
    return Path(sys.executable).parent.parent / "Lib" / "site-packages"


def _pkg_data(import_name: str) -> str:
    """Return --add-data for an installed package's data dir."""
    p = _site_packages() / import_name
    if p.is_dir():
        return f"{p};{import_name}"
    return ""


def main() -> None:
    print("Building with PyInstaller (one-dir mode)...\n")

    if DIST.exists():
        shutil.rmtree(DIST)

    add_data = [f"{PROJECT / 'omnivoice'};omnivoice"]
    for pkg in ["safehttpx", "gradio_client", "ffmpeg", "httpx"]:
        spec = f"--add-data={_site_packages() / pkg};{pkg}"
        if (_site_packages() / pkg).exists():
            add_data.append(f"{_site_packages() / pkg};{pkg}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",
        "--distpath", str(DIST),
        "--workpath", str(DIST / "_build"),
        "--specpath", str(DIST / "_build"),
        "--hidden-import=omnivoice._license",
        "--hidden-import=omnivoice.models.omnivoice",
        "--collect-all=torch",
        "--collect-all=gradio",
        "--collect-all=transformers",
        "--collect-all=accelerate",
        "--collect-all=soundfile",
        "--collect-all=librosa",
        "--collect-data=safehttpx",
        "--collect-data=gradio_client",
        "--recursive-copy-metadata=gradio",
        "--recursive-copy-metadata=transformers",
        "--recursive-copy-metadata=torch",
        "--recursive-copy-metadata=huggingface_hub",
        "--recursive-copy-metadata=tokenizers",
        "--recursive-copy-metadata=safetensors",
        "--exclude-module=matplotlib",
        "--exclude-module=test",
        "--exclude-module=pytest",
        "--windowed",
    ]
    for d in add_data:
        cmd.append(f"--add-data={d}")

    cmd.append(ENTRY)

    subprocess.check_call(cmd)

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
