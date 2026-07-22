# Build OmniVoice standalone exe with Nuitka

import os, sys, subprocess, shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist"
ENTRY = PROJECT / "omnivoice" / "cli" / "demo.py"

NUITKA_ARGS = [
    sys.executable, "-m", "nuitka",
    "--standalone",
    "--onefile",
    "--enable-plugin=torch",
    "--enable-plugin=numpy",
    "--enable-plugin=multiprocessing",
    "--windows-console-mode=disable",
    "--company-name=OmniVoice",
    "--product-name=OmniVoice",
    "--file-version=1.0.0",
    "--product-version=1.0.0",
    "--copyright=OmniVoice",
    # Include license client
    "--include-package=omnivoice",
    "--include-data-dir=omnivoice=omnivoice",
    # Suppress unnecessary
    "--noinclude-pytest-mode=nofollow",
    "--noinclude-setuptools-mode=nofollow",
    # Output
    f"--output-dir={DIST}",
    # Remove temp after build
    "--remove-output",
    str(ENTRY),
]


def main():
    print("Building OmniVoice standalone exe...")
    print("This will take 20-60 minutes depending on hardware.\n")

    if not shutil.which("nuitka"):
        print("Installing Nuitka...")
        subprocess.check_call(["uv", "pip", "install", "nuitka", "zstandard"])

    # Ensure we"re in project dir (so imports resolve)
    os.chdir(PROJECT)

    print("Command: " + " ".join(str(a) for a in NUITKA_ARGS))
    print()
    subprocess.check_call(NUITKA_ARGS)

    # Find output exe
    exe = list(DIST.glob("*.exe"))
    if exe:
        print(f"\nSUCCESS: {exe[0]}")
        print(f"Size: {exe[0].stat().st_size / 1024 / 1024:.1f} MB")
    else:
        print("\nBuild complete. Check dist/ for output.")


if __name__ == "__main__":
    main()
