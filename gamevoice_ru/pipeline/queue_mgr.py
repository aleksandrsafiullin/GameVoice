"""
TTS queue manager.

Implements two queue modes (spec section 5):
  - "natural": current phrase plays to completion; queued stale lines are dropped.
  - "fast": incoming line can interrupt current playback.

Queue constraints:
  - Max 2 pending items.
  - Items older than max_age_sec are dropped silently.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    text: str
    normalized: str
    enqueued_at: float = field(default_factory=time.monotonic)


class TTSQueueManager:
    """
    Runs TTS synthesis + playback in a background thread.

    Usage:
        mgr = TTSQueueManager(synthesize_fn, play_fn)
        mgr.start()
        mgr.enqueue("Текст субтитров")
        mgr.stop()

    Callbacks:
        on_speak(text)       — called just before playback starts
        on_done(text)        — called after playback finishes
        on_error(text, err)  — called on TTS or playback error
    """

    def __init__(
        self,
        synthesize_fn: Callable[[str], tuple[Optional[bytes], Optional[str]]],
        play_fn: Callable[[bytes], Optional[str]],
        mode: str = "natural",
        max_queue: int = 2,
        max_age_sec: float = 3.0,
        interrupt_fn: Optional[Callable[[], None]] = None,
        on_speak: Optional[Callable[[str], None]] = None,
        on_done: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str, str], None]] = None,
    ):
        self._synthesize = synthesize_fn
        self._play = play_fn
        self.mode = mode
        self.max_queue = max_queue
        self.max_age_sec = max_age_sec
        self.on_speak = on_speak
        self.on_done = on_done
        self.on_error = on_error

        self._queue: list[QueueItem] = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_text: str = ""
        # Wire to AudioPlayer.stop() so fast mode actually interrupts audio (Finding #3).
        # Defaults to a no-op if no interrupt_fn is provided.
        self._stop_playback: Callable[[], None] = interrupt_fn or (lambda: None)

    def set_interrupt_fn(self, fn: Callable[[], None]) -> None:
        """Replace the stop-playback hook (e.g. AudioPlayer.stop). Thread-safe."""
        self._stop_playback = fn

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True, name="TTSQueue")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._event.set()
        self._stop_playback()   # immediately interrupts ongoing playback
        if self._thread:
            self._thread.join(timeout=3.0)
        with self._lock:
            self._queue.clear()

    def enqueue(self, text: str, normalized: str = "") -> None:
        """Add a subtitle line to the queue."""
        now = time.monotonic()
        item = QueueItem(text=text, normalized=normalized or text.lower(), enqueued_at=now)
        with self._lock:
            if self.mode == "fast":
                self._stop_playback()   # interrupt current playback immediately
                self._queue.clear()
                self._queue.append(item)
            else:
                # natural: drop oldest if over capacity
                self._drop_stale(now)
                if len(self._queue) >= self.max_queue:
                    dropped = self._queue.pop(0)
                    logger.debug("[Queue] Дроп устаревшей реплики: %r", dropped.text[:40])
                self._queue.append(item)
        self._event.set()

    def clear(self) -> None:
        """Clear the queue and stop current playback."""
        with self._lock:
            self._queue.clear()
        self._stop_playback()

    @property
    def current_text(self) -> str:
        return self._current_text

    @property
    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)

    def _drop_stale(self, now: float) -> None:
        cutoff = now - self.max_age_sec
        before = len(self._queue)
        self._queue = [item for item in self._queue if item.enqueued_at >= cutoff]
        dropped = before - len(self._queue)
        if dropped:
            logger.debug("[Queue] Дроп %d устаревших реплик", dropped)

    def _worker(self) -> None:
        while self._running:
            self._event.wait(timeout=0.1)
            self._event.clear()
            while True:
                item = None
                with self._lock:
                    self._drop_stale(time.monotonic())
                    if self._queue:
                        item = self._queue.pop(0)
                if item is None:
                    break
                self._process(item)

    def _process(self, item: QueueItem) -> None:
        # Check age before synthesizing
        age = time.monotonic() - item.enqueued_at
        if age > self.max_age_sec:
            logger.debug("[Queue] Пропуск: реплика слишком старая (%.1fс): %r", age, item.text[:40])
            return

        self._current_text = item.text
        logger.debug("[Queue] Синтез: %r", item.text[:60])

        wav, err = self._synthesize(item.text)
        if err or wav is None:
            logger.warning("[Queue] Ошибка синтеза: %s", err)
            if self.on_error:
                self.on_error(item.text, err or "Неизвестная ошибка")
            return

        if self.on_speak:
            self.on_speak(item.text)

        err = self._play(wav)
        if err:
            logger.warning("[Queue] Ошибка воспроизведения: %s", err)
            if self.on_error:
                self.on_error(item.text, err)
        else:
            if self.on_done:
                self.on_done(item.text)

        self._current_text = ""
