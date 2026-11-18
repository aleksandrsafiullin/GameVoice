"""
Russian TTS engines.

Primary: Piper TTS (piper-tts Python package) with Russian voices.
  Voices: ru_RU-dmitri-medium (male), ru_RU-irina-medium (female)
  License: CC0 / MIT (check each voice individually — see DECISIONS.md)

Fallback: Silero TTS (silero-models) — requires PyTorch.
  License: Silero model weights are under CC-BY 4.0; check terms for commercial use.

Audio output: via sounddevice (WASAPI on Windows, ALSA/JACK on Linux).

Adapted from Lingolay's PiperEngine (keremss7/lingolay, GPL-3.0).
Differences:
  - Language hardcoded to Russian (no multilingual voice selection needed).
  - sounddevice device selection for Bluetooth / specific output.
  - Silero fallback implemented.
"""
from __future__ import annotations

import io
import logging
import sys
import threading
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Voice catalogue
# ---------------------------------------------------------------------------

PIPER_RUSSIAN_VOICES: dict[str, dict] = {
    "ru_RU-dmitri-medium": {
        "display": "Дмитрий (мужской, Piper)",
        "gender": "male",
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx",
        "url_json": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json",
        "license": "CC0-1.0",
    },
    "ru_RU-irina-medium": {
        "display": "Ирина (женский, Piper)",
        "gender": "female",
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx",
        "url_json": "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json",
        "license": "CC0-1.0",
    },
}

SILERO_VOICES: dict[str, dict] = {
    "silero-aidar": {
        "display": "Айдар (мужской, Silero)",
        "gender": "male",
        "speaker_id": "aidar",
        "license": "CC-BY 4.0",
    },
    "silero-xenia": {
        "display": "Ксения (женский, Silero)",
        "gender": "female",
        "speaker_id": "xenia",
        "license": "CC-BY 4.0",
    },
    "silero-kseniya": {
        "display": "Ксения 2 (женский, Silero)",
        "gender": "female",
        "speaker_id": "kseniya",
        "license": "CC-BY 4.0",
    },
    "silero-eugene": {
        "display": "Евгений (мужской, Silero)",
        "gender": "male",
        "speaker_id": "eugene",
        "license": "CC-BY 4.0",
    },
}

VOICES_DIR = Path.home() / ".gamevoice_ru" / "voices"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class TTSEngine(ABC):
    @abstractmethod
    def synthesize(self, text: str, rate: float = 1.0) -> tuple[Optional[bytes], Optional[str]]:
        """Return (wav_bytes, error_str). One of them will be None."""
        ...

    @abstractmethod
    def is_ready(self) -> bool: ...

    def get_name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Piper TTS engine
# ---------------------------------------------------------------------------

def _pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class PiperTTS(TTSEngine):
    """
    Russian TTS via Piper.

    Adapted from Lingolay's PiperEngine (keremss7/lingolay, GPL-3.0).
    Simplified for direct Russian-only use; no multilingual branching.
    """

    def __init__(self, voice_key: str = "ru_RU-dmitri-medium"):
        self._voice_key = voice_key
        self._voice = None
        self._sample_rate = 22050
        self._init_error: Optional[str] = None
        self._load()

    def _load(self) -> None:
        try:
            import piper as _piper_pkg
            from piper.voice import PiperVoice
        except ImportError as e:
            self._init_error = (
                f"piper-tts не установлен: {e}\n"
                "Установите: pip install piper-tts"
            )
            return

        onnx, json_cfg = self._paths()
        if not onnx.exists() or not json_cfg.exists():
            self._init_error = (
                f"Модель голоса не найдена: {onnx.name}\n"
                "Запустите: python -m gamevoice_ru.pipeline.tts --download"
            )
            return

        try:
            espeak_data = Path(_piper_pkg.__file__).parent / "espeak-ng-data"
            self._voice = PiperVoice.load(
                str(onnx),
                espeak_data_dir=str(espeak_data) if espeak_data.exists() else None,
            )
            self._sample_rate = self._voice.config.sample_rate
            logger.info("[Piper] Голос загружен: %s (%dHz)", self._voice_key, self._sample_rate)
        except Exception as e:
            self._init_error = f"[Piper] Ошибка загрузки модели: {e}"
            logger.error(self._init_error)

    def _paths(self) -> tuple[Path, Path]:
        base = VOICES_DIR / self._voice_key / self._voice_key
        return base.with_suffix(".onnx"), Path(str(base) + ".onnx.json")

    def is_ready(self) -> bool:
        return self._voice is not None

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def get_name(self) -> str:
        info = PIPER_RUSSIAN_VOICES.get(self._voice_key, {})
        return info.get("display", f"Piper {self._voice_key}")

    def synthesize(self, text: str, rate: float = 1.0) -> tuple[Optional[bytes], Optional[str]]:
        if self._voice is None:
            return None, self._init_error or "Piper не инициализирован"
        text = text.strip()
        if not text:
            return None, "Пустой текст"
        # rate: 1.0 = normal; > 1.0 = faster → Piper length_scale < 1.0
        length_scale = max(0.4, min(2.0, 1.0 / rate))
        try:
            from piper.config import SynthesisConfig
            try:
                syn_cfg = SynthesisConfig(length_scale=length_scale)
            except TypeError:
                syn_cfg = SynthesisConfig()
        except Exception:
            syn_cfg = None

        start = time.perf_counter()
        try:
            pcm_parts = []
            sr = self._sample_rate
            ch = 1
            kw = {"syn_config": syn_cfg} if syn_cfg is not None else {}
            for chunk in self._voice.synthesize(text, **kw):
                pcm_parts.append(chunk.audio_int16_bytes)
                sr = chunk.sample_rate
                ch = chunk.sample_channels
            wav = _pcm_to_wav(b"".join(pcm_parts), sr, ch)
            latency = int((time.perf_counter() - start) * 1000)
            logger.debug("[Piper] %d символов → %dмс", len(text), latency)
            return wav, None
        except Exception as e:
            return None, f"[Piper] Ошибка синтеза: {e}"


