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

    # Generate default INI alongside the exe
    ini_path = DIST / APP_NAME / "omnivoice.ini"
    if not ini_path.exists():
        import configparser
        cfg = configparser.ConfigParser()
        cfg["llm"] = {
            "enabled": "false",
            "base_url": "https://opencode.ai/zen/v1",
            "api_key": "public",
            "model": "deepseek-v4-flash-free",
            "extra_headers": "x-opencode-client: desktop",
            "system_prompt": (
                "You are a text normalizer for a high-quality text-to-speech system.\n"
                "Your job is to LIGHTLY format the text — DO NOT rewrite, paraphrase,\n"
                "shorten, or merge sentences. Keep the speaker's exact words.\n"
                "Rules:\n"
                "1. Output the same language as the input — never translate.\n"
                "2. Expand symbols to spoken form.\n"
                "3. Insert punctuation that improves prosody.\n"
                "4. Keep proper nouns verbatim. Do NOT add commentary.\n"
                "5. Keep the output roughly the same length as the input.\n"
                "6. Return ONLY the normalized text, no quotes, no prefix."
            ),
            "timeout": "60",
        }
        with open(str(ini_path), "w", encoding="utf-8") as f:
            cfg.write(f)
        print(f"INI: {ini_path}")

    exe = DIST / APP_NAME / f"{APP_NAME}.exe"
    print(f"\nSUCCESS: {exe}")
    print(f"Zip {DIST / APP_NAME}/ and deliver.")
    print("First run will produce crash.log next to the exe if something fails.")


if __name__ == "__main__":
    main()
