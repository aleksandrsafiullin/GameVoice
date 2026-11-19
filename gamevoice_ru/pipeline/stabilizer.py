"""
Subtitle stabilizer and de-duplicator for Russian subtitles.

Design goals (from spec section 4):
  - Static subtitle → spoken exactly once.
  - Same phrase after a pause/disappearance → spoken again (new episode).
  - Typewriter (char-by-char) subtitles → wait for completion, speak final text once.
  - OCR flicker → ignored, does not reset the settle timer.

State machine per episode:
  EMPTY → APPEARING → STABLE → SPOKEN → CLEARED

Heavily adapted from Lingolay's TextStabilizer (keremss7/lingolay, GPL-3.0).
Differences from Lingolay:
  - Removed all translation-specific logic.
  - Added SPOKEN state to track "already voiced" within the current episode.
  - Added `mark_spoken` / `is_spoken` for direct-dub path.
  - Cyrillics-aware garbage filter (no Latin-only rules applied to Russian).
  - Configurable via AppConfig.
"""
from __future__ import annotations

import collections
import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cyrillic / Russian text cleaning
# ---------------------------------------------------------------------------

_RU_GARBAGE_PATTERNS = [
    r"^\d+$",                   # pure numbers
    r"^[^\w]+$",                # only punctuation/symbols
    r"^\s*$",                   # whitespace only
]
_RU_GARBAGE_RE = [re.compile(p) for p in _RU_GARBAGE_PATTERNS]

# Characters that should not appear in subtitle text (symbols, stray ASCII control)
_STRIP_CHARS_RE = re.compile(r"[^\u0400-\u04FF\u0300-\u036F\u00C0-\u00FFa-zA-Z0-9\s.,!?;:\-—«»\"'()…\n]")


def clean_russian_text(text: str) -> str:
    """
    Clean OCR output for Russian subtitles.

    - Strip stray non-text characters.
    - Normalize whitespace.
    - Normalize em-dash / en-dash.
    - Collapse multiple newlines into one.
    - Strip surrounding noise.
    """
    if not text:
        return ""
    # Collapse Windows line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize dashes BEFORE stripping (en-dash is in the strip set)
    text = text.replace("\u2013", "\u2014")  # en-dash → em-dash
    text = text.replace("\u2014", "\u2014")  # em-dash stays
    # Remove characters outside expected subtitle set
    text = _STRIP_CHARS_RE.sub("", text)
    # Remove repeated spaces / tabs
    text = re.sub(r"[^\S\n]+", " ", text)
    # Collapse 3+ newlines → double newline
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading/trailing whitespace per line
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def normalize_for_compare(text: str) -> str:
    """Normalize to canonical form for equality checks (strip formatting, lowercase)."""
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"\s+", " ", t)
    t = t.strip(".,!?;:— ")
    return t


def is_garbage(text: str, require_cyrillic: bool = True) -> bool:
    """
    Return True if the text is clearly not a subtitle (noise, HUD, etc.).

    ``require_cyrillic=True`` (default): Latin-only strings are rejected.
    This is the correct setting for a Russian-subtitle app — English HUD text,
    stray ASCII OCR artefacts, and system notifications should never reach TTS.
    Pass ``require_cyrillic=False`` only in multi-language contexts.
    """
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if len(stripped) < 2:
        return True
    has_cyrillic = any("\u0400" <= c <= "\u04FF" for c in stripped)
    # For Russian-subtitle apps: Latin-only text is garbage (Finding #6)
    if require_cyrillic and not has_cyrillic:
        return True
    if not has_cyrillic and not any("a" <= c.lower() <= "z" for c in stripped):
        return True
    for pat in _RU_GARBAGE_RE:
        if pat.match(stripped):
            return True
    # Reject lines that are mostly numbers / punctuation
    alpha = sum(1 for c in stripped if c.isalpha())
    if len(stripped) > 3 and alpha / len(stripped) < 0.35:
        return True
    return False


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def seq_ratio(a: str, b: str) -> float:
    """Character-level sequence similarity (0–1), whitespace-stripped, lowercased."""
    ca = a.lower().replace(" ", "")
    cb = b.lower().replace(" ", "")
    if not ca:
        return 0.0
    return difflib.SequenceMatcher(None, ca, cb, autojunk=False).ratio()


def _word_jaccard(a: str, b: str) -> float:
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def is_minor_jitter(a: str, b: str) -> bool:
    """Is a→b just OCR flicker (same subtitle, slightly different reading)?"""
    if not a or not b:
        return False
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return False
    if min(la, lb) / max(la, lb) < 0.75:
        return False
    if seq_ratio(a, b) >= 0.85:
        return True
    if _word_jaccard(a, b) >= 0.65:
        return True
    return False


