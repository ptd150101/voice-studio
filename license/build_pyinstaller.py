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
        # Resolve the actual top-level package directory(ies)
        try:
            top_level = (Path(dist._path) / "top_level.txt").read_text() if dist._path else ""
        except Exception:
            top_level = ""
        pkg_dirs = top_level.strip().splitlines() if top_level else [dist_name.replace("-", "_").replace(".", "_")]
        # fallback: try the name itself
        pkg_folders = [p for p in pkg_dirs if (site_pkgs / p).is_dir()]
        if not pkg_folders:
            pkg_folders = [dist_name]
            if not (site_pkgs / dist_name).is_dir():
                continue

        collected = set()
        for f in dist.files or []:
            parts = f.parts
            if any(p.endswith(".dist-info") or p.endswith(".egg-info") for p in parts):
                continue
            if f.suffix in (".py", ".pyc", ".pyo"):
                continue
            # Try each candidate top-level directory
            src = None
            for pkg_dir_name in pkg_folders:
                candidate = site_pkgs / pkg_dir_name / str(f)
                if candidate.exists():
                    src = candidate
                    break
            if src is None:
                continue
            key = src.parent
            if key not in collected:
                collected.add(key)
                datas.append(f"{key};{pkg_dir_name}")

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
    entry_path = str(PROJECT / "omnivoice" / "cli" / "demo.py")
    spec = f"""# -*- mode: python -*-
a = Analysis(
    [{entry_path!r}],
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
