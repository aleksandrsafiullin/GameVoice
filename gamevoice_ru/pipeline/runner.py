"""
Pipeline runner — ties together all components.

Frame loop:
  1. Grab frame from source.
  2. Crop subtitle region.
  3. Hash frame; skip OCR if unchanged.
  4. Run OCR.
  5. Feed to stabilizer.
  6. If stabilizer says speak → enqueue to TTS queue.
  7. TTS queue synthesizes + plays on the chosen audio device.

Runs in a background thread; main thread just calls start()/stop().
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    frames_captured: int = 0
    ocr_runs: int = 0
    ocr_skipped: int = 0
    phrases_spoken: int = 0
    phrases_dropped: int = 0
    ocr_error_count: int = 0
    tts_error_count: int = 0
    last_ocr_text: str = ""
    last_spoken_text: str = ""
    last_error: str = ""
    # Timing (ms)
    last_ocr_latency_ms: int = 0
    last_tts_latency_ms: int = 0


class PipelineRunner:
    """
    Runs the full frame→OCR→stabilizer→TTS pipeline in a background thread.

    Callbacks (called from the pipeline thread):
        on_frame(crop_bgr)         — latest subtitle crop for diagnostics
        on_ocr(raw, cleaned)       — raw + cleaned OCR text
        on_speak(text)             — text just sent to TTS
        on_error(msg)              — non-fatal error
        on_status_change(status)   — "running" / "stopped" / "source_lost"
    """

    def __init__(
        self,
        config,
        on_frame: Optional[Callable[[np.ndarray], None]] = None,
        on_ocr: Optional[Callable[[str, str], None]] = None,
        on_speak: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_status_change: Optional[Callable[[str], None]] = None,
    ):
        self._config = config
        self.on_frame = on_frame
        self.on_ocr = on_ocr
        self.on_speak = on_speak
        self.on_error = on_error
        self.on_status_change = on_status_change

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stats = PipelineStats()

        # Lazy-initialized
        self._source = None
        self._ocr = None
        self._stabilizer = None
        self._tts = None
        self._player = None
        self._queue_mgr = None
        self._last_frame_hash: Optional[int] = None

        # Maps queued text → normalized so _on_tts_error can call unmark_spoken
        # on TTS failure (Finding #4: prevents permanent suppression on TTS error).
        self._pending_normalized: dict[str, str] = {}
        self._pending_lock = threading.Lock()

    @property
    def stats(self) -> PipelineStats:
        return self._stats

    def start(self) -> Optional[str]:
        """
        Initialize components and start the pipeline thread.
        Returns an error string on initialization failure, None on success.
        """
        err = self._init_components()
        if err:
            return err
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="Pipeline")
        self._thread.start()
        self._queue_mgr.start()
        self._notify_status("running")
        return None

    def stop(self) -> None:
        self._running = False
        if self._queue_mgr:
            self._queue_mgr.stop()
        if self._thread:
            self._thread.join(timeout=5.0)
        if self._source:
            self._source.close()
            self._source = None
        if self._stabilizer:
            self._stabilizer.reset()
        self._notify_status("stopped")

    def is_running(self) -> bool:
        return self._running

    def _init_components(self) -> Optional[str]:
        cfg = self._config

        # Source
        from gamevoice_ru.pipeline.capture import make_source
        self._source = make_source(cfg)
        if not self._source.is_available():
            err = getattr(self._source, "error", None) or "Источник недоступен"
            return f"Источник видео: {err}"

        # OCR — init failure is not fatal but MUST surface to on_error (Finding #9)
        from gamevoice_ru.pipeline.ocr import make_ocr_engine
        self._ocr = make_ocr_engine(cfg.ocr_engine)
        if not self._ocr.is_available():
            err = getattr(self._ocr, "init_error", None) or "OCR движок недоступен"
            logger.warning("[Pipeline] OCR: %s", err)
            # Deferred emit so on_error fires after the runner is fully started
            self._deferred_ocr_error = f"OCR не доступен: {err}"
        else:
            self._deferred_ocr_error = None

        # Stabilizer
        from gamevoice_ru.pipeline.stabilizer import SubtitleStabilizer
        self._stabilizer = SubtitleStabilizer(
            settle_ms=cfg.stabilizer_settle_ms,
            stability_count=cfg.stabilizer_stability_count,
            cooldown_ms=cfg.stabilizer_cooldown_ms,
            text_expiry_sec=cfg.stabilizer_text_expiry_sec,
        )

        # TTS
        from gamevoice_ru.pipeline.tts import make_tts_engine
        self._tts = make_tts_engine(cfg.tts_engine, cfg.tts_voice)
        if not self._tts.is_ready():
            err = getattr(self._tts, "init_error", None) or "TTS не готов"
            logger.warning("[Pipeline] TTS: %s", err)

        # Audio player
        from gamevoice_ru.pipeline.audio import AudioPlayer, find_device_by_name
        device_idx = None
        if cfg.audio_device:
            dev = find_device_by_name(cfg.audio_device)
            if dev:
                device_idx = dev.index
        self._player = AudioPlayer(device_index=device_idx, volume=cfg.tts_volume)

        # Queue manager — wire AudioPlayer.stop() as interrupt_fn (Finding #3)
        from gamevoice_ru.pipeline.queue_mgr import TTSQueueManager

        def _synthesize(text: str):
            t0 = time.perf_counter()
            result = self._tts.synthesize(text, rate=self._config.tts_rate)
            self._stats.last_tts_latency_ms = int((time.perf_counter() - t0) * 1000)
            return result

        def _play(wav: bytes) -> Optional[str]:
            return self._player.play(wav, block=True)

        self._queue_mgr = TTSQueueManager(
            synthesize_fn=_synthesize,
            play_fn=_play,
            mode=cfg.tts_queue_mode,
            max_queue=cfg.tts_max_queue,
            max_age_sec=cfg.tts_max_age_sec,
            interrupt_fn=self._player.stop,  # real stop, not lambda:None (Finding #3)
            on_speak=self._on_tts_speak,
            on_error=self._on_tts_error,
        )
        return None

    def _run_loop(self) -> None:
        # Emit deferred OCR init error now that the runner is live (Finding #9)
        if getattr(self, "_deferred_ocr_error", None):
            self._emit_error(self._deferred_ocr_error)
            self._deferred_ocr_error = None

        cfg = self._config
        frame_interval = 1.0 / max(1.0, cfg.ocr_fps)
        consecutive_empty = 0
        consecutive_errors = 0

        while self._running:
            t_start = time.monotonic()
            try:
                frame = self._source.grab_frame()
                if frame is None:
                    consecutive_empty += 1
                    if consecutive_empty > 30:
                        self._emit_error("Источник видео: нет кадров. Проверьте Remote Play или файл.")
                        consecutive_empty = 0
                    self._stabilizer.clear()
                    time.sleep(frame_interval)
                    continue
                consecutive_empty = 0
                self._stats.frames_captured += 1

                # Crop subtitle region
                from gamevoice_ru.pipeline.capture import crop_region
                h, w = frame.shape[:2]
                crop = crop_region(frame, cfg.region, w, h)

                if self.on_frame:
                    self.on_frame(crop.copy())

                # Frame dedup
                fhash = self._hash_frame(crop)
                if fhash == self._last_frame_hash:
                    self._stats.ocr_skipped += 1
                    # Still poll the stabilizer for typewriter subtitles
                    pending = self._stabilizer.poll()
                    if pending and pending.should_speak:
                        self._trigger_speak(pending)
                else:
                    self._last_frame_hash = fhash
                    self._run_ocr(crop)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.exception("[Pipeline] Ошибка цикла: %s", e)
                if consecutive_errors > 5:
                    self._emit_error(f"Критическая ошибка конвейера: {e}")
                    break

            elapsed = time.monotonic() - t_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _hash_frame(self, frame: np.ndarray) -> int:
        # Sample every 8th pixel for speed
        small = frame[::8, ::8]
        return hash(small.tobytes())

    def _run_ocr(self, crop: np.ndarray) -> None:
        self._stats.ocr_runs += 1
        t0 = time.perf_counter()
        try:
            raw = self._ocr.recognize(crop)
        except Exception as e:
            self._stats.ocr_error_count += 1
            logger.warning("[OCR] Ошибка: %s", e)
            raw = ""
        self._stats.last_ocr_latency_ms = int((time.perf_counter() - t0) * 1000)
        self._stats.last_ocr_text = raw

        if self.on_ocr:
            from gamevoice_ru.pipeline.stabilizer import clean_russian_text
            self.on_ocr(raw, clean_russian_text(raw))

        result = self._stabilizer.update(raw)
        if result.should_speak:
            self._trigger_speak(result)

    def _trigger_speak(self, result) -> None:
        # Mark spoken immediately on enqueue to prevent duplicate enqueue from
        # the same stable frame arriving before synthesis completes.
        # On TTS failure, _on_tts_error calls unmark_spoken() to allow retry
        # (Finding #4: prevents permanent suppression without causing re-enqueue loops).
        self._stabilizer.mark_spoken(result.normalized)
        self._stats.phrases_spoken += 1
        # Keep a record for the error rollback path
        with self._pending_lock:
            self._pending_normalized[result.text] = result.normalized
        self._queue_mgr.enqueue(result.text, result.normalized)
        logger.info("[Pipeline] В очередь: %r", result.text[:60])

    def _on_tts_speak(self, text: str) -> None:
        with self._pending_lock:
            self._pending_normalized.pop(text, None)
        self._stats.last_spoken_text = text
        if self.on_speak:
            self.on_speak(text)

    def _on_tts_error(self, text: str, err: str) -> None:
        # Rollback: unmark_spoken so the subtitle can be re-spoken on next stable
        # detection (Finding #4 — no permanent suppression on TTS failure).
        with self._pending_lock:
            normalized = self._pending_normalized.pop(text, None)
        if normalized and self._stabilizer:
            self._stabilizer.unmark_spoken(normalized)
        self._stats.tts_error_count += 1
        self._emit_error(f"TTS ошибка: {err}")

    def _emit_error(self, msg: str) -> None:
        self._stats.last_error = msg
        logger.warning("[Pipeline] %s", msg)
        if self.on_error:
            self.on_error(msg)

    def _notify_status(self, status: str) -> None:
        if self.on_status_change:
            self.on_status_change(status)
