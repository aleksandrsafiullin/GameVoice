"""
Russian OCR module.

Primary engine: Tesseract (pytesseract) with rus.traineddata.
Fallback: Windows OCR (WinRT) when available.

Preprocessing: scale, contrast boost, binarize for white subtitle text.

Adapted from Lingolay's ocr/tesseract_ocr.py and ocr/windows_ocr.py.
(Original: keremss7/lingolay, GPL-3.0)
"""
from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class OCREngine(ABC):
    @abstractmethod
    def recognize(self, image: np.ndarray) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    def get_name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Image pre-processing
# ---------------------------------------------------------------------------

def preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    """
    Prepare subtitle crop for Russian OCR.

    Strategy:
    1. Upscale to at least 100 px height (Tesseract accuracy drops below 30px).
    2. Convert to grayscale.
    3. Apply adaptive threshold or Otsu to extract bright text on dark background.
    4. Dilate slightly to fill gaps from dark outline.
    """
    try:
        import cv2

        h, w = image.shape[:2]
        if h < 60:
            scale = 60.0 / h
            image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()

        # Bilateral filter to reduce noise while keeping edges
        gray = cv2.bilateralFilter(gray, 5, 50, 50)

        # Adaptive threshold — handles dark outline + bright text
        # Use THRESH_BINARY_INV so text becomes black (Tesseract prefers black text on white)
        mean_val = np.mean(gray)
        if mean_val < 100:
            # Dark background — text is bright
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            # Light background — text is dark
            thresh = cv2.adaptiveThreshold(
                gray, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31, 10
            )

        # Slight dilation to close broken letter strokes
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        result = cv2.dilate(thresh, kernel, iterations=1)
        return result
    except Exception as e:
        logger.debug("[OCR preprocess] %s", e)
        return image


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------

_PYTESSERACT_OK: Optional[bool] = None
_TESSERACT_LANG_AVAILABLE: Optional[bool] = None


def _check_tesseract() -> bool:
    global _PYTESSERACT_OK, _TESSERACT_LANG_AVAILABLE
    if _PYTESSERACT_OK is not None:
        return _PYTESSERACT_OK
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _PYTESSERACT_OK = True
    except Exception as e:
        logger.info("[Tesseract] Not available: %s", e)
        _PYTESSERACT_OK = False
    return _PYTESSERACT_OK


def _check_rus_lang() -> bool:
    global _TESSERACT_LANG_AVAILABLE
    if _TESSERACT_LANG_AVAILABLE is not None:
        return _TESSERACT_LANG_AVAILABLE
    try:
        import pytesseract
        langs = pytesseract.get_languages()
        _TESSERACT_LANG_AVAILABLE = "rus" in langs
        if not _TESSERACT_LANG_AVAILABLE:
            logger.warning(
                "[Tesseract] Языковой пакет 'rus' не найден. "
                "Установите tesseract-ocr-rus (Linux: sudo apt install tesseract-ocr-rus; "
                "Windows: установщик из UB-Mannheim)."
            )
    except Exception:
        _TESSERACT_LANG_AVAILABLE = False
    return _TESSERACT_LANG_AVAILABLE


class TesseractOCR(OCREngine):
    """
    Russian OCR via Tesseract with pytesseract.
    Adapted from Lingolay's TesseractOCR (keremss7/lingolay, GPL-3.0).
    """

    _PSM_OPTIONS = [
        "--psm 6",   # Uniform block of text
        "--psm 7",   # Single text line
    ]

    def __init__(self, language: str = "ru"):
        self._lang = "rus"
        self._init_error: Optional[str] = None
        self._last_latency_ms = 0
        self._preprocess = True
        if not _check_tesseract():
            self._init_error = (
                "Tesseract OCR не установлен. "
                "Linux: sudo apt install tesseract-ocr tesseract-ocr-rus\n"
                "Windows: https://github.com/UB-Mannheim/tesseract/wiki"
            )
        elif not _check_rus_lang():
            self._init_error = (
                "Языковой пакет Tesseract 'rus' не найден.\n"
                "Linux: sudo apt install tesseract-ocr-rus\n"
                "Windows: переустановите Tesseract с Russian Language Data."
            )

    def is_available(self) -> bool:
        return self._init_error is None

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def get_name(self) -> str:
        return "Tesseract OCR (rus)"

    def recognize(self, image: np.ndarray) -> str:
        if self._init_error:
            return ""
        start = time.perf_counter()
        processed = preprocess_for_ocr(image) if self._preprocess else image
        result = self._run_tesseract(processed)
        # If that failed, try original image
        if not result.strip() and self._preprocess:
            result = self._run_tesseract(image)
        self._last_latency_ms = int((time.perf_counter() - start) * 1000)
        logger.debug("[Tesseract] %dms → %r", self._last_latency_ms, result[:60])
        return result

    def _run_tesseract(self, image: np.ndarray) -> str:
        try:
            import pytesseract
            import cv2

            # Ensure RGB for pytesseract
            if image.ndim == 2:
                rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            elif image.shape[2] == 4:
                rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
            else:
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

            # Try different PSM modes, pick the best non-empty result
            best = ""
            for psm in self._PSM_OPTIONS:
                config = f"--oem 1 {psm} -l rus"
                text = pytesseract.image_to_string(rgb, config=config, lang=self._lang)
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                candidate = "\n".join(lines)
                if len(candidate) > len(best):
                    best = candidate
            return best
        except Exception as e:
            logger.warning("[Tesseract] Error: %s", e)
            return ""

    @property
    def last_latency_ms(self) -> int:
        return self._last_latency_ms


