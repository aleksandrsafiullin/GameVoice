"""
Tests for TTS engine selection and audio output.

Where Piper models are not available (CI), tests use mocks.
"""
from __future__ import annotations

import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gamevoice_ru.pipeline.tts import (
    PIPER_RUSSIAN_VOICES,
    SILERO_VOICES,
    PiperTTS,
    SileroTTS,
    _pcm_to_wav,
    make_tts_engine,
)
from gamevoice_ru.pipeline.audio import AudioDevice, list_output_devices


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dummy_wav(sample_rate=22050, duration_sec=0.1) -> bytes:
    """Create a valid minimal WAV for playback tests."""
    n = int(sample_rate * duration_sec)
    data = np.zeros(n, dtype=np.int16)
    return _pcm_to_wav(data.tobytes(), sample_rate, 1)


# ---------------------------------------------------------------------------
# Voice catalogue
# ---------------------------------------------------------------------------

class TestVoiceCatalogue:
    def test_piper_voices_have_required_fields(self):
        for key, info in PIPER_RUSSIAN_VOICES.items():
            assert "display" in info, f"Missing display for {key}"
            assert "gender" in info, f"Missing gender for {key}"
            assert "url" in info, f"Missing url for {key}"
            assert "url_json" in info, f"Missing url_json for {key}"
            assert "license" in info, f"Missing license for {key}"

    def test_silero_voices_have_required_fields(self):
        for key, info in SILERO_VOICES.items():
            assert "display" in info
            assert "gender" in info
            assert "speaker_id" in info
            assert "license" in info

    def test_at_least_one_male_one_female_piper(self):
        genders = [v["gender"] for v in PIPER_RUSSIAN_VOICES.values()]
        assert "male" in genders
        assert "female" in genders

    def test_piper_voices_license_documented(self):
        for key, info in PIPER_RUSSIAN_VOICES.items():
            assert info["license"], f"License must be documented for {key}"


# ---------------------------------------------------------------------------
# PiperTTS (mocked)
# ---------------------------------------------------------------------------

class TestPiperTTS:
    def test_init_error_when_no_model(self, tmp_path):
        """When model files are absent, is_ready() == False and init_error is set."""
        tts = PiperTTS(voice_key="ru_RU-dmitri-medium")
        # In CI where model is not downloaded, it should not crash
        if not tts.is_ready():
            assert tts.init_error is not None
            assert len(tts.init_error) > 10

    def test_synthesize_returns_error_when_not_ready(self):
        tts = PiperTTS.__new__(PiperTTS)
        tts._voice = None
        tts._init_error = "модель не найдена"
        tts._voice_key = "ru_RU-dmitri-medium"
        wav, err = tts.synthesize("Текст", rate=1.0)
        assert wav is None
        assert err is not None

    def test_synthesize_empty_text(self):
        tts = PiperTTS.__new__(PiperTTS)
        tts._voice = MagicMock()
        tts._init_error = None
        tts._sample_rate = 22050
        wav, err = tts.synthesize("   ", rate=1.0)
        assert wav is None
        assert err is not None

    @patch("gamevoice_ru.pipeline.tts.VOICES_DIR")
    def test_make_tts_engine_piper(self, mock_dir):
        engine = make_tts_engine("piper", "ru_RU-dmitri-medium")
        assert isinstance(engine, PiperTTS)

    def test_make_tts_engine_silero(self):
        engine = make_tts_engine("silero", "silero-xenia")
        assert isinstance(engine, SileroTTS)


class TestSileroTTS:
    def test_init_error_when_no_model(self):
        tts = SileroTTS(speaker_id="xenia")
        if not tts.is_ready():
            assert tts.init_error is not None

    def test_synthesize_error_when_not_ready(self):
        tts = SileroTTS.__new__(SileroTTS)
        tts._model = None
        tts._init_error = "не найдена"
        tts._speaker_id = "xenia"
        tts._sample_rate = 24000
        wav, err = tts.synthesize("Текст")
        assert wav is None
        assert err is not None


# ---------------------------------------------------------------------------
# _pcm_to_wav
# ---------------------------------------------------------------------------

class TestPcmToWav:
    def test_valid_wav(self):
        pcm = (np.zeros(1000, dtype=np.int16)).tobytes()
        wav = _pcm_to_wav(pcm, 22050, 1)
        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.getframerate() == 22050
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2

    def test_stereo(self):
        pcm = (np.zeros(2000, dtype=np.int16)).tobytes()
        wav = _pcm_to_wav(pcm, 44100, 2)
        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.getnchannels() == 2
            assert wf.getframerate() == 44100


# ---------------------------------------------------------------------------
# AudioDevice / list
# ---------------------------------------------------------------------------

class TestAudioDevices:
    def test_list_returns_list(self):
        """Should return a list (possibly empty in CI) without crashing."""
        devices = list_output_devices()
        assert isinstance(devices, list)

    def test_device_str(self):
        d = AudioDevice(index=0, name="Bluetooth Speaker", max_output_channels=2, is_default=True)
        s = str(d)
        assert "Bluetooth Speaker" in s
        assert "по умолчанию" in s
