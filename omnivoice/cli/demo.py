#!/usr/bin/env python3
# Copyright    2026  Xiaomi Corp.        (authors:  Han Zhu)
#
# See ../../LICENSE for clarification regarding multiple authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Gradio demo for OmniVoice.

Supports voice cloning and voice design.

Usage:
    omnivoice-demo --model /path/to/checkpoint --port 8000
"""

import argparse
import io
import logging
import os
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
import numpy as np
import torch

from omnivoice import OmniVoice, OmniVoiceGenerationConfig
from omnivoice.cli.llm_normalize import (
    DEFAULTS as llm_defaults,
    describe_config as llm_describe,
    is_llm_settings_visible,
    normalize_text as llm_normalize,
    save_config as llm_save,
)
from omnivoice.cli.script_parser import (
    parse_script,
    speaker_count,
    unique_speakers,
)
from omnivoice.utils.common import get_best_device
from omnivoice.utils.lang_map import LANG_NAMES, lang_display_name


# ---------------------------------------------------------------------------
# Silence helpers — short pause between lines of the same speaker,
# longer pause between speaker changes.
# ---------------------------------------------------------------------------
def _silence(seconds: float, sampling_rate: int) -> np.ndarray:
    return np.zeros(int(seconds * sampling_rate), dtype=np.float32)


def _pause_between(
    current_speaker: Optional[int],
    next_speaker: Optional[int],
    same_speaker_pause: float,
    cross_speaker_pause: float,
) -> float:
    return (
        same_speaker_pause
        if current_speaker is not None
        and next_speaker is not None
        and current_speaker == next_speaker
        else cross_speaker_pause
    )


def _audio_to_int16(waveform_f32: np.ndarray) -> np.ndarray:
    return np.clip(waveform_f32, -1.0, 1.0).astype(np.int16)


def _concat_segments(
    segments: List[np.ndarray],
    sampling_rate: int,
    same_speaker_pause: float = 0.25,
    cross_speaker_pause: float = 0.7,
    speakers: Optional[List[int]] = None,
) -> np.ndarray:
    """Concatenate float32 audio segments with pauses between them.

    Short pause when consecutive segments share the same speaker,
    longer pause when the speaker changes.
    """
    if not segments:
        return np.zeros(0, dtype=np.float32)
    out_parts: List[np.ndarray] = []
    for i, seg in enumerate(segments):
        if seg.ndim > 1:
            seg = seg.squeeze()
        out_parts.append(seg.astype(np.float32, copy=False))
        if i < len(segments) - 1:
            pause = _pause_between(
                speakers[i] if speakers is not None else None,
                speakers[i + 1] if speakers is not None else None,
                same_speaker_pause,
                cross_speaker_pause,
            )
            out_parts.append(_silence(pause, sampling_rate))
    return np.concatenate(out_parts) if len(out_parts) > 1 else out_parts[0]


def _srt_timestamp(sample_index: int, sampling_rate: int) -> str:
    total_ms = sample_index * 1000 // sampling_rate
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def _build_srt(
    texts: List[str],
    segments: List[np.ndarray],
    speakers: List[int],
    sampling_rate: int,
    same_speaker_pause: float,
    cross_speaker_pause: float,
) -> str:
    cursor = 0
    blocks: List[str] = []
    for i, (text, seg) in enumerate(zip(texts, segments)):
        if seg.ndim > 1:
            seg = seg.squeeze()
        start = cursor
        end = start + len(seg)
        blocks.append(
            f"{i + 1}\n"
            f"{_srt_timestamp(start, sampling_rate)} --> "
            f"{_srt_timestamp(end, sampling_rate)}\n"
            f"{text}"
        )
        cursor = end
        if i < len(segments) - 1:
            pause = _pause_between(
                speakers[i],
                speakers[i + 1],
                same_speaker_pause,
                cross_speaker_pause,
            )
            cursor += int(pause * sampling_rate)
    return "\n\n".join(blocks) + "\n"


def _wav_bytes(waveform_f32: np.ndarray, sampling_rate: int) -> bytes:
    """Encode a float32 mono waveform to a 16-bit PCM WAV byte string."""
    pcm = _audio_to_int16(waveform_f32)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sampling_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _decode_ref_audio_with_ffmpeg(
    audio_path: str,
    sampling_rate: int,
    max_seconds: float = 20.0,
) -> Tuple[torch.Tensor, int]:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        audio_path,
        "-map",
        "0:a:0",
        "-t",
        str(max_seconds),
        "-ac",
        "1",
        "-ar",
        str(sampling_rate),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as e:
        raise RuntimeError("ffmpeg not found") from e
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "ffmpeg failed to decode reference audio") from e
    wav = np.frombuffer(proc.stdout, dtype="<f4").copy()
    if wav.size == 0:
        raise RuntimeError("ffmpeg decoded empty reference audio")
    return torch.from_numpy(wav).unsqueeze(0), sampling_rate


def _create_voice_clone_prompt(model, ref_audio, ref_text, preprocess_prompt=True):
    try:
        return model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=ref_text,
            preprocess_prompt=preprocess_prompt,
        )
    except Exception as first_error:
        if not isinstance(ref_audio, str):
            raise
        decoded = _decode_ref_audio_with_ffmpeg(ref_audio, model.sampling_rate)
        try:
            return model.create_voice_clone_prompt(
                ref_audio=decoded,
                ref_text=ref_text,
                preprocess_prompt=preprocess_prompt,
            )
        except Exception as second_error:
            raise RuntimeError(
                f"{type(first_error).__name__}: {first_error}; "
                f"ffmpeg fallback failed: {second_error}"
            ) from second_error


# ---------------------------------------------------------------------------
# Language list — all 600+ supported languages
# ---------------------------------------------------------------------------
_ALL_LANGUAGES = ["Auto"] + sorted(lang_display_name(n) for n in LANG_NAMES)
_AUTO = "Auto"
if gr.NO_RELOAD or "demo" not in globals():
    demo: Optional[gr.Blocks] = None


# ---------------------------------------------------------------------------
# Voice Design instruction templates
# ---------------------------------------------------------------------------
# Each option is displayed as "English / 中文".
# The model expects English for accents and Chinese for dialects.
_CATEGORIES = {
    "Gender / 性别": ["Male / 男", "Female / 女"],
    "Age / 年龄": [
        "Child / 儿童",
        "Teenager / 少年",
        "Young Adult / 青年",
        "Middle-aged / 中年",
        "Elderly / 老年",
    ],
    "Pitch / 音调": [
        "Very Low Pitch / 极低音调",
        "Low Pitch / 低音调",
        "Moderate Pitch / 中音调",
        "High Pitch / 高音调",
        "Very High Pitch / 极高音调",
    ],
    "Style / 风格": ["Whisper / 耳语"],
    "English Accent / 英文口音": [
        "American Accent / 美式口音",
        "Australian Accent / 澳大利亚口音",
        "British Accent / 英国口音",
        "Chinese Accent / 中国口音",
        "Canadian Accent / 加拿大口音",
        "Indian Accent / 印度口音",
        "Korean Accent / 韩国口音",
        "Portuguese Accent / 葡萄牙口音",
        "Russian Accent / 俄罗斯口音",
        "Japanese Accent / 日本口音",
    ],
    "Chinese Dialect / 中文方言": [
        "Henan Dialect / 河南话",
        "Shaanxi Dialect / 陕西话",
        "Sichuan Dialect / 四川话",
        "Guizhou Dialect / 贵州话",
        "Yunnan Dialect / 云南话",
        "Guilin Dialect / 桂林话",
        "Jinan Dialect / 济南话",
        "Shijiazhuang Dialect / 石家庄话",
        "Gansu Dialect / 甘肃话",
        "Ningxia Dialect / 宁夏话",
        "Qingdao Dialect / 青岛话",
        "Northeast Dialect / 东北话",
    ],
}

# Mapping from display label to the short English key the model accepts
# in `instruct`. This is what gets sent to the TTS model.
_ATTR_KEY = {
    "Male / 男": "male",
    "Female / 女": "female",
    "Child / 儿童": "child",
    "Teenager / 少年": "teenager",
    "Young Adult / 青年": "young adult",
    "Middle-aged / 中年": "middle-aged",
    "Elderly / 老年": "elderly",
    "Very Low Pitch / 极低音调": "very low pitch",
    "Low Pitch / 低音调": "low pitch",
    "Moderate Pitch / 中音调": "moderate pitch",
    "High Pitch / 高音调": "high pitch",
    "Very High Pitch / 极高音调": "very high pitch",
    "Whisper / 耳语": "whisper",
    "American Accent / 美式口音": "american accent",
    "Australian Accent / 澳大利亚口音": "australian accent",
    "British Accent / 英国口音": "british accent",
    "Chinese Accent / 中国口音": "chinese accent",
    "Canadian Accent / 加拿大口音": "canadian accent",
    "Indian Accent / 印度口音": "indian accent",
    "Korean Accent / 韩国口音": "korean accent",
    "Portuguese Accent / 葡萄牙口音": "portuguese accent",
    "Russian Accent / 俄罗斯口音": "russian accent",
    "Japanese Accent / 日本口音": "japanese accent",
    # Chinese dialects — model expects the Chinese short form.
    "Henan Dialect / 河南话": "河南话",
    "Shaanxi Dialect / 陕西话": "陕西话",
    "Sichuan Dialect / 四川话": "四川话",
    "Guizhou Dialect / 贵州话": "贵州话",
    "Yunnan Dialect / 云南话": "云南话",
    "Guilin Dialect / 桂林话": "桂林话",
    "Jinan Dialect / 济南话": "济南话",
    "Shijiazhuang Dialect / 石家庄话": "石家庄话",
    "Gansu Dialect / 甘肃话": "甘肃话",
    "Ningxia Dialect / 宁夏话": "宁夏话",
    "Qingdao Dialect / 青岛话": "青岛话",
    "Northeast Dialect / 东北话": "东北话",
}

# Lowercased set of model-accepted instruct tokens. Used to filter user
# free-form text from the "Extra instruct" field, since the model
# rejects any token outside this list (e.g. "calm", "slow", etc.).
_VALID_INSTRUCT_TOKENS = {v.lower() for v in _ATTR_KEY.values()}

# Map each model token to a category. Used to detect duplicate
# gender/age/pitch across speakers and warn the user.
_TOKEN_CATEGORY = {}
for _g in ("male", "female"):
    _TOKEN_CATEGORY[_g] = "gender"
for _a in (
    "child",
    "teenager",
    "young adult",
    "middle-aged",
    "elderly",
):
    _TOKEN_CATEGORY[_a] = "age"
for _p in (
    "very low pitch",
    "low pitch",
    "moderate pitch",
    "high pitch",
    "very high pitch",
):
    _TOKEN_CATEGORY[_p] = "pitch"
_TOKEN_CATEGORY["whisper"] = "style"
for _ac in (
    "american accent",
    "australian accent",
    "british accent",
    "chinese accent",
    "canadian accent",
    "indian accent",
    "korean accent",
    "portuguese accent",
    "russian accent",
    "japanese accent",
):
    _TOKEN_CATEGORY[_ac] = "accent"

_ATTR_INFO = {
    "English Accent / 英文口音": "Only effective for English speech.",
    "Chinese Dialect / 中文方言": "Only effective for Chinese speech.",
}

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omnivoice-demo",
        description="Launch a Gradio demo for OmniVoice.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--model",
        default="k2-fsa/OmniVoice",
        help="Model checkpoint path or HuggingFace repo id.",
    )
    parser.add_argument(
        "--device", default=None, help="Device to use. Auto-detected if not specified."
    )
    parser.add_argument("--ip", default="0.0.0.0", help="Server IP (default: 0.0.0.0).")
    parser.add_argument(
        "--port", type=int, default=7860, help="Server port (default: 7860)."
    )
    parser.add_argument(
        "--root-path",
        default=None,
        help="Root path for reverse proxy.",
    )
    parser.add_argument(
        "--share", action="store_true", default=False, help="Create public link."
    )
    parser.add_argument(
        "--no-asr",
        action="store_true",
        default=False,
        help="Skip loading Whisper ASR model. Reference text auto-transcription"
        " will be unavailable.",
    )
    parser.add_argument(
        "--asr-model",
        default="openai/whisper-large-v3-turbo",
        help="ASR model path or HuggingFace repo id"
        " (default: openai/whisper-large-v3-turbo).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Auto-reload the demo when source files change.",
    )
    return parser


# ---------------------------------------------------------------------------
# Build demo
# ---------------------------------------------------------------------------


def build_demo(
    model: OmniVoice,
    checkpoint: str,
    generate_fn=None,
) -> gr.Blocks:

    logger = logging.getLogger("omnivoice.demo")
    sampling_rate = model.sampling_rate

    # -- shared generation core --
    def _gen_core(
        text,
        language,
        ref_audio,
        instruct,
        num_step,
        guidance_scale,
        denoise,
        speed,
        duration,
        preprocess_prompt,
        postprocess_output,
        mode,
        ref_text=None,
        use_llm=True,
    ):
        if not text or not text.strip():
            return None, "Please enter the text to synthesize."

        # LLM normalize (no-op if disabled in INI / via use_llm=False).
        if use_llm:
            try:
                text = llm_normalize(text)
            except Exception as e:
                logger.warning("LLM normalize failed: %s", e)

        gen_config = OmniVoiceGenerationConfig(
            num_step=int(num_step or 32),
            guidance_scale=float(guidance_scale) if guidance_scale is not None else 2.0,
            denoise=bool(denoise) if denoise is not None else True,
            preprocess_prompt=bool(preprocess_prompt),
            postprocess_output=bool(postprocess_output),
        )

        lang = language if (language and language != "Auto") else None

        kw: Dict[str, Any] = dict(
            text=text.strip(), language=lang, generation_config=gen_config
        )

        if speed is not None and float(speed) != 1.0:
            kw["speed"] = float(speed)
        if duration is not None and float(duration) > 0:
            kw["duration"] = float(duration)

        if mode == "clone":
            if not ref_audio:
                return None, "Please upload a reference audio."
            kw["voice_clone_prompt"] = _create_voice_clone_prompt(
                model,
                ref_audio,
                ref_text,
            )

        if instruct and instruct.strip():
            kw["instruct"] = instruct.strip()

        try:
            audio = model.generate(**kw)
        except Exception as e:
            return None, f"Error: {type(e).__name__}: {e}"

        waveform = (audio[0] * 32767).astype(np.int16)
        return (sampling_rate, waveform), "Done."

    # Allow external wrappers (e.g. spaces.GPU for ZeroGPU Spaces)
    _gen = generate_fn if generate_fn is not None else _gen_core

    # =====================================================================
    # UI
    # =====================================================================
    theme = gr.themes.Soft(
        font=["Inter", "Arial", "sans-serif"],
    )
    css = """
    .gradio-container {max-width: 100% !important; font-size: 16px !important;}
    .gradio-container h1 {font-size: 1.5em !important;}
    .gradio-container .prose {font-size: 1.1em !important;}
    .compact-audio audio {height: 60px !important;}
    .compact-audio .waveform {min-height: 80px !important;}
    .clone-field, .clone-fields {display: none !important;}
    .gr-group:has(input[value="Clone"]:checked) .clone-field,
    .gr-group:has(input[value="Clone"]:checked) .clone-fields {display: block !important;}
    """
    js = r"""
    () => {
      function findSlotGroups() {
        return [...document.querySelectorAll('.gr-group')].filter((el) => {
          const text = el.innerText || '';
          return /Speaker slot #\d+/.test(text)
            && text.includes('Auto (single voice)')
            && text.includes('Clone')
            && text.includes('Attributes');
        });
      }
      function findCloneFields(slot) {
        const groups = [...slot.querySelectorAll('.gr-group')]
          .filter((el) => {
            const text = el.innerText || '';
            return text.includes('Reference Audio')
              && text.includes('Reference Text')
              && !/Speaker slot #\d+/.test(text);
          })
          .sort((a, b) => a.innerText.length - b.innerText.length);
        return groups[0];
      }
      function syncCloneFields() {
        for (const slot of findSlotGroups()) {
          const fields = findCloneFields(slot);
          if (!fields) continue;
          const clone = [...slot.querySelectorAll('input[type="radio"]')]
            .some((input) => input.value === 'Clone' && input.checked);
          fields.style.display = clone ? '' : 'none';
        }
      }
      document.addEventListener('change', () => setTimeout(syncCloneFields, 0), true);
      document.addEventListener('click', () => setTimeout(syncCloneFields, 0), true);
      new MutationObserver(syncCloneFields).observe(document.body, {
        childList: true,
        subtree: true,
      });
      setInterval(syncCloneFields, 500);
      syncCloneFields();
    }
    """

    # Reusable: language dropdown component
    def _lang_dropdown(label="Language (optional) / 语种 (可选)", value="Auto"):
        return gr.Dropdown(
            label=label,
            choices=_ALL_LANGUAGES,
            value=value,
            allow_custom_value=False,
            interactive=True,
            info="Keep as Auto to auto-detect the language.",
        )

    # Reusable: optional generation settings accordion
    def _gen_settings():
        with gr.Accordion("Generation Settings (optional)", open=False):
            sp = gr.Slider(
                0.5,
                1.5,
                value=1.0,
                step=0.05,
                label="Speed",
                info="1.0 = normal. >1 faster, <1 slower. Ignored if Duration is set.",
            )
            du = gr.Number(
                value=None,
                label="Duration (seconds)",
                info=(
                    "Leave empty to use speed."
                    " Set a fixed duration to override speed."
                ),
            )
            ns = gr.Slider(
                4,
                64,
                value=32,
                step=1,
                label="Inference Steps",
                info="Default: 32. Lower = faster, higher = better quality.",
            )
            dn = gr.Checkbox(
                label="Denoise",
                value=True,
                info="Default: enabled. Uncheck to disable denoising.",
            )
            gs = gr.Slider(
                0.0,
                4.0,
                value=2.0,
                step=0.1,
                label="Guidance Scale (CFG)",
                info="Default: 2.0.",
            )
            pp = gr.Checkbox(
                label="Preprocess Prompt",
                value=True,
                info="apply silence removal and trimming to the reference "
                "audio, add punctuation in the end of reference text (if not already)",
            )
            po = gr.Checkbox(
                label="Postprocess Output",
                value=True,
                info="Remove long silences from generated audio.",
            )
        return ns, gs, dn, sp, du, pp, po

    with gr.Blocks(title="Voice Studio") as demo:
        gr.Markdown(
            """
