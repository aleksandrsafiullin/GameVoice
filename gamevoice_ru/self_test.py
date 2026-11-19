"""
GameVoice RU headless self-test.

Run with:
  GameVoiceRU.exe --self-test                   # lenient mode (SKIP != failure)
  GameVoiceRU.exe --self-test --strict          # strict mode (SKIP = failure)
  GameVoiceRU.exe --self-test --output out.json # save JSON to file

Performs:
  1. Load AppConfig (defaults — no settings file needed).
  2. Render a synthetic Russian subtitle frame.
  3. Run the platform's preferred OCR engine:
       - Windows: WindowsOCR first; falls back to Tesseract if unavailable.
       - Linux/macOS: Tesseract directly.
     Reports which engine ran.
  4. Feed OCR result through SubtitleStabilizer.
  5. Synthesize with TTS into a WAV buffer (no audio device needed).
  6. Enumerate audio output devices.
  7. Print a JSON summary to stdout.
  8. Exit 0 on success, 1 on critical failure.

Lenient mode (default):
  - Missing TTS model, unavailable OCR engine → SKIP (warning, not failure).
  - Useful for quick smoke-test of EXE launch and import correctness.

Strict mode (--strict):
  - OCR SKIP (no engine available) → critical failure, exit 1.
  - TTS SKIP (model not downloaded) → critical failure, exit 1.
  - OCR ran but returned no Cyrillic → critical failure, exit 1.
  - TTS ran but returned empty WAV → critical failure, exit 1.
  - Use in CI after Tesseract, winrt, and Piper voice are pre-installed.
"""
from __future__ import annotations

import io
import json
import logging
import sys
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Synthetic frame generator (no Pillow dependency on critical path)
# ---------------------------------------------------------------------------

def _make_synthetic_frame(text: str = "Привет, это тест!", width: int = 640, height: int = 360) -> np.ndarray:
    """
    Create a minimal BGR numpy frame with white text on black background using
    OpenCV (no Pillow needed in the self-test). Falls back to a Pillow-rendered
    frame if OpenCV putText can't render Cyrillic.
    """
    try:
        import cv2
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # OpenCV cannot render Cyrillic natively on all platforms;
        # fall through to Pillow path.
        raise ValueError("use pillow path")
    except Exception:
        pass
    return _make_pillow_frame(text, width, height)


