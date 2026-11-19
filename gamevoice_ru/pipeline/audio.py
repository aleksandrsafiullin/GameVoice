"""
Audio output device selection and WAV playback.

Uses sounddevice (wraps PortAudio) which supports WASAPI on Windows,
giving direct device selection without changing the system default.

On Linux/macOS, uses the same API with ALSA/CoreAudio backends.
"""
from __future__ import annotations

import io
import logging
import threading
import wave
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_SOUNDDEVICE_OK: Optional[bool] = None


def _check_sounddevice() -> bool:
    global _SOUNDDEVICE_OK
    if _SOUNDDEVICE_OK is not None:
        return _SOUNDDEVICE_OK
    try:
        import sounddevice  # noqa: F401
        _SOUNDDEVICE_OK = True
    except Exception as e:
        logger.warning("[Audio] sounddevice недоступен: %s", e)
        _SOUNDDEVICE_OK = False
    return _SOUNDDEVICE_OK


# ---------------------------------------------------------------------------
# Device list
# ---------------------------------------------------------------------------

@dataclass
class AudioDevice:
    index: int
    name: str
    max_output_channels: int
    is_default: bool = False
    hostapi_name: str = ""

    def __str__(self) -> str:
        default_mark = " [по умолчанию]" if self.is_default else ""
        return f"{self.name}{default_mark}"


def list_output_devices() -> list[AudioDevice]:
    """Return all audio output devices, sorted: default first, then alphabetical."""
    if not _check_sounddevice():
        return []
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        default_idx = sd.default.device[1]  # output device index

        result = []
        for i, dev in enumerate(devices):
            if dev["max_output_channels"] > 0:
                api_name = hostapis[dev["hostapi"]]["name"] if dev["hostapi"] < len(hostapis) else ""
                result.append(AudioDevice(
                    index=i,
                    name=dev["name"],
                    max_output_channels=dev["max_output_channels"],
                    is_default=(i == default_idx),
                    hostapi_name=api_name,
                ))
        result.sort(key=lambda d: (not d.is_default, d.name.lower()))
        return result
    except Exception as e:
        logger.warning("[Audio] Не удалось получить список устройств: %s", e)
        return []


def find_device_by_name(name: str) -> Optional[AudioDevice]:
    """Find device by substring match (case-insensitive)."""
    for dev in list_output_devices():
        if name.lower() in dev.name.lower():
            return dev
    return None


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

def _wav_to_numpy(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Parse WAV bytes → (float32 array, sample_rate)."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        ch = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    if sw == 2:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if ch > 1:
        data = data.reshape(-1, ch)
    return data, sr


class AudioPlayer:
    """
    Thread-safe audio player targeting a specific output device.

    Usage:
        player = AudioPlayer(device_index=5)
        player.play(wav_bytes, volume=0.9, block=False)
        player.stop()
    """

    def __init__(self, device_index: Optional[int] = None, volume: float = 1.0):
        self._device_index = device_index
        self._volume = max(0.0, min(1.0, volume))
        self._stream: Optional[object] = None
        self._lock = threading.Lock()
        self._error: Optional[str] = None
        self._last_latency_ms: int = 0
        self._playing = False  # tracked around play/stop (Finding #8)

    @property
    def device_index(self) -> Optional[int]:
        return self._device_index

    @device_index.setter
    def device_index(self, idx: Optional[int]) -> None:
        with self._lock:
            self.stop()
            self._device_index = idx

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))

    def is_available(self) -> bool:
        return _check_sounddevice()

    def play(self, wav_bytes: bytes, volume: Optional[float] = None, block: bool = False) -> Optional[str]:
        """
        Play WAV audio. Returns error string or None.

        Tracks ``_playing`` correctly for both ``block=True`` and ``block=False``
        (Finding #8, Finding #C). For non-blocking play a watcher thread resets
        the flag when ``sd.wait()`` returns, so ``is_playing()`` eventually
        becomes False even without an explicit ``stop()`` call.
        """
        if not _check_sounddevice():
            return "sounddevice не установлен"
        try:
            import sounddevice as sd
            data, sr = _wav_to_numpy(wav_bytes)
            vol = volume if volume is not None else self._volume
            data = data * vol
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            self._playing = True
            sd.play(data, samplerate=sr, device=self._device_index, blocking=False)
            if block:
                try:
                    sd.wait()
                finally:
                    self._playing = False
            else:
                # Watcher thread resets the flag when playback finishes
                # (Finding #C: block=False must not leave _playing stuck True).
                threading.Thread(
                    target=self._wait_and_clear, daemon=True, name="AudioWatcher"
                ).start()
            return None
        except Exception as e:
            self._playing = False
            self._error = str(e)
            logger.warning("[Audio] Ошибка воспроизведения: %s", e)
            return str(e)

    def _wait_and_clear(self) -> None:
        """Background thread: wait for sd.wait() and reset _playing flag."""
        try:
            import sounddevice as sd
            sd.wait()
        except Exception:
            pass
        finally:
            self._playing = False

    def wait_until_done(self) -> None:
        """Block until the current audio finishes."""
        try:
            import sounddevice as sd
            sd.wait()
        except Exception:
            pass
        finally:
            self._playing = False

    def stop(self) -> None:
        """Interrupt any currently playing audio."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        finally:
            self._playing = False

    def is_playing(self) -> bool:
        """True if audio is currently being output (tracked via _playing flag)."""
        return self._playing

    @property
    def last_error(self) -> Optional[str]:
        return self._error


# ---------------------------------------------------------------------------
# Test phrase
# ---------------------------------------------------------------------------

TEST_PHRASE_RU = "Тестовая фраза. Колонка работает."
