"""Bundle with PyInstaller — auto-collect ALL package data files."""

import os, shutil, subprocess, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist_pyinstaller"
APP_NAME = "voice-studio"


def _collect_data_spec() -> list:
    """
    Walk every installed distribution via importlib.metadata and collect
    NON-.py files into (src_dir, dst_prefix) pairs. This eliminates
    "version.txt not found" errors for groovy, safehttpx, etc.
    """
    from importlib.metadata import distributions

    site_pkgs = Path(sys.executable).parent.parent / "Lib" / "site-packages"
    datas = []

    for dist in distributions():
        dist_name = dist.metadata.get("Name", "")
        if not dist_name:
            continue
        # ignore eggs, zip-installed
        pkg_dir = site_pkgs / dist_name.replace("-", "_").replace(".", "_")
        if not pkg_dir.is_dir():
            # try the original name
            pkg_dir = site_pkgs / dist_name
            if not pkg_dir.is_dir():
                continue

        collected = set()
        for f in dist.files or []:
            # Keep everything that is NOT .py / .pyc / .dist-info
            parts = f.parts
            if any(p.endswith(".dist-info") or p.endswith(".egg-info") for p in parts):
                continue
            if f.suffix in (".py", ".pyc", ".pyo", ".pyd"):
                continue
            src = pkg_dir / str(f)
            if not src.exists():
                continue
            # Destination: relative path inside the package dir
            key = f.parent
            if key not in collected:
                collected.add(key)
                datas.append(f"{pkg_dir / key};{dist_name}")

    # Remove duplicates (same source path, different package detection)
    seen = set()
    unique = []
    for d in datas:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def main() -> None:
    print("Building with PyInstaller (one-dir mode)...\n")
    print("Collecting data files from all installed packages (one-time scan)...")

    if DIST.exists():
        shutil.rmtree(DIST)

    datas = _collect_data_spec()
    # Add the omnivoice package itself
    datas.append(f"{PROJECT / 'omnivoice'};omnivoice")

    # Hidden imports
    hidden = [
        "omnivoice._license",
        "omnivoice.models.omnivoice",
    ]

    # Excluded
    excludes = ["matplotlib", "test", "pytest", "setuptools"]

    # Build spec as string
    spec = f"""# -*- mode: python -*-
a = Analysis(
    [{PROJECT / 'omnivoice' / 'cli' / 'demo.py'!r}],
    pathex=[],
    binaries=[],
    datas={datas!r},
    hiddenimports={hidden!r},
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={excludes!r},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.zipped_data,
    a.binaries, a.datas,
    name={APP_NAME!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name={APP_NAME!r},
)
"""

    spec_dir = DIST / "_build"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_path = spec_dir / f"{APP_NAME}.spec"
    spec_path.write_text(spec, encoding="utf-8")

    print(f"  Collected {len(datas)} data directories")
    print(f"  Spec written to {spec_path}\n")

    subprocess.check_call([
        sys.executable, "-m", "PyInstaller",
        "--distpath", str(DIST),
        "--workpath", str(spec_dir),
        str(spec_path),
    ])

    exe = DIST / APP_NAME / f"{APP_NAME}.exe"
    print(f"\nSUCCESS: {exe}")
    print(f"Zip entire {DIST / APP_NAME}/ folder and deliver.")
    print(f"Customer unzips and runs {APP_NAME}.exe.")


if __name__ == "__main__":
    main()