# Voice Studio

Create speech from text, clone voices from reference audio, and generate multi-speaker scripts.
"""
        )

        with gr.Tabs():
            # ==============================================================
            # Voice Clone
            # ==============================================================
            with gr.TabItem("Voice Clone"):
                with gr.Row():
                    with gr.Column(scale=1):
                        vc_text = gr.Textbox(
                            label="Text to Synthesize / 待合成文本",
                            lines=4,
                            placeholder="Enter the text you want to synthesize...",
                        )
                        vc_ref_audio = gr.Audio(
                            label="Reference Audio / 参考音频",
                            type="filepath",
                            elem_classes="compact-audio",
                        )
                        gr.Markdown(
                            "<span style='font-size:0.85em;color:#888;'>"
                            "Recommended: 3–10 seconds audio. "
                            "</span>"
                        )
                        vc_ref_text = gr.Textbox(
                            label=("Reference Text (optional)" " / 参考音频文本（可选）"),
                            lines=2,
                            placeholder="Transcript of the reference audio. Leave empty"
                            " to auto-transcribe via ASR models.",
                        )
                        vc_lang = _lang_dropdown("Language (optional) / 语种 (可选)")
                        (
                            vc_ns,
                            vc_gs,
                            vc_dn,
                            vc_sp,
                            vc_du,
                            vc_pp,
                            vc_po,
                        ) = _gen_settings()
                        vc_btn = gr.Button("Generate / 生成", variant="primary")
                        vc_use_llm = gr.Checkbox(
                            label="Normalize text via LLM (if enabled in Settings)",
                            value=True,
                        )
                    with gr.Column(scale=1):
                        vc_audio = gr.Audio(
                            label="Output Audio / 合成结果",
                            type="numpy",
                        )
                        vc_status = gr.Textbox(label="Status / 状态", lines=2)

                def _clone_fn(
                    text, lang, ref_aud, ref_text, ns, gs, dn, sp, du, pp, po, use_llm
                ):
                    return _gen(
                        text,
                        lang,
                        ref_aud,
                        "",
                        ns,
                        gs,
                        dn,
                        sp,
                        du,
                        pp,
                        po,
                        mode="clone",
                        ref_text=ref_text or None,
                        use_llm=bool(use_llm),
                    )

                vc_btn.click(
                    _clone_fn,
                    inputs=[
                        vc_text,
                        vc_lang,
                        vc_ref_audio,
                        vc_ref_text,
                        vc_ns,
                        vc_gs,
                        vc_dn,
                        vc_sp,
                        vc_du,
                        vc_pp,
                        vc_po,
                        vc_use_llm,
                    ],
                    outputs=[vc_audio, vc_status],
                )

            # ==============================================================
            # Script (multi-speaker)
            # ==============================================================
            with gr.TabItem("Script"):
                gr.Markdown(
                    "**Format:** each line `\\#N\\t<text>` where N is the speaker"
                    " id. Upload a `.txt` file or paste the script below.\n\n"
                    "After parsing, configure each speaker's voice in the"
                    " per-speaker section."
                )
                with gr.Row():
                    with gr.Column(scale=1):
                        sc_file = gr.File(
                            label="Script file (.txt) / 脚本文件",
                            file_types=[".txt"],
                            type="filepath",
                        )
                        sc_text = gr.Textbox(
                            label="Script text / 脚本内容",
                            lines=10,
                            placeholder=(
                                "#1\tHello world.\n"
                                "#2\tHi there, how are you?\n"
                                "#1\tI'm fine, thanks."
                            ),
                        )
                        sc_parse_btn = gr.Button(
                            "Parse Script / 解析脚本", variant="secondary"
                        )
                        sc_info = gr.Textbox(
                            label="Parse info", lines=1, interactive=False
                        )
                        sc_lang = _lang_dropdown()
                        sc_use_llm = gr.Checkbox(
                            label="Normalize text via LLM (if enabled in Settings)",
                            value=True,
                        )
                        with gr.Accordion(
                            "Generation Settings (optional)", open=False
                        ):
                            sc_ns = gr.Slider(
                                4,
                                64,
                                value=32,
                                step=1,
                                label="Inference Steps",
                            )
                            sc_gs = gr.Slider(
                                0.0,
                                4.0,
                                value=2.0,
                                step=0.1,
                                label="Guidance Scale (CFG)",
                            )
                            sc_dn = gr.Checkbox(label="Denoise", value=True)
                            sc_sp = gr.Slider(
                                0.5,
                                1.5,
                                value=1.0,
                                step=0.05,
                                label="Speed",
                            )
                            sc_du = gr.Number(
                                value=None, label="Duration (seconds)"
                            )
                            sc_pp = gr.Checkbox(
                                label="Preprocess Prompt", value=True
                            )
                            sc_po = gr.Checkbox(
                                label="Postprocess Output", value=True
                            )
                        sc_btn = gr.Button("Generate / 生成", variant="primary")
                    with gr.Column(scale=1):
                        sc_audio = gr.Audio(
                            label="Final audio (all lines merged) / 合成结果",
                            type="numpy",
                        )
                        sc_wav_dl = gr.File(
                            label="Download WAV / 下载",
                            interactive=False,
                        )
                        sc_srt_dl = gr.File(
                            label="Download SRT / 下载字幕",
                            interactive=False,
                        )
                        sc_status = gr.Textbox(
                            label="Status / 状态", lines=4
                        )

                # Per-speaker config panel — up to 6 fixed slots, hidden by default.
                # Each slot exposes (mode, ref_audio, ref_text, design_attrs).
                # Only the first N slots (matched to the parsed speakers) are used.
                sc_speakers_state = gr.State([])
                sc_spk_panel = gr.Column(visible=False)

                MAX_SPK = 6
                sc_slot_components: List[List[Any]] = []
                sc_slot_groups: List[Any] = []
                with sc_spk_panel:
                    for slot_idx in range(MAX_SPK):
                        with gr.Group(visible=False, elem_classes="speaker-slot") as slot_group:
                            gr.Markdown(f"### Speaker slot #{slot_idx + 1}")
                            mode_dd = gr.Radio(
                                choices=[
                                    ("Auto (single voice)", "Auto (single voice)"),
                                    ("Clone", "Clone"),
                                ],
                                value="Auto (single voice)",
                                label="Mode",
                            )
                            with gr.Group(elem_classes="clone-fields"):
                                ref_audio = gr.Audio(
                                    label="Reference Audio",
                                    type="filepath",
                                    elem_classes=["compact-audio", "clone-field"],
                                )
                                ref_text = gr.Textbox(
                                    label="Reference Text (optional)",
                                    lines=2,
                                    elem_classes="clone-field",
                                )
                        sc_slot_components.append(
                            [mode_dd, ref_audio, ref_text]
                        )
                        sc_slot_groups.append(slot_group)


                def _load_script(file_path, text):
                    if file_path and os.path.exists(file_path):
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                text = f.read()
                        except Exception as e:
                            return _build_load_result(
                                text, [], hide_all=True, info=f"Failed to read file: {e}"
                            )
                    items = parse_script(text)
                    speakers = unique_speakers(items)
                    info = (
                        f"Parsed {len(items)} line(s), {len(speakers)} speaker(s):"
                        f" {speakers}"
                    )
                    if not items:
                        return _build_load_result(
                            text, [], hide_all=True,
                            info=info + " — no valid lines found.",
                        )
                    n = len(speakers)
                    if n > MAX_SPK:
                        info += (
                            f" — only the first {MAX_SPK} speaker(s) will be"
                            " configurable; others use 'Auto'."
                        )
                    return _build_load_result(text, speakers, hide_all=False, info=info)

                def _build_load_result(text, speakers, hide_all, info):
                    n = len(speakers)
                    slot_updates: List[Any] = []
                    group_updates: List[Any] = []
                    for i in range(MAX_SPK):
                        slot_visible = (not hide_all) and (i < n)
                        if slot_visible:
                            # Reset mode so the first Clone click fires the JS toggle.
                            mode_upd = gr.update(
                                visible=True, value="Auto (single voice)"
                            )
                        else:
                            # When hidden, do NOT touch value at all —
                            # some Gradio 6.0 components raise on None.
                            mode_upd = gr.update(visible=False)
                        slot_updates.append(mode_upd)  # mode
                        slot_updates.append(gr.update(visible=True))  # ref_audio
                        slot_updates.append(gr.update(visible=True))  # ref_text
                        # Per-slot group updates are returned after all
                        # per-slot component updates, matching outputs order.
                        group_updates.append(gr.update(visible=slot_visible))
                    panel_update = gr.update(visible=not hide_all)
                    return (text, speakers, panel_update, info, *slot_updates, *group_updates)

                sc_parse_btn.click(
                    _load_script,
                    inputs=[sc_file, sc_text],
                    outputs=[
                        sc_text,
                        sc_speakers_state,
                        sc_spk_panel,
                        sc_info,
                    ]
                    + [c for slot in sc_slot_components for c in slot]
                    + sc_slot_groups,
                )

                def _gen_script(
                    text,
                    lang,
                    use_llm,
                    ns,
                    gs,
                    dn,
                    sp,
                    du,
                    pp,
                    po,
                    speakers,
                    *slot_values,
                ):
                    logger.info(
                        "[script] start: speakers=%s text_len=%d slot_n=%d",
                        speakers, len(text or ""), len(slot_values),
                    )
                    if not speakers:
                        return (
                            None,
                            None,
                            None,
                            "Please click 'Parse Script' first.",
                        )
                    items = parse_script(text)
                    if not items:
                        return None, None, None, "No valid #N\\t<text> lines found."

                    # slot_values = flat list of 3*MAX_SPK values
                    #   per slot: (mode, ref_audio, ref_text)
                    cfg_by_spk = {}
                    for i, spk_id in enumerate(speakers[:MAX_SPK]):
                        base = i * 3
                        cfg_by_spk[spk_id] = {
                            "mode": slot_values[base + 0],
                            "ref_audio": slot_values[base + 1],
                            "ref_text": slot_values[base + 2],
                            "design": [],
                            "extra": "",
                            "seed": None,
                        }

                    progress_lines: List[str] = []

                    # Warn if multiple speakers share the same voice
                    # attributes AND no distinct seed — they will likely
                    # sound identical.
                    _seen: Dict[Tuple, List[int]] = {}
                    for _spk, _c in cfg_by_spk.items():
                        _key = (
                            tuple(sorted(_c.get("design") or [])),
                            (_c.get("extra") or "").strip().lower(),
                        )
                        _seen.setdefault(_key, []).append(_spk)
                    for _key, _spk_list in _seen.items():
                        if len(_spk_list) > 1:
                            # Check if any of them has a distinct seed.
                            _has_unique_seed = any(
                                cfg_by_spk[s].get("seed") is not None
                                and str(cfg_by_spk[s].get("seed")).strip() != ""
                                for s in _spk_list
                            )
                            if not _has_unique_seed:
                                progress_lines.append(
                                    f"[warn] speakers {_spk_list} share the"
                                    " same voice attributes and have no"
                                    " distinct seeds — they will likely"
                                    " sound identical. Set a different"
                                    " seed (1/2/3/4) per speaker to"
                                    " differentiate."
                                )

                    # Warn per category (gender/age/pitch) if multiple
                    # speakers share the same value — they will sound
                    # alike on that axis regardless of accent/style.
                    _by_cat: Dict[str, Dict[str, List[int]]] = {}
                    for _spk, _c in cfg_by_spk.items():
                        _design_mapped = [
                            _ATTR_KEY.get(v, v).lower()
                            for v in (_c.get("design") or [])
                        ]
                        _extra_mapped = (
                            (_c.get("extra") or "").strip().lower()
                        )
                        for _tok in _design_mapped:
                            _cat = _TOKEN_CATEGORY.get(_tok)
                            if _cat:
                                _by_cat.setdefault(_cat, {}).setdefault(
                                    _tok, []
                                ).append(_spk)
                        if _extra_mapped:
                            for _tok in [
                                t.strip()
                                for t in _extra_mapped.split(",")
                                if t.strip()
                            ]:
                                _cat = _TOKEN_CATEGORY.get(_tok)
                                if _cat:
                                    _by_cat.setdefault(_cat, {}).setdefault(
                                        _tok, []
                                    ).append(_spk)
                    for _cat, _by_tok in _by_cat.items():
                        for _tok, _spk_list in _by_tok.items():
                            if len(_spk_list) > 1:
                                progress_lines.append(
                                    f"[warn] speakers {_spk_list} share the"
                                    f" same {_cat} ('{_tok}'). For distinct"
                                    f" voices, pick different {_cat}"
                                    " (e.g. male vs female, young adult"
                                    " vs elderly, high pitch vs low pitch)."
                                )

                    gen_config = OmniVoiceGenerationConfig(
                        num_step=int(ns or 32),
                        guidance_scale=float(gs) if gs is not None else 2.0,
                        denoise=bool(dn) if dn is not None else True,
                        preprocess_prompt=bool(pp),
                        postprocess_output=bool(po),
                    )
                    audio_lang = lang if (lang and lang != "Auto") else None

                    segments: List[np.ndarray] = []
                    speaker_seq: List[int] = []
                    caption_texts: List[str] = []
                    prompt_cache: Dict[int, Any] = {}

                    # Batch LLM normalize — 1 round-trip per script instead of N.
                    normalized_texts: List[str] = [it["text"] for it in items]
                    logger.info(
                        "[script] use_llm=%s items=%d", use_llm, len(items),
                    )
                    if use_llm and items:
                        try:
                            from omnivoice.cli.llm_normalize import (
                                normalize_batch as llm_normalize_batch,
                            )
                            logger.info("[script] calling normalize_batch...")
                            normalized_texts = llm_normalize_batch(
                                [it["text"] for it in items]
                            )
                            logger.info(
                                "[script] normalize_batch returned %d items",
                                len(normalized_texts),
                            )
                        except Exception as e:
                            logger.warning("LLM batch normalize failed: %s", e)

                    for idx, item in enumerate(items):
                        spk = item["speaker"]
                        line_text = normalized_texts[idx]

                        spk_cfg = cfg_by_spk.get(spk)
                        if spk_cfg is None:
                            # Speakers beyond MAX_SPK or unknown — Auto mode.
                            spk_cfg = {
                                "mode": "Auto (single voice)",
                                "ref_audio": None,
                                "ref_text": None,
                                "design": [],
                                "extra": "",
                                "seed": None,
                            }
                        mode = spk_cfg["mode"]
                        ref_aud = spk_cfg["ref_audio"]
                        ref_tx = spk_cfg["ref_text"]
                        design_vals = spk_cfg["design"] or []
                        extra_instruct = (spk_cfg.get("extra") or "").strip()
                        spk_seed = spk_cfg.get("seed")
                        if spk_seed is not None:
                            try:
                                spk_seed = int(spk_seed)
                            except (TypeError, ValueError):
                                spk_seed = None
                        # Build a single instruct string from design + extra.
                        # Map display labels to model-safe short keys.
                        # Extra instruct textbox is filtered through the
                        # model whitelist — any unknown token is dropped
                        # and warned about (the model would reject the
                        # whole instruct otherwise).
                        if design_vals or extra_instruct:
                            mapped = [
                                _ATTR_KEY.get(v, v)
                                for v in design_vals
                                if v and v != _AUTO
                            ]
                            joined = ", ".join(mapped)
                            if extra_instruct:
                                kept: List[str] = []
                                dropped: List[str] = []
                                for tok in extra_instruct.split(","):
                                    t = tok.strip().lower()
                                    if not t:
                                        continue
                                    if t in _VALID_INSTRUCT_TOKENS:
                                        kept.append(t)
                                    else:
                                        dropped.append(t.strip())
                                if kept:
                                    joined = (
                                        f"{joined}, {', '.join(kept)}"
                                        if joined
                                        else ", ".join(kept)
                                    )
                                if dropped:
                                    progress_lines.append(
                                        f"[line {idx+1}] speaker #{spk}:"
                                        f" extra voice detail ignored unknown"
                                        f" values: {dropped}"
                                    )
                            spk_instruct = joined or None
                        else:
                            spk_instruct = None
                        kw: Dict[str, Any] = dict(
                            text=line_text,
                            language=audio_lang,
                            generation_config=gen_config,
                        )
                        if sp is not None and float(sp) != 1.0:
                            kw["speed"] = float(sp)
                        if du is not None and float(du) > 0:
                            kw["duration"] = float(du)
                        # Per-speaker seed — helps Auto/Clone mode produce
                        # distinct voices across speakers with similar
                        # attributes. Model falls back to random if absent.
                        if spk_seed is not None:
                            kw["seed"] = spk_seed

                        # Radio values include the human-readable label
                        # ("Auto (single voice)"); normalize to a stable
                        # short key for branching.
                        if mode in ("Clone",) or (isinstance(mode, str) and mode.startswith("Clone")):
                            if not ref_aud:
                                progress_lines.append(
                                    f"[line {idx+1}] speaker #{spk}: no ref"
                                    " audio for Clone mode, skipping."
                                )
                                continue
                            try:
                                cached = prompt_cache.get(spk)
                                if cached is None:
                                    cached = _create_voice_clone_prompt(
                                        model,
                                        ref_aud,
                                        ref_tx or None,
                                    )
                                    prompt_cache[spk] = cached
                                kw["voice_clone_prompt"] = cached
                            except Exception as e:
                                progress_lines.append(
                                    f"[line {idx+1}] speaker #{spk}: ref audio"
                                    f" failed ({type(e).__name__}: {e}); skipping."
                                )
                                continue
                        elif mode == "Design" or (isinstance(mode, str) and mode.startswith("Design")):
                            # Design mode uses voice attributes; extra
                            # instruct (if any) is merged in.
                            if spk_instruct:
                                kw["instruct"] = spk_instruct
                        else:
                            # Auto mode — reuse a cached voice_clone_prompt
                            # per speaker so all lines of the same speaker
                            # share one voice. Synthesized on first call.
                            # Voice attributes (if any) are applied to both
                            # the seed and subsequent lines so each speaker
                            # sounds distinct.
                            cached = prompt_cache.get(spk)
                            if cached is None:
                                # Seed: use the first line's audio as a
                                # synthetic ref. This is the most reliable
                                # way to keep Auto mode consistent across
                                # all subsequent lines of this speaker.
                                try:
                                    seed_kw: Dict[str, Any] = dict(
                                        text=line_text,
                                        language=audio_lang,
                                        generation_config=gen_config,
                                    )
                                    if spk_instruct:
                                        seed_kw["instruct"] = spk_instruct
                                    if spk_seed is not None:
                                        seed_kw["seed"] = spk_seed
                                    seed_audio = model.generate(**seed_kw)
                                    seed = seed_audio[0].astype(np.float32)
                                    seed_t = torch.from_numpy(seed).unsqueeze(0)
                                    cached = model.create_voice_clone_prompt(
                                        ref_audio=(seed_t, sampling_rate),
                                        ref_text=line_text,
                                        preprocess_prompt=False,
                                    )
                                    prompt_cache[spk] = cached
                                    progress_lines.append(
                                        f"[line {idx+1}] speaker #{spk}: seeded"
                                        f" voice ({len(seed)} samples"
                                        f", attrs={spk_instruct or 'none'})"
                                    )
                                except Exception as e:
                                    progress_lines.append(
                                        f"[line {idx+1}] speaker #{spk}: seed"
                                        f" failed ({e}); falling back to"
                                        " free generation."
                                    )
                            if cached is not None:
                                kw["voice_clone_prompt"] = cached

                        try:
                            audio = model.generate(**kw)
                            seg = audio[0].astype(np.float32)
                            segments.append(seg)
                            speaker_seq.append(spk)
                            caption_texts.append(item["text"])
                            progress_lines.append(
                                f"[line {idx+1}] speaker #{spk}: ok ({len(seg)} samples)"
                            )
                        except Exception as e:
                            progress_lines.append(
                                f"[line {idx+1}] speaker #{spk}: ERROR"
                                f" {type(e).__name__}: {e}"
                            )

                    if not segments:
                        return (
                            None,
                            None,
                            None,
                            "No audio produced.\n" + "\n".join(progress_lines),
                        )

                    same_speaker_pause = 0.25
                    cross_speaker_pause = 0.7
                    final = _concat_segments(
                        segments,
                        sampling_rate,
                        same_speaker_pause=same_speaker_pause,
                        cross_speaker_pause=cross_speaker_pause,
                        speakers=speaker_seq,
                    )
                    waveform = (final * 32767).astype(np.int16)
                    sr_out = sampling_rate
                    tmp_path = os.path.join(
                        os.getcwd(), f"voice_script_{int(os.times()[4])}.wav"
                    )
                    # Persist temp files for download.
                    try:
                        import soundfile as sf
                        sf.write(tmp_path, final, sr_out)
                        wav_dl = tmp_path
                    except Exception:
                        wav_dl = None
                    try:
                        srt_path = os.path.splitext(tmp_path)[0] + ".srt"
                        srt = _build_srt(
                            caption_texts,
                            segments,
                            speaker_seq,
                            sampling_rate,
                            same_speaker_pause,
                            cross_speaker_pause,
                        )
                        with open(srt_path, "w", encoding="utf-8-sig") as f:
                            f.write(srt)
                        srt_dl = srt_path
                    except Exception:
                        srt_dl = None
                    status = (
                        f"Generated {len(segments)}/{len(items)} line(s).\n"
                        + "\n".join(progress_lines[-8:])
                    )
                    return (sr_out, waveform), wav_dl, srt_dl, status

                sc_btn.click(
                    _gen_script,
                    inputs=[
                        sc_text,
                        sc_lang,
                        sc_use_llm,
                        sc_ns,
                        sc_gs,
                        sc_dn,
                        sc_sp,
                        sc_du,
                        sc_pp,
                        sc_po,
                        sc_speakers_state,
                    ]
                    + [c for slot in sc_slot_components for c in slot],
                    outputs=[sc_audio, sc_wav_dl, sc_srt_dl, sc_status],
                )

            # ==============================================================
            # LLM Settings (hidden by default; toggle via `show_llm_settings` in omnivoice.ini)
            # ==============================================================
            if is_llm_settings_visible():
                with gr.TabItem("LLM Settings"):
                    gr.Markdown(
                        "Configure an OpenAI-compatible chat-completions endpoint"
                        " used to normalize text before TTS. The config is stored"
                        " in the app config file in the demo's working directory."
                    )
                    llm_status = gr.Textbox(
                        label="Settings status", value="Configuration loaded", interactive=False
                    )
                    llm_enabled = gr.Checkbox(
                        label="Enable LLM normalization", value=False
                    )
                    llm_base_url = gr.Textbox(
                        label="Base URL",
                        placeholder="https://opencode.ai/zen/v1",
                    )
                    llm_api_key = gr.Textbox(
                        label="API Key", type="password", placeholder="public"
                    )
                    llm_model = gr.Textbox(
                        label="Model", placeholder="deepseek-v4-flash-free"
                    )
                    llm_headers = gr.Textbox(
                        label="Extra headers (k: v, k: v)",
                        placeholder="x-opencode-client: desktop",
                        info=(
                            "Only sent to the server if non-empty and different"
                            " from the default. Leave blank for standard"
                            " OpenAI-compatible endpoints."
                        ),
                    )
                    llm_timeout = gr.Number(
                        label="Timeout (seconds)", value=60
                    )
                    llm_system = gr.Textbox(
                        label="System prompt",
                        lines=8,
                    )
                    llm_save_btn = gr.Button(
                        "Save config / 保存", variant="primary"
                    )
                    llm_reload_btn = gr.Button(
                        "Reload from disk / 重新加载"
                    )
                    llm_reset_btn = gr.Button(
                        "Reset system prompt to default / 重置系统提示"
                    )
                    llm_msg = gr.Textbox(label="Result", interactive=False)
    
                    def _llm_load_all():
                        cfg = llm_describe()
                        return (
                            cfg["enabled"],
                            cfg["base_url"],
                            cfg["api_key"],
                            cfg["model"],
                            cfg["extra_headers"],
                            float(cfg["timeout"]),
                            cfg["system_prompt"],
                            "Loaded configuration.",
                        )
    
                    def _llm_save_all(
                        enabled, base_url, api_key, model, headers, timeout, system
                    ):
                        llm_save(
                            enabled=bool(enabled),
                            base_url=base_url.strip(),
                            api_key=api_key.strip(),
                            model=model.strip(),
                            extra_headers=headers,
                            system_prompt=system,
                            timeout=float(timeout or 60),
                        )
                        return "Saved configuration."
    
                    llm_save_btn.click(
                        _llm_save_all,
                        inputs=[
                            llm_enabled,
                            llm_base_url,
                            llm_api_key,
                            llm_model,
                            llm_headers,
                            llm_timeout,
                            llm_system,
                        ],
                        outputs=[llm_msg],
                    )
                    llm_reload_btn.click(
                        _llm_load_all,
                        outputs=[
                            llm_enabled,
                            llm_base_url,
                            llm_api_key,
                            llm_model,
                            llm_headers,
                            llm_timeout,
                            llm_system,
                            llm_msg,
                        ],
                    )
    
                    def _llm_reset_prompt(
                        enabled, base_url, api_key, model, headers, timeout, system
                    ):
                        # Replace system prompt with the bundled default and
                        # persist the rest of the form unchanged.
                        default_prompt = llm_defaults["system_prompt"]
                        llm_save(
                            enabled=bool(enabled),
                            base_url=base_url.strip(),
                            api_key=api_key.strip(),
                            model=model.strip(),
                            extra_headers=headers,
                            system_prompt=default_prompt,
                            timeout=float(timeout or 60),
                        )
                        return default_prompt, "Reset prompt and saved configuration."
    
                    llm_reset_btn.click(
                        _llm_reset_prompt,
                        inputs=[
                            llm_enabled,
                            llm_base_url,
                            llm_api_key,
                            llm_model,
                            llm_headers,
                            llm_timeout,
                            llm_system,
                        ],
                        outputs=[llm_system, llm_msg],
                    )
                    # Auto-populate from current config on tab open.
                    demo.load(
                        _llm_load_all,
                        outputs=[
                            llm_enabled,
                            llm_base_url,
                            llm_api_key,
                            llm_model,
                            llm_headers,
                            llm_timeout,
                            llm_system,
                            llm_msg,
                        ],
                    )
    
    demo._custom_theme = theme
    demo._custom_css = css
    demo._custom_js = js
    return demo


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _is_gradio_reload_thread() -> bool:
    try:
        from gradio.cli.commands.reload import reload_thread
    except ImportError:
        return False
    return bool(getattr(reload_thread, "running_reload", False))


def _run_reload(argv: List[str]) -> int:
    script_path = Path(__file__).resolve()
    package_dir = script_path.parents[1]
    env = dict(os.environ)
    env["GRADIO_WATCH_DIRS"] = str(package_dir)
    env["GRADIO_WATCH_MODULE_NAME"] = "omnivoice.cli.demo"
    env["GRADIO_WATCH_DEMO_NAME"] = "demo"
    env["GRADIO_WATCH_DEMO_PATH"] = str(script_path)
    env["GRADIO_WATCH_ENCODING"] = "utf-8"
    cmd = [sys.executable, "-u", str(script_path), *argv]
    return subprocess.call(cmd, env=env)


def main(argv=None) -> int:
    global demo
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if args.reload and not os.environ.get("GRADIO_WATCH_DIRS"):
        raw_argv = [arg for arg in raw_argv if arg != "--reload"]
        return _run_reload(raw_argv)

    device = args.device or get_best_device()

    checkpoint = args.model
    if not checkpoint:
        parser.print_help()
        return 0
    if _is_gradio_reload_thread() and demo is not None:
        model = demo._custom_model
    else:
        logging.info(f"Loading model from {checkpoint}, device={device} ...")
        model = OmniVoice.from_pretrained(
            checkpoint,
            device_map=device,
            dtype=torch.float16,
            load_asr=not args.no_asr,
            asr_model_name=args.asr_model,
        )
        print("Model loaded.")

    # Create omnivoice.ini with defaults if missing, so users don't have
    # to open the LLM Settings tab and click Save just to enable LLM
    # normalization.
    try:
        llm_describe()
        logging.info("LLM config ready at %s", llm_describe().get("path"))
    except Exception as e:
        logging.warning("Could not initialize LLM config: %s", e)

    demo = build_demo(model, checkpoint)
    demo._custom_model = model

    demo.queue().launch(
        server_name=args.ip,
        server_port=args.port,
        share=args.share,
        root_path=args.root_path,
        theme=demo._custom_theme,
        css=demo._custom_css,
        js=demo._custom_js,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
