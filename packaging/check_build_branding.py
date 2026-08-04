"""Minimal branding and packaging self-check."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = (ROOT / "omnivoice" / "cli" / "demo.py").read_text(encoding="utf-8")
NORMALIZE = (ROOT / "omnivoice" / "cli" / "llm_normalize.py").read_text(
    encoding="utf-8"
)
BUILDER = (ROOT / "packaging" / "build_pyinstaller.py").read_text(
    encoding="utf-8"
)

for customer_ui_text in (
    'title="OmniVoice',
    "🔊 OmniVoice",
    'prog="omnivoice-demo"',
    "Launch a Gradio demo for OmniVoice",
):
    assert customer_ui_text not in DEMO, customer_ui_text

assert 'DEFAULT_INI_NAME = "voice-studio.ini"' in NORMALIZE
assert '"voice-studio.ini"' in BUILDER
assert '"omnivoice.ini"' not in BUILDER
assert "100.75.219.28" not in BUILDER
assert "sk-3eaa33d61eca1bd0" not in BUILDER
assert (ROOT / "LICENSE").is_file()
assert (ROOT / "NOTICE").is_file()

DIST = ROOT / "dist_pyinstaller" / "voice-studio"
if DIST.exists():
    for name in ("voice-studio.exe", "voice-studio.ini", "LICENSE", "NOTICE"):
        assert (DIST / name).exists(), name
    assert not (DIST / "omnivoice.ini").exists()

print("Voice Studio branding check passed.")