def is_growth(old: str, new: str) -> bool:
    """Is `new` a typewriter extension of `old`?"""
    if not old:
        return False
    # Require at least 10% longer AND at least 3 extra characters to avoid
    # spurious matches from single punctuation additions (e.g. '!' appended).
    if len(new) <= len(old) * 1.10:
        return False
    if len(new) - len(old) < 3:
        return False
    return seq_ratio(new[: len(old)], old) >= 0.80


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class SubtitleState(Enum):
    EMPTY = auto()       # No subtitle currently
    APPEARING = auto()   # Text seen but not settled yet
    STABLE = auto()      # Text has settled (ready to speak)
    SPOKEN = auto()      # Text was sent to TTS this episode
    CLEARING = auto()    # Text disappeared, waiting for new episode


@dataclass
class StabilizerResult:
    """Returned by SubtitleStabilizer.update()."""
    text: str = ""
    normalized: str = ""
    state: SubtitleState = SubtitleState.EMPTY
    should_speak: bool = False      # True once per episode when text is ready
    skip_reason: str = ""           # Why it was not sent to TTS


@dataclass
class _EpisodeHistory:
    """Tracks whether the last N episodes arrived by typewriter growth."""
    history: collections.deque = field(default_factory=lambda: collections.deque(maxlen=8))

    def record_grew(self, grew: bool) -> None:
        self.history.append(grew)

    @property
    def incremental_mode(self) -> bool:
        if len(self.history) < 3:
            return False
        return sum(self.history) / len(self.history) >= 0.4