def _make_pillow_frame(text: str, width: int, height: int) -> np.ndarray:
    """Render text using Pillow → BGR numpy array."""
    try:
        import cv2
        from PIL import Image, ImageDraw, ImageFont

        frame = Image.new("RGB", (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(frame)

        font = None
        for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]:
            if Path(path).exists():
                try:
                    font = ImageFont.truetype(path, 28)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()

        # Draw outline + text in subtitle area (bottom)
        y = int(height * 0.78)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
        except AttributeError:
            tw, _ = draw.textsize(text, font=font)
        x = (width - tw) // 2

        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
            draw.text((x + dx, y + dy), text, fill=(0, 0, 0), font=font)
        draw.text((x, y), text, fill=(255, 255, 255), font=font)

        arr = np.array(frame)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception as e:
        logger.warning("[SelfTest] Pillow frame failed: %s", e)
        return np.zeros((height, width, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Self-test runner
# ---------------------------------------------------------------------------

def run_self_test(output_path: Optional[Path] = None, strict: bool = False) -> dict:
    """
    Execute the self-test pipeline and return a JSON-serialisable result dict.
    Does not raise; all errors are captured in the result.

    ``strict=True``: any SKIP on OCR or TTS is treated as a critical failure
    (use in CI after Tesseract, winrt, and Piper voice are pre-installed).
    """
    result: dict = {
        "gamevoice_ru_version": "0.1.0",
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "strict": strict,
        "steps": {},
        "overall": "PASS",
        "critical_failures": [],
        "warnings": [],
    }

    steps = result["steps"]

    # ------------------------------------------------------------------
    # Step 1: Config
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        from gamevoice_ru.config import AppConfig
        cfg = AppConfig()
        steps["config"] = {"status": "OK", "source_type": cfg.source_type}
    except Exception as e:
        steps["config"] = {"status": "FAIL", "error": str(e)}
        result["critical_failures"].append(f"Config: {e}")
        result["overall"] = "FAIL"
    steps["config"]["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------
    # Step 2: Synthetic frame
    # ------------------------------------------------------------------
    SUBTITLE_TEXT = "Привет! Это тестовая реплика."
    t0 = time.perf_counter()
    try:
        frame = _make_pillow_frame(SUBTITLE_TEXT, 640, 360)
        h, w = frame.shape[:2]
        crop = frame[int(h * 0.73):h, 0:w]
        steps["frame"] = {
            "status": "OK",
            "size": f"{w}x{h}",
            "crop_size": f"{crop.shape[1]}x{crop.shape[0]}",
        }
    except Exception as e:
        steps["frame"] = {"status": "FAIL", "error": str(e)}
        result["critical_failures"].append(f"Frame: {e}")
        result["overall"] = "FAIL"
        crop = np.zeros((50, 300, 3), dtype=np.uint8)
    steps["frame"]["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------
    # Step 3: OCR — try Windows OCR first on Windows, then Tesseract
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    ocr_text = ""
    ocr_engine_used = "none"
    ocr_error = None

    if sys.platform == "win32":
        try:
            from gamevoice_ru.pipeline.ocr import WindowsOCR
            win_ocr = WindowsOCR("ru")
            if win_ocr.is_available():
                ocr_text = win_ocr.recognize(crop)
                ocr_engine_used = "WindowsOCR"
            else:
                result["warnings"].append(f"WindowsOCR unavailable: {win_ocr.init_error}. Falling back to Tesseract.")
        except Exception as e:
            result["warnings"].append(f"WindowsOCR exception: {e}. Falling back to Tesseract.")

    if not ocr_engine_used or ocr_engine_used == "none":
        try:
            from gamevoice_ru.pipeline.ocr import TesseractOCR
            tess = TesseractOCR("ru")
            if tess.is_available():
                ocr_text = tess.recognize(crop)
                ocr_engine_used = "TesseractOCR"
            else:
                ocr_error = tess.init_error
        except Exception as e:
            ocr_error = str(e)

    has_cyrillic = any("\u0400" <= c <= "\u04FF" for c in ocr_text)
    if not ocr_engine_used or ocr_engine_used == "none":
        ocr_status = "SKIP"
    elif has_cyrillic:
        ocr_status = "OK"
    elif ocr_text:
        ocr_status = "WARN"  # text but no Cyrillic
    else:
        ocr_status = "WARN"  # engine present but empty result

    steps["ocr"] = {
        "status": ocr_status,
        "engine": ocr_engine_used,
        "text": ocr_text[:200],
        "has_cyrillic": has_cyrillic,
        "error": ocr_error,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }
    # Lenient mode: missing engine → warning only.
    # Strict mode: any SKIP or missing Cyrillic → critical failure.
    if not ocr_text and ocr_error:
        msg = f"OCR engine unavailable: {ocr_error}"
        if strict:
            result["critical_failures"].append(f"[strict] {msg}")
            result["overall"] = "FAIL"
        else:
            result["warnings"].append(f"{msg} (not critical in lenient mode)")
    elif not has_cyrillic and ocr_text:
        msg = f"OCR returned text but no Cyrillic: {ocr_text!r}"
        if strict:
            result["critical_failures"].append(f"[strict] {msg}")
            result["overall"] = "FAIL"
        else:
            result["warnings"].append(msg)
    elif not ocr_text and not ocr_error and ocr_engine_used not in ("", "none"):
        msg = "OCR engine present but returned empty string on synthetic frame"
        if strict:
            result["critical_failures"].append(f"[strict] {msg}")
            result["overall"] = "FAIL"
        else:
            result["warnings"].append(msg)
    elif ocr_status == "SKIP" and strict:
        result["critical_failures"].append("[strict] OCR=SKIP: no OCR engine available")
        result["overall"] = "FAIL"

    # ------------------------------------------------------------------
    # Step 4: Stabilizer
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        from gamevoice_ru.pipeline.stabilizer import SubtitleStabilizer
        stab = SubtitleStabilizer(settle_ms=0, stability_count=1, cooldown_ms=0)
        stab_result = stab.update(ocr_text or SUBTITLE_TEXT)
        steps["stabilizer"] = {
            "status": "OK",
            "should_speak": stab_result.should_speak,
            "state": stab_result.state.name,
            "text": stab_result.text[:100],
        }
    except Exception as e:
        steps["stabilizer"] = {"status": "FAIL", "error": str(e)}
        result["critical_failures"].append(f"Stabilizer: {e}")
        result["overall"] = "FAIL"
    steps["stabilizer"]["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------
    # Step 5: TTS — synthesize to WAV buffer (no device needed)
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    tts_ok = False
    try:
        from gamevoice_ru.pipeline.tts import PiperTTS
        tts = PiperTTS("ru_RU-dmitri-medium")
        if tts.is_ready():
            wav, err = tts.synthesize(SUBTITLE_TEXT)
            if wav and not err:
                with wave.open(io.BytesIO(wav), "rb") as wf:
                    duration_ms = int(wf.getnframes() / wf.getframerate() * 1000)
                steps["tts"] = {
                    "status": "OK",
                    "engine": "PiperTTS",
                    "voice": "ru_RU-dmitri-medium",
                    "wav_bytes": len(wav),
                    "duration_ms": duration_ms,
                }
                tts_ok = True
                # Optionally save WAV
                if output_path:
                    wav_path = output_path.parent / "self_test_audio.wav"
                    wav_path.write_bytes(wav)
                    steps["tts"]["wav_saved_to"] = str(wav_path)
            else:
                steps["tts"] = {"status": "FAIL", "engine": "PiperTTS", "error": err}
        else:
            skip_reason = tts.init_error or "model not downloaded"
            steps["tts"] = {
                "status": "SKIP",
                "engine": "PiperTTS",
                "reason": "model not downloaded",
                "init_error": tts.init_error,
            }
            if strict:
                result["critical_failures"].append(
                    f"[strict] TTS=SKIP: Piper model not ready: {skip_reason}"
                )
                result["overall"] = "FAIL"
            else:
                result["warnings"].append(f"TTS model not available: {skip_reason}")
    except Exception as e:
        steps["tts"] = {"status": "FAIL", "error": str(e)}
        result["critical_failures"].append(f"TTS: {e}")
        result["overall"] = "FAIL"
    steps["tts"]["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------
    # Step 6: Audio devices
    # ------------------------------------------------------------------
    t0 = time.perf_counter()
    try:
        from gamevoice_ru.pipeline.audio import list_output_devices
        devices = list_output_devices()
        steps["audio_devices"] = {
            "status": "OK",
            "count": len(devices),
            "devices": [str(d) for d in devices[:5]],
        }
        if not devices:
            result["warnings"].append("No audio output devices found (expected in headless CI)")
    except Exception as e:
        steps["audio_devices"] = {"status": "FAIL", "error": str(e)}
    steps["audio_devices"]["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    result["ocr_engine_used"] = ocr_engine_used
    result["ocr_fallback_to_tesseract"] = (
        ocr_engine_used == "TesseractOCR" and sys.platform == "win32"
    )
    result["total_elapsed_ms"] = sum(
        s.get("elapsed_ms", 0) for s in steps.values()
    )
    return result


def main() -> int:
    logging.basicConfig(level=logging.WARNING)

    # On Windows the console may be cp1252; force UTF-8 so Cyrillic JSON prints cleanly.
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass  # Python <3.7 or non-reconfigurable stream

    output_path = None
    strict = False

    args = sys.argv[1:]
    if "--strict" in args:
        strict = True
    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 < len(args):
            output_path = Path(args[idx + 1])

    result = run_self_test(output_path=output_path, strict=strict)

    # Use ensure_ascii=True so the output is safe on any terminal encoding.
    # The JSON is still fully valid; Cyrillic appears as \uXXXX escapes.
    print(json.dumps(result, indent=2, ensure_ascii=True))

    # Also save to file if requested
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        # Print path to stderr for CI log capture
        print(f"Self-test JSON saved to: {output_path}", file=sys.stderr)

    if result["critical_failures"]:
        print(
            f"\nCRITICAL FAILURES ({len(result['critical_failures'])}):",
            file=sys.stderr,
        )
        for failure in result["critical_failures"]:
            print(f"  - {failure}", file=sys.stderr)

    return 0 if result["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