# ---------------------------------------------------------------------------
# Windows OCR (WinRT) — Windows only, requires Russian language pack
# ---------------------------------------------------------------------------

class WindowsOCR(OCREngine):
    """
    Windows OCR via WinRT.
    Works on Windows 10/11 with Russian language pack installed.
    Adapted from Lingolay's WindowsOCR (keremss7/lingolay, GPL-3.0).
    """

    def __init__(self, language: str = "ru"):
        self._language = language
        self._ocr = None
        self._init_error: Optional[str] = None
        self._last_latency_ms = 0
        if sys.platform != "win32":
            self._init_error = "Windows OCR доступен только на Windows."
        else:
            self._init()

    def _init(self) -> None:
        try:
            import winrt.windows.media.ocr as winrt_ocr
            import winrt.windows.globalization as globalization

            lang = globalization.Language(self._language)
            if not winrt_ocr.OcrEngine.is_language_supported(lang):
                self._init_error = (
                    f"Язык '{self._language}' не поддерживается Windows OCR. "
                    "Установите языковой пакет: Настройки → Время и язык → Язык."
                )
                return
            self._ocr = winrt_ocr.OcrEngine.try_create_from_language(lang)
            if self._ocr is None:
                self._init_error = "Не удалось создать движок Windows OCR."
        except Exception as e:
            self._init_error = f"Windows OCR недоступен: {e}"

    def is_available(self) -> bool:
        return self._ocr is not None

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    def get_name(self) -> str:
        return "Windows OCR (WinRT)"

    def recognize(self, image: np.ndarray) -> str:
        """
        Run Windows OCR on a BGR numpy frame.

        Converts to BGRA8, writes to an InMemoryRandomAccessStream via BitmapEncoder,
        then decodes it into a SoftwareBitmap so the bitmap is properly populated
        (Finding #2 — the original code allocated an empty bitmap and never loaded data).
        """
        if self._ocr is None:
            return ""
        start = time.perf_counter()
        try:
            import asyncio
            import io as _io
            from PIL import Image

            # BGR → RGBA (Windows OCR expects BGRA8 or RGBA8)
            rgb = image if image.ndim == 2 else image[:, :, ::-1]  # BGR→RGB
            pil_image = Image.fromarray(rgb).convert("RGBA")

            # Encode to PNG in memory, then decode via BitmapDecoder into SoftwareBitmap
            png_buf = _io.BytesIO()
            pil_image.save(png_buf, format="PNG")
            png_bytes = png_buf.getvalue()

            async def _run():
                import winrt.windows.storage.streams as streams
                import winrt.windows.graphics.imaging as imaging

                # Write PNG bytes into an InMemoryRandomAccessStream
                ras = streams.InMemoryRandomAccessStream()
                writer = streams.DataWriter(ras)
                writer.write_bytes(bytearray(png_bytes))
                await writer.store_async()
                writer.detach_stream()
                ras.seek(0)

                # Decode stream → SoftwareBitmap (BGRA8 for OCR)
                decoder = await imaging.BitmapDecoder.create_async(ras)
                sb = await decoder.get_software_bitmap_async()
                # Ensure BGRA8 premultiplied (required by OcrEngine)
                sb_converted = imaging.SoftwareBitmap.convert(
                    sb,
                    imaging.BitmapPixelFormat.BGRA8,
                    imaging.BitmapAlphaMode.PREMULTIPLIED,
                )
                result = await self._ocr.recognize_async(sb_converted)
                return result

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_run())
            loop.close()

            lines = [ln.text for ln in result.lines]
            return "\n".join(lines)
        except Exception as e:
            logger.warning("[WinOCR] Error: %s", e)
            return ""
        finally:
            self._last_latency_ms = int((time.perf_counter() - start) * 1000)

    @property
    def last_latency_ms(self) -> int:
        return self._last_latency_ms


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_ocr_engine(engine: str = "tesseract") -> OCREngine:
    """Create the best available OCR engine for Russian."""
    if engine == "winrt" and sys.platform == "win32":
        ocr = WindowsOCR("ru")
        if ocr.is_available():
            return ocr
        logger.info("[OCR] WinRT unavailable (%s), falling back to Tesseract", ocr.init_error)
    ocr = TesseractOCR("ru")
    if not ocr.is_available():
        logger.error("[OCR] %s", ocr.init_error)
    return ocr
