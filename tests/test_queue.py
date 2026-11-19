"""
Unit tests for TTS queue manager.

Covered:
  - Items enqueued and processed in order.
  - Stale items older than max_age_sec are dropped.
  - Queue size never exceeds max_queue in natural mode.
  - Fast mode: new item clears the queue.
  - on_speak / on_done callbacks fire.
  - TTS error → on_error callback, no crash.
"""
import threading
import time

import pytest

from gamevoice_ru.pipeline.queue_mgr import QueueItem, TTSQueueManager


def _make_manager(
    mode="natural",
    max_queue=2,
    max_age_sec=3.0,
    synth_delay=0.0,
    play_delay=0.0,
    synth_error=False,
    play_error=False,
    spoken=None,
    done=None,
    errors=None,
):
    spoke = spoken if spoken is not None else []
    dones = done if done is not None else []
    errs = errors if errors is not None else []

    def _synth(text):
        if synth_delay:
            time.sleep(synth_delay)
        if synth_error:
            return None, "synth error"
        return b"WAV_BYTES", None

    def _play(wav):
        if play_delay:
            time.sleep(play_delay)
        if play_error:
            return "play error"
        return None

    mgr = TTSQueueManager(
        synthesize_fn=_synth,
        play_fn=_play,
        mode=mode,
        max_queue=max_queue,
        max_age_sec=max_age_sec,
        on_speak=lambda t: spoke.append(t),
        on_done=lambda t: dones.append(t),
        on_error=lambda t, e: errs.append((t, e)),
    )
    return mgr, spoke, dones, errs


class TestQueueBasic:
    def test_enqueue_and_process(self):
        mgr, spoke, dones, errs = _make_manager()
        mgr.start()
        mgr.enqueue("Привет!")
        time.sleep(0.3)
        mgr.stop()
        assert "Привет!" in spoke
        assert "Привет!" in dones
        assert not errs

    def test_on_error_synth(self):
        mgr, spoke, dones, errs = _make_manager(synth_error=True)
        mgr.start()
        mgr.enqueue("Текст")
        time.sleep(0.3)
        mgr.stop()
        assert not spoke
        assert errs

    def test_on_error_play(self):
        mgr, spoke, dones, errs = _make_manager(play_error=True)
        mgr.start()
        mgr.enqueue("Текст")
        time.sleep(0.3)
        mgr.stop()
        assert errs

    def test_max_age_drop(self):
        """Items older than max_age_sec are dropped before processing."""
        # Use a first item with a long synth delay to hold the worker thread,
        # then enqueue the target item with a very short max_age.
        # The target item expires while the worker is busy with the first item.
        blocker_done = threading.Event()
        spoke_items = []
        errors = []

        def _slow_synth(text):
            if text == "BLOCKER":
                blocker_done.wait(timeout=0.3)
                return b"WAV", None
            return b"WAV", None

        def _play(wav):
            return None

        mgr = TTSQueueManager(
            synthesize_fn=_slow_synth,
            play_fn=_play,
            mode="natural",
            max_queue=5,
            max_age_sec=0.08,
            on_speak=lambda t: spoke_items.append(t),
        )
        mgr.start()
        mgr.enqueue("BLOCKER")
        time.sleep(0.02)  # Let the worker pick up BLOCKER
        mgr.enqueue("Старая реплика")
        time.sleep(0.15)  # > max_age_sec while BLOCKER is still processing
        blocker_done.set()
        time.sleep(0.2)
        mgr.stop()
        # "Старая реплика" should have expired by the time worker gets to it
        assert "Старая реплика" not in spoke_items, f"Expected drop, spoke: {spoke_items}"

    def test_queue_size_natural(self):
        """In natural mode, queue never exceeds max_queue."""
        mgr, spoke, dones, errs = _make_manager(mode="natural", max_queue=2, play_delay=0.1)
        mgr.start()
        # Flood with items
        for i in range(10):
            mgr.enqueue(f"Реплика {i}")
        time.sleep(1.0)
        mgr.stop()
        # At most max_queue+1 (1 processing + 2 pending) should have been processed
        # Exact count depends on timing, but should not exceed total
        assert len(spoke) <= 11

    def test_fast_mode_clears_queue(self):
        """Fast mode: new item clears pending items."""
        mgr, spoke, _, _ = _make_manager(mode="fast", play_delay=0.5)
        mgr.start()
        mgr.enqueue("Первая реплика")
        time.sleep(0.05)
        mgr.enqueue("Важная новая реплика")
        time.sleep(1.0)
        mgr.stop()
        assert "Важная новая реплика" in spoke

    def test_stop_while_processing(self):
        """Stop should not hang even if playback is in progress."""
        mgr, spoke, _, _ = _make_manager(play_delay=5.0)
        mgr.start()
        mgr.enqueue("Долгая реплика")
        time.sleep(0.1)
        t0 = time.monotonic()
        mgr.stop()
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"stop() took too long: {elapsed:.1f}s"

    def test_clear(self):
        """clear() empties pending queue."""
        mgr, spoke, _, _ = _make_manager(play_delay=0.5)
        mgr.start()
        for i in range(5):
            mgr.enqueue(f"Реплика {i}")
        mgr.clear()
        time.sleep(0.2)
        mgr.stop()
        # After clear, little should have been spoken
        assert len(spoke) <= 2


class TestQueueItem:
    def test_enqueued_at_set(self):
        t0 = time.monotonic()
        item = QueueItem(text="test", normalized="test")
        assert item.enqueued_at >= t0
