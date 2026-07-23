"""Obfuscate source with PyArmor for fast code-protected delivery."""

import os, shutil, subprocess, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist_obf"


def main():
    print("Obfuscating with PyArmor...\n")

    if not shutil.which("pyarmor"):
        print("Installing PyArmor...")
        subprocess.check_call([sys.executable, "-m", "uv", "pip", "install", "pyarmor"])

    if DIST.exists():
        shutil.rmtree(DIST)

    # Obfuscate entire omnivoice package
    subprocess.check_call([
        sys.executable, "-m", "pyarmor", "obfuscate",
        "--output", str(DIST),
        "--recursive",
        "--no-cross-protection",
        "--restrict=0",
        str(PROJECT / "omnivoice"),
    ])

    # Copy pyproject.toml + requirements so customer can install deps
    for f in ["pyproject.toml", "uv.lock"]:
        src = PROJECT / f
        if src.exists():
            shutil.copy2(src, DIST / f)

    # Copy license client if not already in dist
    client_src = PROJECT / "omnivoice" / "_license.py"
    client_dst = DIST / "omnivoice" / "_license.py"
    if client_src.exists() and not client_dst.exists():
        shutil.copy2(client_src, client_dst)

    print(f"\nSUCCESS: {DIST}")
    print("Zip this folder and deliver to customer.")
    print("Customer runs:")
    print(f"  cd voice-studio")
    print(f"  uv sync")
    print(f"  uv run python dist_obf/omnivoice/cli/demo.py --model k2-fsa/OmniVoice")


if __name__ == "__main__":
    main()