# ---------------------------------------------------------------------------
# Silero TTS engine (PyTorch-based fallback)
# ---------------------------------------------------------------------------

class SileroTTS(TTSEngine):
    """
    Russian TTS via Silero.
    License of model weights: CC-BY 4.0 — see DECISIONS.md.
    """

    _MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
    _MODEL_PATH = VOICES_DIR / "silero" / "v4_ru.pt"

    def __init__(self, speaker_id: str = "xenia", sample_rate: int = 24000):
        self._speaker_id = speaker_id
        self._sample_rate = sample_rate
        self._model = None
        self._init_error: Optional[str] = None
        self._load()

    def _load(self) -> None:
        try:
            import torch
        except ImportError:
            self._init_error = "PyTorch не установлен. pip install torch --index-url https://download.pytorch.org/whl/cpu"
            return
        if not self._MODEL_PATH.exists():
            self._init_error = (
                f"Silero модель не найдена: {self._MODEL_PATH}\n"
                "Скачайте: python -m gamevoice_ru.pipeline.tts --download silero"
            )
            return
        try:
            import torch
            self._model = torch.package.PackageImporter(str(self._MODEL_PATH)).load_pickle("tts_models", "model")
            self._model.to("cpu")
            logger.info("[Silero] Модель загружена.")
        except Exception as e:
            self._init_error = f"[Silero] Ошибка загрузки: {e}"

    def is_ready(self) -> bool:
        return self._model is not None

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def get_name(self) -> str:
        info = SILERO_VOICES.get(f"silero-{self._speaker_id}", {})
        return info.get("display", f"Silero {self._speaker_id}")

    def synthesize(self, text: str, rate: float = 1.0) -> tuple[Optional[bytes], Optional[str]]:
        if self._model is None:
            return None, self._init_error or "Silero не готов"
        text = text.strip()
        if not text:
            return None, "Пустой текст"
        try:
            import torch
            audio = self._model.apply_tts(
                text=text,
                speaker=self._speaker_id,
                sample_rate=self._sample_rate,
            )
            pcm = (audio.numpy() * 32767).astype(np.int16).tobytes()
            wav = _pcm_to_wav(pcm, self._sample_rate, 1)
            return wav, None
        except Exception as e:
            return None, f"[Silero] Ошибка синтеза: {e}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_tts_engine(engine: str = "piper", voice: str = "ru_RU-dmitri-medium") -> TTSEngine:
    if engine == "silero":
        # Parse "silero-xenia" → speaker_id="xenia"
        speaker = voice.replace("silero-", "") if voice.startswith("silero-") else "xenia"
        return SileroTTS(speaker_id=speaker)
    return PiperTTS(voice_key=voice)


# ---------------------------------------------------------------------------
# Voice model downloader
# ---------------------------------------------------------------------------

def download_piper_voice(voice_key: str, progress_cb=None) -> Optional[str]:
    """
    Download a Piper voice to VOICES_DIR.
    Returns error string on failure, None on success.
    """
    info = PIPER_RUSSIAN_VOICES.get(voice_key)
    if not info:
        return f"Голос не найден в каталоге: {voice_key}"
    dest_dir = VOICES_DIR / voice_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    for url_key, suffix in [("url", ".onnx"), ("url_json", ".onnx.json")]:
        url = info[url_key]
        dest = dest_dir / f"{voice_key}{suffix}"
        if dest.exists():
            if progress_cb:
                progress_cb(f"{dest.name}: уже скачан.")
            continue
        err = _download_file(url, dest, progress_cb)
        if err:
            return err
    return None


def _download_file(url: str, dest: Path, progress_cb=None) -> Optional[str]:
    try:
        import urllib.request

        def reporthook(count, block_size, total_size):
            if progress_cb and total_size > 0:
                pct = min(100, int(count * block_size * 100 / total_size))
                progress_cb(f"Скачивание {dest.name}: {pct}%")

        if progress_cb:
            progress_cb(f"Скачивание {dest.name}…")
        urllib.request.urlretrieve(url, dest, reporthook=reporthook)
        if progress_cb:
            progress_cb(f"{dest.name}: готово.")
        return None
    except Exception as e:
        return f"Ошибка загрузки {url}: {e}"


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    engine = "piper"
    if len(sys.argv) > 2 and sys.argv[1] == "--download":
        engine = sys.argv[2]

    if engine == "piper":
        for vk in PIPER_RUSSIAN_VOICES:
            print(f"Скачиваю {vk}…")
            err = download_piper_voice(vk, progress_cb=print)
            if err:
                print("ОШИБКА:", err)
            else:
                print("OK")
    else:
        print("Использование: python -m gamevoice_ru.pipeline.tts --download [piper|silero]")
