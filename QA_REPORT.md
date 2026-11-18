# QA_REPORT.md — Independent QA Review

**Reviewer:** QA Agent (independent)
**Date:** 18 November 2026
**PR under review:** https://github.com/aleksandrsafiullin/GameVoice/pull/1
**Branch:** `cursor/gamevoice-ru-app-10be`
**Spec version:** 1.0 (sections §11 acceptance, §12 honesty rules)

---

## 1. Section 11 Acceptance Criteria — Status Table

| # | Criterion (§11) | QA Status | Evidence |
|---|---|---|---|
| 1 | App runs on Windows 11 / reproducible launch | **UNPROVEN** | EXE built by CI (5.75 MB), never launched in CI. No headless self-test mode. Needs real Windows 11 hardware. |
| 2 | Video source and subtitle rectangle selection work | **PASS by test** | `tests/qa/test_section11_black_box.py::TestBlackOrMissingSource` + existing `tests/test_config.py`. Region selector is a manual text-entry dialog (no interactive overlay). |
| 3 | Demo stream recognises Russian phrases without translation | **PASS by test** | `TestDemoEndToEndHeadless::test_demo_pipeline_speaks_at_least_one_subtitle`: real Tesseract OCR on synthetic frames + `TestDirectModeNoTranslation`. No translation module loads anywhere. |
| 4 | Static phrase not spoken repeatedly | **PASS by test** | `TestStaticSubtitleSpokenOnce::test_static_subtitle_spoken_exactly_once`: 20 identical frames → exactly 1 speak confirmed via `FakeAudioSink`. |
| 5 | New phrase and repeat-after-pause both recognised separately | **PASS by test** | `TestNewLineSpoken` + `TestSameLineAfterPauseReSpoken` (two variants: via reset and via natural expiry). |
| 6 | User can select a specific Bluetooth speaker and hear test phrase | **NEEDS USER HARDWARE** | Code path verified (`audio.py`, `AudioPlayer`, `find_device_by_name`). `test_find_nonexistent_device_returns_none` passes. No Bluetooth hardware in CI; never verified end-to-end. |
| 7 | Start/Stop buttons and visual OCR diagnostic | **PASS by code review** | `ui/main_window.py` implements Start/Stop, diagnostics notebook with OCR/stats/error tabs. Tkinter GUI not testable headlessly. |
| 8 | Source loss, OCR failure, missing model, bad audio device handled without crash | **PASS by test** | `TestMissingTTSModel`, `TestMissingAudioDevice`, `TestBlackOrMissingSource`, `TestBlackFrameNoSpeak`. All pass with no exceptions raised. |
| 9 | All simulator results backed by tests; no made-up PS5/GTA VI checks | **PASS** | No PS5/Remote Play hardware tested. Tests use synthetic frames. No claim of GTA VI or Bluetooth verification. |
| 10 | Source code, Russian instructions, deps, licenses, report attached | **PARTIAL** | Source: ✅. `README.ru.md`: ✅. `requirements.txt`: ✅. `LICENSE` (GPL-3.0): ✅. Lingolay copyright notice in source headers: **partially** — `stabilizer.py` and `tts.py` have file-level docstrings citing Lingolay; `ocr.py` has the note. `DECISIONS.md` cites correct Lingolay file paths (verified against upstream tree). REPORT.md: ✅ with honest ⚠️ markers. |

---

## 2. CI Results

| Job | Runner | Status | Notes |
|---|---|---|---|
| `test-linux` | `ubuntu-latest` | ✅ **success** | 103 existing tests pass + 4 integration tests pass |
| `build-windows` | `windows-latest` | ✅ **success** | PyInstaller builds `GameVoiceRU.exe` (5,751,546 bytes); zipped as `GameVoiceRU-windows-x64.zip` |
| Headless EXE smoke test | — | ❌ **MISSING** | EXE is built but **never launched** in CI. Finding #1 below. |

---

## 3. QA Test Results

```
tests/qa/test_section11_black_box.py  22 collected  22 passed  3 warnings
tests/ (original)                    103 collected 103 passed
tests/test_pipeline_integration.py     4 collected   4 passed
─────────────────────────────────────────────────────────────
Total                                129 collected 129 passed
```

