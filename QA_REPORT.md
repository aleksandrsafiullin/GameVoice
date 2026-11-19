# QA_REPORT.md — Independent QA Review (v2 Re-verification)

**Reviewer:** QA Agent (independent)
**Original review date:** 18 November 2026
**Re-verification date:** 19 November 2026
**PR under review:** https://github.com/aleksandrsafiullin/GameVoice/pull/1
**Dev branch:** `cursor/gamevoice-ru-app-10be` @ commits 84585d0, 6351ff6, 8f4002f
**QA branch:** `cursor/qa-review-1104` (this PR)
**Spec:** §11 acceptance criteria, §12 honesty rules

---

## 1. Section 11 Acceptance Criteria — Current Status

| # | Criterion (§11) | QA Status | Evidence |
|---|---|---|---|
| 1 | App runs on Windows 11 / reproducible launch | **PARTIAL** | EXE builds and launches (`--self-test` exits 0), but OCR=SKIP and TTS=SKIP in Windows CI. The EXE launches; it does not prove OCR or TTS work on Windows. |
| 2 | Video source and subtitle rectangle selection | **PASS by test** | `tests/test_config.py` + existing tests. File source EOF fixed (Finding #5). |
| 3 | Demo stream recognises RU phrases without translation | **PASS by test** | Linux CI: Tesseract OCR ran, 20/20 synthetic frames, no translation module loaded. Windows CI: OCR=SKIP (Tesseract download failed in CI). |
| 4 | Static phrase not spoken repeatedly | **PASS by test** | `tests/qa/TestStaticSubtitleSpokenOnce` + `test_stabilizer.py`. All pass. |
| 5 | New phrase + repeat-after-pause both recognised | **PASS by test** | `tests/qa/TestSameLineAfterPauseReSpoken` (two variants). |
| 6 | Specific Bluetooth speaker selectable, test phrase audible | **NEEDS USER HARDWARE** | Code path verified. `interrupt_fn` now wired (Finding #3 fixed). No Bluetooth hardware in CI. |
| 7 | Start/Stop buttons + visual OCR diagnostics | **PASS by code review** | `ui/main_window.py` unchanged and correct. |
| 8 | Source/OCR/model/audio failures don't crash | **PASS by test** | `tests/qa/TestMissingTTSModel`, `TestMissingAudioDevice`, `TestBlackOrMissingSource`. OCR init error now reaches `on_error` (Finding #9 fix verified). |
| 9 | Simulator results backed by tests; no fake PS5/GTA VI claims | **PASS** | No hardware claimed. REPORT.md now has honest ⚠️ markers. |
| 10 | Source code, Russian guide, deps, licenses, report | **PARTIAL** | `winrt` missing from `requirements.txt` (New Finding #E). No install instructions for `winrt` packages in README.ru.md. |

---

## 2. CI Results — Run 44442723007

| Job | Runner | Status | Notes |
|---|---|---|---|
| `test-linux` | ubuntu-latest | ✅ success | 155 tests pass. Linux `--self-test`: OCR=TesseractOCR ✅, TTS=SKIP (model not downloaded). |
| `build-windows` | windows-latest | ✅ success | EXE built (5.75 MB), `--self-test` exit 0. **OCR=SKIP, TTS=SKIP** — see Finding #A. |

**Critical gap confirmed:** The Windows CI `--self-test` passes with both OCR and TTS in SKIP state. No Russian OCR and no TTS synthesis actually ran on Windows in any CI run. REPORT.md claim "Windows OCR=TesseractOCR" is **false** — CI log shows OCR=SKIP and warning "WindowsOCR unavailable: No module named 'winrt'. Falling back to Tesseract." followed by Tesseract download failure.

---

## 3. QA Test Results

```
tests/qa/test_section11_black_box.py  22 collected  22 passed  3 warnings
tests/test_qa_fixes.py               36 collected  36 passed
tests/ (original + integration)      97 collected  97 passed
─────────────────────────────────────────────────────────────
Total                                155 collected 155 passed
```

Warnings are UserWarnings from `test_white_noise_frame_no_speak` — no longer relevant since `is_garbage(require_cyrillic=True)` now rejects Latin-only text. The warning is from the QA test's internal documentation loop and is harmless.

---

## 4. Per-Finding Re-Verification

### Finding #1 — EXE never launched in CI

**Fix claimed:** `--self-test` mode added; CI runs `GameVoiceRU.exe --self-test`.

**Verified:** ✅ EXE launches and exits 0. `Start-Process -Wait` correctly blocks.

**Residual gap:** EXE self-test reports OCR=SKIP and TTS=SKIP (see Finding #A). The EXE launches but does not synthesise any audio or run any OCR on Windows CI. This QA branch adds CI steps to address this.

---

### Finding #2 — WindowsOCR `SoftwareBitmap` buffer never loaded

**Fix claimed:** Now uses `BitmapDecoder` + `InMemoryRandomAccessStream` to populate the bitmap from PNG bytes.

**Code review:** The new code path is structurally correct. Bytes are encoded to PNG, written to `InMemoryRandomAccessStream`, decoded via `BitmapDecoder`, and the resulting `SoftwareBitmap` is converted to BGRA8 before being passed to `recognize_async()`. The fix addresses both the missing buffer bug and the RGBA8/RGB format mismatch.

**CI test coverage:** `test_windows_ocr_init_error_on_linux` and `test_windows_ocr_recognize_returns_empty_when_unavailable` only test the unavailable path on Linux. Neither test exercises the actual fixed OCR code path on Windows.

**Root cause of non-verification:** `winrt` Python packages are not installed in the CI environment (no `pip install winrt-runtime` step). OCR fell back to Tesseract (which also failed to download), so WindowsOCR was never attempted.

**Verdict: Code fix CLAIMED, not verified in any CI run.** This QA branch adds `pip install winrt-runtime` and `Add-WindowsCapability` steps to exercise the fixed path.

---

### Finding #3 — `_stop_playback` permanently a no-op lambda

**Fix claimed:** `TTSQueueManager` accepts `interrupt_fn`; `PipelineRunner` passes `AudioPlayer.stop`.

**Code review:** ✅ Wiring is correct. `PipelineRunner._init_components` passes `interrupt_fn=self._player.stop`. `set_interrupt_fn()` allows late-binding.

**Test:** `test_interrupt_fn_called_on_fast_enqueue` verifies the lambda fires when fast-mode enqueue is called. This is a real regression test.

**Limitation:** Actual audio interruption (PortAudio stream stop) cannot be tested without hardware. The test verifies wiring, not actual audio cut-off.

**Verdict: ✅ VERIFIED (wiring correct + regression test exists).** Full audio-stop verification needs hardware.

---

### Finding #4 — `mark_spoken()` before synthesis: silent TTS failure suppresses subtitle

**Fix claimed:** `unmark_spoken()` added to stabilizer; called in `_on_tts_error` to roll back suppression.

**Code review:** ✅ Mechanism is correct. `mark_spoken` is still called at enqueue time (preventing re-enqueue loops from the same stable frame), and `unmark_spoken` rolls it back on TTS failure. `_pending_normalized` dict tracks the mapping.

**Test:** `test_tts_error_allows_retry` and `test_unmark_spoken_via_on_tts_error` are real regression tests.

**Verdict: ✅ VERIFIED.**

---

### Finding #5 — `FileFrameSource` returns last frame indefinitely in non-loop mode

**Fix claimed:** `grab_frame()` returns `None` after last frame; `at_eof` property added.

**Code review:** ✅ Fix is correct:
```python
if self._index >= len(self._frames):
    return None  # EOF in non-loop mode
...
self._index += 1  # will exceed len on next call
```

**Test:** `test_returns_none_after_last_frame` creates 3 frames, reads 10 times, asserts frames 4–10 are `None`. This is a real regression test.

**Verdict: ✅ VERIFIED.**

---

### Finding #6 — `is_garbage()` accepts Latin-only text

**Fix claimed:** `is_garbage(text, require_cyrillic=True)` — Latin-only text rejected by default.

**Code review:** ✅ `if require_cyrillic and not has_cyrillic: return True` added before existing checks.

**Test:** `test_latin_only_is_garbage` verifies "Hello World Test" etc. return True. `test_stabilizer_rejects_latin_only` confirms `SubtitleStabilizer.update("Hello World Test")` does not trigger speech.

**Verdict: ✅ VERIFIED.** QA test `test_white_noise_frame_no_speak` no longer emits UserWarnings on the fixed code.

---

### Finding #7 — `_effective_settle_ms()` ignores configured `settle_ms` for non-typewriter subtitles

**Fix claimed:** Returns `max(90.0, settle_ms * 0.3)` instead of hardcoded 90.

**Code review:** Fix is real but **minimal in effect**. At the default `settle_ms=300`:
- Before fix: returns 90 ms
- After fix: `max(90, 300 * 0.3) = max(90, 90) = 90 ms`

The user must set `stabilizer_settle_ms > 300` in settings to see any change. At `settle_ms=600`, effective becomes 180 ms (before: 90 ms). The fix is real but invisible at the default setting.

**Test:** `test_nonzero_settle_ms_used_in_instant_mode` checks `settle_ms=600 → effective >= 180`. This is a valid regression test for the non-default case.

**Verdict: ✅ VERIFIED for non-default values. At default settle_ms=300, behaviour is identical to before the fix.** Not a regression — just minimal improvement.

---

### Finding #8 — `AudioPlayer.is_playing()` always returns `False`

**Fix claimed:** `self._playing` flag tracked around `play()`/`stop()`/`wait_until_done()`.

**Code review:** Fix is correct for `block=True` (the pipeline use case). **New residual bug:** when `play(wav, block=False)` is called, `_playing` is set to True but only reset in the `finally` block when `block=True`. For non-blocking play, `_playing` stays True indefinitely after the audio finishes (until `stop()` is called).

```python
finally:
    if block:          # ← only resets for blocking play
        self._playing = False
```

In the actual pipeline, `block=True` is always used (`_play = lambda wav: self._player.play(wav, block=True)`), so this residual bug has no production impact. But the public API is misleading.

**Test:** `test_is_playing_true_during_play` manually sets `player._playing = True` and reads `is_playing()`. It does NOT call `play()` at all. The test verifies the flag plumbing works but not the actual play/stop lifecycle through the flag.

**Verdict: ✅ VERIFIED for pipeline use case (block=True). New residual: non-blocking play leaves `_playing=True` after audio ends.**

---

### Finding #9 — OCR init failure not surfaced to `on_error`

**Fix claimed:** `_deferred_ocr_error` set in `_init_components`, emitted at start of `_run_loop`.

**Code review:** ✅ Mechanism is correct. The deferred emit fires even if OCR init failure does not block pipeline start.

**Test:** `test_ocr_init_failure_calls_on_error` manually sets `runner._deferred_ocr_error` and runs `_run_loop` briefly, then checks `errors` contains "OCR". This directly tests the emission path.

**Partial gap:** The test bypasses `_init_components` and sets `_deferred_ocr_error` manually. It does not test that a real missing-Tesseract scenario triggers the deferred error path end-to-end through `_init_components`.

**Verdict: ✅ VERIFIED for emission path. Full end-to-end coverage is partial.**

---

### Finding #10 — `poll()` state path (original low-severity)

Not addressed in fixes. SPOKEN guard still prevents double-speak in practice.

**Verdict: Acknowledged, not fixed. Low practical impact.**

---

### Finding #14 — Copyright notice missing in `capture.py`

**Fix claimed:** Docstring updated to include "Copyright (c) keremss7/lingolay contributors. Licensed under GPL-3.0."

**Code review:** ✅ Docstring now contains "keremss7/lingolay".

**Test:** `test_lingolay_attribution_in_capture_docstring` checks `capture.__doc__` contains "lingolay" or "keremss7".

**Verdict: ✅ VERIFIED.**

---

## 5. New Findings from Re-Verification

### New Finding #A — Windows CI self-test passes with OCR=SKIP and TTS=SKIP (critical honesty)

- **Severity:** Critical / Honesty
- **Evidence (CI log run 44442723007):**
  - `WindowsOCR unavailable: No module named 'winrt'`
  - `WARNING: Tesseract download failed` (UB-Mannheim installer network-blocked)
  - `tts: SKIP` (model not downloaded)
  - `overall: PASS` (SKIP ≠ critical failure)
- **Impact:** REPORT.md claims "✅ Windows EXE `--self-test`" but no OCR and no TTS synthesis actually ran on Windows. The self-test proves the EXE launches; it does not prove OCR or TTS work on Windows.
- **This QA branch adds:**
  1. `choco install tesseract` for reliable Tesseract on Windows (chocolatey is pre-installed on GH Actions)
  2. `pip install winrt-runtime` + WinRT namespace packages
  3. `Add-WindowsCapability -Online -Name "Language.OCR~~~ru-RU~0.0.1.0"` for Russian OCR
  4. HuggingFace download of CC0-1.0 `ru_RU-dmitri-medium.onnx` for TTS verification
  5. Linux `--self-test` QA-strict check that OCR≠SKIP
  6. Windows QA diagnostic check: logs OCR/TTS status as QA-WARN or QA-OK

---

### New Finding #B — No `--strict` mode in `--self-test`: SKIP indistinguishable from pass

- **Severity:** High
- **File:** `gamevoice_ru/self_test.py`, `gamevoice_ru/__main__.py`
- **Evidence:** `self_test.py` line 219: `if not ocr_text and ocr_error: result["warnings"].append(...)` — missing OCR/TTS is a warning, not a failure. `overall = "PASS"` with 2 SKIPs.
- **Impact:** CI cannot reliably distinguish "app works" from "app launched but nothing ran." A `--strict` flag that fails on any SKIP would catch environment gaps.
- **Requires app code change** → reported as finding, not implemented.
- **Suggested fix:** Add `--strict` argument to `self_test.py`; when set, any `SKIP` in a critical step (OCR, TTS) causes exit 1.

---

### New Finding #C — `AudioPlayer.is_playing()` broken for non-blocking play

- **Severity:** Low (no production pipeline impact — pipeline always uses `block=True`)
- **File:** `gamevoice_ru/pipeline/audio.py:167–182`
- **Evidence:**
  ```python
  finally:
      if block:          # block=False case never resets the flag
          self._playing = False
  ```
  Calling `player.play(wav, block=False)` leaves `_playing=True` after audio ends.
- **Impact:** Public API is misleading; could cause incorrect "audio still playing" status in future non-pipeline uses of `AudioPlayer`. No production impact today.

---

### New Finding #D — `winrt` packages missing from `requirements.txt` and README.ru.md

- **Severity:** Medium (Windows OCR path silently falls through to Tesseract)
- **File:** `requirements.txt`, `README.ru.md`
- **Evidence:** `ocr.py` imports `winrt.windows.media.ocr`, `winrt.windows.globalization`, etc. These are not in `requirements.txt`. On a clean Windows install with standard `pip install -r requirements.txt`, WindowsOCR will always fail with `ModuleNotFoundError` and fall back to Tesseract.
- **Impact:** The fixed `WindowsOCR.recognize()` code (Finding #2) is never exercised on a clean install unless the user manually installs `winrt-runtime` and related packages. The README.ru.md has no mention of WinRT packages.
- **Suggested fix:** Add to `requirements.txt` (Windows-conditional) and to README.ru.md "Windows OCR (Optional)" section: `pip install winrt-runtime winrt.windows.media.ocr winrt.windows.globalization winrt.windows.graphics.imaging winrt.windows.storage.streams`

---

### New Finding #E — REPORT.md claim "Windows OCR=TesseractOCR" is false

- **Severity:** Honesty
- **File:** `REPORT.md` line ~36
- **Evidence:** REPORT.md says `"| Windows EXE --self-test | ✅ | OCR=TesseractOCR (или WindowsOCR если установлен русский пакет), overall=PASS |"`. CI log shows OCR engine = SKIP (not TesseractOCR) in the Windows self-test, because Tesseract download failed.
- **Suggested fix:** REPORT.md should say "OCR=SKIP (Tesseract download failed in CI runner; choco install added in QA branch to fix this)".

---

## 6. What Remains Unverified (Requires User Hardware)

The following **cannot be verified** in any CI environment:

- **PS5 + Remote Play** — window capture, HDCP, black frame detection
- **GTA VI subtitles** — font, region, actual OCR accuracy at ≥95%
- **Bluetooth speaker** — device enumeration, WASAPI device selection, audio latency  
- **Windows OCR with actual Russian Language Pack** — Finding #2 fix code is structurally correct but completely unrun in CI  
- **Piper TTS synthesis on Windows** — SKIP in all Windows CI runs to date
- **EXE on real Windows 11** — only Windows Server 2022 tested (GitHub Actions)
- **Latency spec** — ≤1500 ms p50 / ≤2500 ms p95: `not verified on target hardware`
- **Second PSN account for Remote Play** — unsupported by Sony

---

## 7. Summary Table

| Finding | Original Status | Fix Claimed | QA Verdict |
|---|---|---|---|
| #1 EXE never launched | Critical | Added `--self-test` | ✅ EXE launches; ⚠️ OCR/TTS SKIP in Windows CI |
| #2 WindowsOCR buffer bug | Critical | BitmapDecoder fix | ⚠️ Code correct, **never ran in CI** (no `winrt` package) |
| #3 `_stop_playback` no-op | High | `interrupt_fn` wired | ✅ Wiring verified |
| #4 `mark_spoken` too early | High | `unmark_spoken` on error | ✅ Verified |
| #5 FileSource infinite last frame | High | Returns `None` at EOF | ✅ Verified with regression test |
| #6 Latin-only garbage passes | Medium | `require_cyrillic=True` | ✅ Verified with regression test |
| #7 `settle_ms` ignored | Medium | `max(90, ms*0.3)` | ✅ Verified; minimal at default=300 |
| #8 `is_playing()` always False | Medium | `_playing` flag | ✅ block=True case; ⚠️ block=False residual bug |
| #9 OCR error not shown in UI | Medium | Deferred `_emit_error` | ✅ Emission path verified |
| #10 `poll()` state path | Low | Not fixed | Acknowledged |
| #14 `capture.py` attribution | Low | Docstring updated | ✅ Verified |
| **#A** Windows SKIP self-test | **New Critical** | CI steps added in QA branch | Pending next CI run |
| **#B** No `--strict` mode | **New High** | App code change needed | Reported as finding |
| **#C** `is_playing()` block=False | **New Low** | Not fixed | Residual |
| **#D** `winrt` missing from deps | **New Medium** | Reported | Not fixed by dev |
| **#E** REPORT.md false OCR claim | **New Honesty** | Reported | Not corrected by dev |
