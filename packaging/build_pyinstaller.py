"""Build with PyInstaller — bundle with crash.log + collect-all."""

import os, shutil, subprocess, sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DIST = PROJECT / "dist_pyinstaller"
APP_NAME = "voice-studio"


def main() -> None:
    print("Building with PyInstaller...\n")

    if DIST.exists():
        shutil.rmtree(DIST)

    # Write a runtime hook that dumps stderr to crash.log
    hook_path = PROJECT / "_runtime_hook.py"
    hook_path.write_text("""\
import sys, traceback
_log = open(__file__ + ".crash.log", "w", buffering=1)
sys.stderr = _log
def _hook(tp, val, tb):
    traceback.print_exception(tp, val, tb, file=_log)
    _log.flush()
sys.excepthook = _hook
""", encoding="utf-8")

    site_pkgs = Path(sys.executable).parent.parent / "Lib" / "site-packages"

    # --add-data for packages known to need data files
    add_data_pkgs = [
        "groovy", "safehttpx", "gradio_client", "ffmpeg",
    ]
    add_data_args = []
    for pkg in add_data_pkgs:
        pkg_dir = site_pkgs / pkg
        if pkg_dir.exists():
            add_data_args.append(f"--add-data={pkg_dir};{pkg}")

    # --collect-all for packages that misbehave
    collect_all_pkgs = [
        "torch", "gradio", "transformers", "accelerate",
        "soundfile", "librosa", "groovy", "safehttpx", "gradio_client",
        "aiofiles", "anyio", "h11", "httpcore", "httpx", "sniffio",
        "starlette", "uvicorn", "websockets", "certifi",
        "charset_normalizer", "idna", "urllib3",
        "multidict", "yarl", "frozenlist", "aiosignal",
    ]
    collect_all_args = []
    for pkg in collect_all_pkgs:
        if (site_pkgs / pkg).exists():
            collect_all_args.append(f"--collect-all={pkg}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",
        "--distpath", str(DIST),
        "--workpath", str(DIST / "_build"),
        "--specpath", str(DIST / "_build"),
        "--runtime-hook", str(hook_path),
        "--add-data", f"{PROJECT / 'omnivoice'};omnivoice",
        "--hidden-import=omnivoice._license",
        "--hidden-import=omnivoice.models.omnivoice",
        "--recursive-copy-metadata=gradio",
        "--recursive-copy-metadata=transformers",
        "--recursive-copy-metadata=huggingface_hub",
        "--recursive-copy-metadata=torch",
        "--recursive-copy-metadata=tokenizers",
        "--recursive-copy-metadata=safetensors",
        "--exclude-module=matplotlib",
        "--exclude-module=test",
        "--exclude-module=pytest",
    ]
    cmd.extend(add_data_args)
    cmd.extend(collect_all_args)
    cmd.append(str(PROJECT / "omnivoice" / "cli" / "demo.py"))

    subprocess.check_call(cmd)

    # Clean up hook
    hook_path.unlink(missing_ok=True)

    # Generate customer config from build-time environment variables.
    ini_path = DIST / APP_NAME / "voice-studio.ini"
    if not ini_path.exists():
        import configparser
        cfg = configparser.ConfigParser()
        cfg["llm"] = {
            "enabled": os.environ.get("VOICE_STUDIO_LLM_ENABLED", "false"),
            "base_url": os.environ.get(
                "VOICE_STUDIO_LLM_BASE_URL", "https://opencode.ai/zen/v1"
            ),
            "api_key": os.environ.get("VOICE_STUDIO_LLM_API_KEY", ""),
            "model": os.environ.get(
                "VOICE_STUDIO_LLM_MODEL", "deepseek-v4-flash-free"
            ),
            "system_prompt": os.environ.get(
                "VOICE_STUDIO_LLM_SYSTEM_PROMPT",
                "You are a text normalizer for a high-quality text-to-speech system.",
            ),
            "timeout": os.environ.get("VOICE_STUDIO_LLM_TIMEOUT", "60"),
            "extra_headers": os.environ.get(
                "VOICE_STUDIO_LLM_EXTRA_HEADERS", "x-opencode-client: desktop"
            ),
            "show_llm_settings": os.environ.get(
                "VOICE_STUDIO_SHOW_LLM_SETTINGS", "false"
            ),
        }
        with open(str(ini_path), "w", encoding="utf-8") as f:
            cfg.write(f)
        print(f"INI: {ini_path}")

    for legal_name in ("LICENSE", "NOTICE"):
        legal_src = PROJECT / legal_name
        if legal_src.exists():
            shutil.copy2(legal_src, DIST / APP_NAME / legal_name)

    exe = DIST / APP_NAME / f"{APP_NAME}.exe"
    print(f"\nSUCCESS: {exe}")
    print(f"Zip {DIST / APP_NAME}/ and deliver.")
    print("First run will produce crash.log next to the exe if something fails.")


if __name__ == "__main__":
    main()