Warnings are UserWarnings from `test_white_noise_frame_no_speak` documenting Finding #6 (Latin-only garbage not filtered). All 22 QA tests pass on Linux with Tesseract 5.3.4 + synthetic frames.

---

## 4. Findings — Ranked by Severity

### CRITICAL

---

**Finding #1 — EXE never launched in CI (no headless smoke test)**
- **Severity:** Critical / Honesty
- **File:** `.github/workflows/ci.yml` (entire `build-windows` job)
- **Evidence:** CI log confirms `GameVoiceRU.exe` built (5.75 MB) but no step runs it. REPORT.md claims "✅ Verified in CI" for the EXE — only build success was verified, not runtime.
- **Impact:** Silent breakage could exist in PyInstaller bundling (missing DLLs, import errors) that only surfaces on first launch.
- **Suggested fix:** Add a CI step: `.\dist\GameVoiceRU\GameVoiceRU.exe --self-test` (requires a `--self-test` / `--headless-check` mode in app code, or at minimum `--version`/`--help`). Since the app has no such flag, **report this as an open gap** rather than adding one to app code.

---

**Finding #2 — `WindowsOCR.recognize()`: SoftwareBitmap buffer never loaded**
- **Severity:** Critical (Windows OCR path always returns empty string)
- **File:** `gamevoice_ru/pipeline/ocr.py:278–282`
- **Evidence:**
  ```python
  buf = pil_image.tobytes()          # obtained but never used
  sb = imaging.SoftwareBitmap(       # created empty, buffer not passed
      imaging.BitmapPixelFormat.RGBA8, ...
  )
  ```
  `buf` is never passed to `SoftwareBitmap`. The bitmap is zero-filled. `recognize_async(sb)` runs OCR on a blank image → always returns `""`.
- **Impact:** Selecting `ocr_engine="winrt"` on Windows silently produces no subtitles. User sees no error.
- **Suggested fix:** Use the WinRT `SoftwareBitmap.copy_from_buffer()` or convert via `BitmapDecoder`/`InMemoryRandomAccessStream` to populate the bitmap from `buf`. Also convert PIL RGB → RGBA8 before creating the bitmap (second sub-bug: format mismatch).

---

### HIGH

---

**Finding #3 — `_stop_playback` is permanently a no-op lambda**
- **Severity:** High (fast mode does not interrupt audio; `stop()` can block 3 s)
- **File:** `gamevoice_ru/pipeline/queue_mgr.py:68, 80–86`
- **Evidence:**
  ```python
  self._stop_playback: Callable[[], None] = lambda: None  # never replaced
  ...
  if self.mode == "fast":
      self._stop_playback()  # does nothing
  ```
  No path in `TTSQueueManager` ever replaces `_stop_playback` with a call to `AudioPlayer.stop()`. `stop()` calls `self._thread.join(timeout=3.0)`, so stopping takes up to 3 s when audio is playing.
- **Impact:** "Fast" mode claim (spec §5) is false. `stop()` UX is poor.
- **Suggested fix:** Accept an `interrupt_fn: Callable[[], None]` parameter and wire it to `AudioPlayer.stop()` from `PipelineRunner._init_components`.

---

**Finding #4 — `mark_spoken()` called before TTS synthesis: silent failure on TTS error**
- **Severity:** High (subtitle permanently skipped if TTS fails)
- **File:** `gamevoice_ru/pipeline/runner.py:264–269`
- **Evidence:**
  ```python
  def _trigger_speak(self, result) -> None:
      self._stabilizer.mark_spoken(result.normalized)  # marks BEFORE synthesis
      self._stats.phrases_spoken += 1
      self._queue_mgr.enqueue(result.text, result.normalized)
  ```
  If the queue later fails to synthesize (model missing, OOM), the stabiliser has already blocked re-speaking. The subtitle is silently dropped.
- **Impact:** With a missing Piper model, the first recognised subtitle is permanently suppressed (confirmed in `TestMissingTTSModel::test_pipeline_runner_starts_without_tts_model` — log shows "В очередь" but no speak callback fires).
- **Suggested fix:** Move `mark_spoken()` to `_on_tts_speak()` callback, only after confirmed playback.

