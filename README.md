# voice-studio

A multilingual voice generation studio for cloning, designing, and producing natural-sounding speech.

## About

voice-studio is a toolkit for generating speech in many languages from a short reference clip, a text description of a voice, or no prompt at all. It is aimed at researchers, product teams, and creators who need flexible zero-shot text-to-speech with fine-grained control over how a voice sounds.

## Key Features

- **Voice cloning** — Replicate a voice from a short reference audio sample.
- **Voice design** — Describe a target voice with attributes such as gender, age, pitch, accent, or style.
- **Auto voice** — Let the model pick a voice automatically when no reference is given.
- **Multilingual** — Generate speech across a broad set of languages.
- **Fine-grained control** — Inline non-verbal tags and pronunciation overrides.
- **Batch inference** — Distribute workloads across multiple GPUs.

## Installation

Clone the repository and install in editable mode:

```bash
git clone https://github.com/ptd150101/voice-studio.git
cd voice-studio
pip install -e .
```

For GPU acceleration, install a CUDA build of PyTorch that matches your driver before the editable install. See the [PyTorch install guide](https://pytorch.org/get-started/locally/) for the right command for your platform.

## Quick Start

```python
from omnivoice import OmniVoice
import soundfile as sf
import torch

model = OmniVoice.from_pretrained(
    "voice-studio/base",
    device_map="cuda:0",
    dtype=torch.float16,
)

# Voice cloning from a reference sample
audio = model.generate(
    text="Hello, this is a test of zero-shot voice cloning.",
    ref_audio="ref.wav",
    ref_text="Transcription of the reference audio.",
)

sf.write("out.wav", audio[0], 24000)
```

To design a voice from a description instead of a reference clip:

```python
audio = model.generate(
    text="Hello, this is a test of zero-shot voice design.",
    instruct="female, low pitch, british accent",
)
```

To let the model choose a voice:

```python
audio = model.generate(text="This is a sentence without any voice prompt.")
```

## CLI Tools

| Command | Description |
| --- | --- |
| `omnivoice-demo` | Launch the interactive Gradio web demo. |
| `omnivoice-infer` | Run inference on a single text. |
| `omnivoice-infer-batch` | Run batch inference across multiple GPUs. |

Launch the demo locally:

```bash
omnivoice-demo --ip 0.0.0.0 --port 8001
```

## Project Layout

```
voice-studio/
├── omnivoice/        # Core package (model, training, inference, evaluation)
├── examples/         # Training and evaluation example configs
├── docs/             # Guides on training, data prep, evaluation
├── LICENSE           # Apache-2.0
└── README.md
```

## License

Released under the [Apache License 2.0](LICENSE).

## Maintainer

Maintained by [@ptd150101](https://github.com/ptd150101).