class SubtitleStabilizer:
    """
    One instance per pipeline run.

    Call update(raw_ocr_text) every frame tick.
    Returns StabilizerResult; act when result.should_speak is True.
    Call mark_spoken(normalized) after sending to TTS.
    Call clear() when the subtitle area is empty.
    """

    _SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")

    def __init__(
        self,
        settle_ms: int = 300,
        stability_count: int = 2,
        cooldown_ms: int = 200,
        text_expiry_sec: float = 4.0,
    ):
        self.settle_ms = settle_ms
        self.stability_count = stability_count
        self.cooldown_ms = cooldown_ms
        self.text_expiry_sec = text_expiry_sec

        self._state = SubtitleState.EMPTY
        self._current_normalized: str = ""
        self._current_cleaned: str = ""
        self._consecutive_count: int = 0
        self._change_time: float = 0.0
        self._last_spoken_time: float = 0.0
        self._spoken_texts: dict[str, float] = {}   # normalized → time spoken
        self._episode_history = _EpisodeHistory()
        self._episode_grew: bool = False
        self._episode_started: bool = False

    # --- Public API ---

    def update(self, raw_text: str) -> StabilizerResult:
        """Process a new OCR result. Returns decision."""
        if is_garbage(raw_text):
            return self._make_result(should_speak=False, skip_reason="мусор OCR")

        cleaned = clean_russian_text(raw_text)
        if not cleaned:
            return self._make_result(should_speak=False, skip_reason="пустой текст после очистки")

        normalized = normalize_for_compare(cleaned)
        if len(normalized) < 2:
            return self._make_result(should_speak=False, skip_reason="слишком короткий")

        now = time.monotonic()
        return self._process(cleaned, normalized, now)

    def poll(self) -> Optional[StabilizerResult]:
        """
        Call when no OCR ran this tick (unchanged frame).

        Returns a ready result if the current text just finished settling,
        otherwise None.
        """
        if not self._current_normalized:
            return None
        if self._state not in (SubtitleState.APPEARING,):
            return None
        now = time.monotonic()
        if self._is_settled(now) and self._consecutive_count >= self.stability_count:
            if not self._is_recently_spoken(self._current_normalized) and not self._in_cooldown(now):
                self._state = SubtitleState.STABLE
                return StabilizerResult(
                    text=self._current_cleaned,
                    normalized=self._current_normalized,
                    state=SubtitleState.STABLE,
                    should_speak=True,
                )
        return None

    def clear(self) -> None:
        """Call when the subtitle area is visually blank (empty OCR result)."""
        if self._state not in (SubtitleState.EMPTY, SubtitleState.CLEARING):
            self._state = SubtitleState.CLEARING
            logger.debug("[Stabilizer] CLEARING ← %r", self._current_normalized[:40])

    def mark_spoken(self, normalized: str) -> None:
        """Record that this subtitle was enqueued for TTS."""
        self._last_spoken_time = time.monotonic()
        self._spoken_texts[normalized] = time.monotonic()
        self._state = SubtitleState.SPOKEN
        # Prune old entries
        cutoff = time.monotonic() - self.text_expiry_sec
        self._spoken_texts = {k: v for k, v in self._spoken_texts.items() if v > cutoff}

    def unmark_spoken(self, normalized: str) -> None:
        """
        Remove a subtitle from the spoken registry.

        Call this when TTS synthesis fails so the subtitle can be re-spoken
        on the next stable detection (Finding #4 — prevents permanent suppression
        on TTS failure while still preventing infinite re-enqueue loops).
        """
        self._spoken_texts.pop(normalized, None)
        if self._state == SubtitleState.SPOKEN:
            self._state = SubtitleState.APPEARING

    def reset(self) -> None:
        self._state = SubtitleState.EMPTY
        self._current_normalized = ""
        self._current_cleaned = ""
        self._consecutive_count = 0
        self._change_time = 0.0
        self._last_spoken_time = 0.0
        self._spoken_texts.clear()
        self._episode_history = _EpisodeHistory()
        self._episode_grew = False
        self._episode_started = False

    @property
    def state(self) -> SubtitleState:
        return self._state

    @property
    def current_text(self) -> str:
        return self._current_cleaned

    # --- Internal ---

    def _process(self, cleaned: str, normalized: str, now: float) -> StabilizerResult:
        same = (normalized == self._current_normalized)
        jitter = not same and is_minor_jitter(self._current_normalized, normalized)
        growing = not same and not jitter and is_growth(self._current_normalized, normalized)

        if same or jitter:
            self._consecutive_count += 1
        else:
            if growing:
                self._episode_grew = True
            else:
                # New subtitle episode
                if self._episode_started:
                    self._episode_history.record_grew(self._episode_grew)
                self._episode_started = True
                self._episode_grew = False
                self._consecutive_count = 1
                self._state = SubtitleState.APPEARING
            self._current_normalized = normalized
            self._current_cleaned = cleaned
            self._change_time = now

        if not self._current_cleaned:
            self._current_cleaned = cleaned
            self._current_normalized = normalized

        skip_reason = ""
        should_speak = False

        if self._is_recently_spoken(self._current_normalized):
            skip_reason = "уже произнесено в этом эпизоде"
        elif self._in_cooldown(now):
            skip_reason = "кулдаун"
        elif not self._is_settled(now):
            skip_reason = f"ожидание стабилизации ({self._effective_settle_ms():.0f}мс)"
        elif self._consecutive_count < self.stability_count:
            skip_reason = f"нужно {self.stability_count} последовательных кадра"
        elif self._state == SubtitleState.SPOKEN:
            skip_reason = "уже произнесено"
        else:
            self._state = SubtitleState.STABLE
            should_speak = True

        return StabilizerResult(
            text=self._current_cleaned,
            normalized=self._current_normalized,
            state=self._state,
            should_speak=should_speak,
            skip_reason=skip_reason,
        )

    def _make_result(self, should_speak: bool, skip_reason: str = "") -> StabilizerResult:
        return StabilizerResult(
            text=self._current_cleaned,
            normalized=self._current_normalized,
            state=self._state,
            should_speak=should_speak,
            skip_reason=skip_reason,
        )

    def _effective_settle_ms(self) -> float:
        # When settle_ms=0, return 0 immediately — used in tests.
        if self.settle_ms == 0:
            return 0.0
        if not self._episode_history.incremental_mode:
            # Non-typewriter (instant appearance): use at least 30% of configured
            # settle_ms, minimum 90 ms. This ensures the user setting has visible
            # effect instead of always using the hardcoded 90 ms (Finding #7).
            return max(90.0, self.settle_ms * 0.3)
        txt = self._current_normalized.rstrip(" \"'")
        if txt.endswith(self._SENTENCE_END):
            return float(self.settle_ms)
        return self.settle_ms * 2.5  # unfinished sentence → wait longer

    def _is_settled(self, now: float) -> bool:
        elapsed_ms = (now - self._change_time) * 1000.0
        return elapsed_ms >= min(self._effective_settle_ms(), 2500.0)

    def _is_recently_spoken(self, normalized: str) -> bool:
        cutoff = time.monotonic() - self.text_expiry_sec
        ts = self._spoken_texts.get(normalized)
        if ts is not None and ts > cutoff:
            return True
        # Remove stale entries
        self._spoken_texts = {k: v for k, v in self._spoken_texts.items() if v > cutoff}
        return False

    def _in_cooldown(self, now: float) -> bool:
        if self.cooldown_ms <= 0 or self._last_spoken_time == 0:
            return False
        return (now - self._last_spoken_time) * 1000 < self.cooldown_ms