---

**Finding #5 — `FileFrameSource.grab_frame()` returns last frame indefinitely in non-loop mode**
- **Severity:** High (demo mode pipeline never self-terminates)
- **File:** `gamevoice_ru/pipeline/capture.py:143–146`
- **Evidence:**
  ```python
  self._index = (self._index + 1) % len(self._frames) if self._loop else \
      min(self._index + 1, len(self._frames) - 1)
  return frame.copy()
  ```
  When `loop=False` and `_index == len(frames) - 1`, the same last frame is returned on every call forever. `None` is never returned to signal end-of-stream.
- **Impact:** `PipelineRunner` never receives `None` from the source, so it never emits a "source ended" status. The pipeline runs at full OCR rate burning CPU on the final static frame. The integration tests only work because they sleep for a fixed duration.
- **Suggested fix:** Return `None` after the final frame when `loop=False`.

---

### MEDIUM

---

**Finding #6 — `is_garbage()` accepts Latin-only text (no Cyrillic required)**
- **Severity:** Medium (English-language game UI text, OCR artefacts could reach TTS)
- **File:** `gamevoice_ru/pipeline/stabilizer.py:88–105`
- **Evidence:** `is_garbage("Hello World Test")` returns `False`. `is_garbage("abcdefghijklmnop")` returns `False`. Demonstrated by `test_white_noise_frame_no_speak` (UserWarning in test output).
- **Impact:** If the subtitle region overlaps with English-language HUD elements (kill-counter, objective text in GTA VI), those will be passed to TTS and spoken in Russian voice. Particularly risky if the Remote Play window shows English system notifications.
- **Suggested fix:** In `is_garbage()`, add: `if not has_cyrillic: return True` (Russian-subtitle-only app has no use for Latin-only text).

---

**Finding #7 — `_effective_settle_ms()` ignores `stabilizer_settle_ms` for non-typewriter subtitles**
- **Severity:** Medium (user setting has no effect in the common case)
- **File:** `gamevoice_ru/pipeline/stabilizer.py:370–379`
- **Evidence:**
  ```python
  def _effective_settle_ms(self) -> float:
      if self.settle_ms == 0:
          return 0.0
      if not self._episode_history.incremental_mode:
          return 90.0  # hardcoded — ignores self.settle_ms
  ```
  When `incremental_mode=False` (fewer than 40% of recent episodes were typewriter-style), the configured `settle_ms` is replaced with a hardcoded 90 ms. The user's `stabilizer_settle_ms` from settings has no effect unless watching typewriter subtitles.
- **Impact:** Miscalibrated stabilisation for non-typewriter games; user tuning has no effect.
- **Suggested fix:** Return `max(90.0, self.settle_ms * 0.3)` or similar so the configured value still influences the outcome.

---

**Finding #8 — `AudioPlayer.is_playing()` always returns `False`**
- **Severity:** Medium (broken status indicator; no real impact on pipeline logic)
- **File:** `gamevoice_ru/pipeline/audio.py:187–192`
- **Evidence:**
  ```python
  def is_playing(self) -> bool:
      try:
          import sounddevice as sd
          return sd.get_stream() is not None and sd.get_stream().active
      except Exception:
          return False
  ```
  `sd.play()` uses an internal stream not accessible via `sd.get_stream()`. The latter raises `RuntimeError("No current stream")`, caught as `Exception → return False`.
- **Impact:** `is_playing()` is never `True`. The audio indicator in the GUI may not reflect actual playback state.
- **Suggested fix:** Use `sd.wait()` in a thread to track active playback, or maintain a `self._playing: bool` flag updated around `sd.play()` / `sd.wait()`.

---

**Finding #9 — OCR init failure not surfaced to UI `on_error` callback**
- **Severity:** Medium (silent failure)
- **File:** `gamevoice_ru/pipeline/runner.py:132–137`
- **Evidence:**
  ```python
  if not self._ocr.is_available():
      err = getattr(self._ocr, "init_error", None) or "OCR движок недоступен"
      logger.warning("[Pipeline] OCR: %s", err)
      # OCR errors are logged but not fatal at start
  ```
  `on_error` callback is not invoked. The pipeline silently runs with a broken OCR engine; the user sees the app running but no subtitles ever appear.
- **Suggested fix:** Call `self._emit_error(f"OCR не доступен: {err}")` so the UI's error tab shows the message.

---

**Finding #10 — `poll()` doesn't transition state to `APPEARING` on typewriter accumulation**
- **Severity:** Low / Edge case
- **File:** `gamevoice_ru/pipeline/stabilizer.py:250–270`
- **Evidence:** `poll()` returns a `should_speak=True` result with `state=STABLE`, but `_state` was set to `STABLE` without having passed through `APPEARING`. If `_change_time` was set by an earlier frame, `_is_settled()` could be True immediately on the next regular `update()` call after a poll-triggered speak, potentially triggering a double-speak within the same episode.
- **Impact:** Unlikely in practice (the SPOKEN guard protects it), but `poll()` and `update()` have inconsistent state transition paths.

---

### HONESTY / DOCUMENTATION

---

**Finding #11 — REPORT.md §11 table: `build-windows` EXE claim is overclaimed**
- **Severity:** Honesty
- **Evidence:** REPORT.md row: `"✅ Verified in CI | GitHub Actions build-windows собирает EXE"`. The EXE was built, not launched. There is no `--self-test` mode; the CI never runs the binary. The claim should read: *"EXE built in CI — runtime not verified (no self-test mode)"*.

---

**Finding #12 — REPORT.md §7 latency table: "~65-80% слов" is an estimate, not a measured result**
- **Severity:** Honesty
- **Evidence:** The integration test threshold is set to 50% (`assert recognized_count / total >= 0.5`). The 65% figure in the report is derived from a comment, not from a saved measurement. The test actually achieved 100% on the 20 sampled frames in this QA run, but the report column reads "оценочно".
- **Suggested fix:** Report actual measured values from CI, with a note that the 95% spec target requires real GTA VI subtitles.

---

**Finding #13 — DECISIONS.md claims Lingolay `src/lingolay/text/cleaning.py` is the stabiliser source**
- **Severity:** Honesty (minor inaccuracy)
- **Evidence:** Lingolay's repo structure (verified via GitHub API) contains `src/lingolay/text/cleaning.py` (21505 bytes) and separately `src/lingolay/text/stabilizer.py` (198 bytes — a stub). The actual stabiliser algorithm does appear to be in `cleaning.py`. The claimed paths exist and are plausible. Attribution is **correct** but slightly imprecise about the specific stabiliser class origin (the `cleaning.py` file contains text utilities, not a `TextStabilizer` class per se).

---

**Finding #14 — No copyright notice in `gamevoice_ru/pipeline/capture.py` docstring**
- **Severity:** Low (GPL compliance gap)
- **File:** `gamevoice_ru/pipeline/capture.py:14`
- **Evidence:** File docstring says "Adapted from Lingolay's capture/screen.py and capture/region.py patterns." but unlike `stabilizer.py`, `tts.py`, and `ocr.py`, there is no explicit Lingolay copyright attribution line. `LICENSE` file covers this globally, but per-file attribution is inconsistent.

---

## 5. What Was Not Verified (Requires User Hardware)

The following **cannot be verified** without physical access to the target hardware:

- PS5 + Remote Play: window capture, black frame detection, HDCP behaviour
- GTA VI subtitle rendering: font, position, background, actual OCR accuracy
- Bluetooth speaker: device enumeration, WASAPI device selection, audio latency
- Windows OCR (WinRT): Russian language pack, `SoftwareBitmap` pipeline (also blocked by Finding #2)
- EXE launch on real Windows 11: DLL loading, tkinter display, first-run wizard
- Latency metrics: spec targets ≤1500 ms p50 / ≤2500 ms p95 — marked `not verified on target hardware`
- Second PSN account Remote Play scenario (noted as unsupported by Sony)

None of these hardware-requiring checks are claimed as verified anywhere in this report.
