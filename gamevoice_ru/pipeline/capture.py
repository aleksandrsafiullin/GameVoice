"""
Frame capture sources.

Supported modes:
  - file      : iterate frames from a folder or video file (demo/test mode)
  - window    : capture a named window (Windows only via mss; on Linux uses PIL stub)
  - monitor   : capture a monitor region
  - remoteplay: alias for window("PS Remote Play")

The crop region (SubtitleRegion) is applied after capture to extract the
subtitle rectangle.

Adapted from Lingolay's capture/screen.py and capture/region.py patterns.
(Original: keremss7/lingolay, GPL-3.0)
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Generator, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cv2_or_pil_imread(path: Path) -> Optional[np.ndarray]:
    """Read an image file → BGR numpy array."""
    try:
        import cv2
        img = cv2.imread(str(path))
        if img is not None:
            return img
    except Exception:
        pass
    try:
        from PIL import Image
        pil = Image.open(path).convert("RGB")
        arr = np.array(pil)
        import cv2
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _frame_hash(frame: np.ndarray) -> int:
    """Fast approximate hash to skip identical consecutive frames."""
    small = frame[::8, ::8, :]  # downsample 8x
    return hash(small.tobytes())


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class FrameSource:
    """Yields frames as BGR numpy arrays."""

    def is_available(self) -> bool:
        return False

    def grab_frame(self) -> Optional[np.ndarray]:
        return None

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# File / folder source (demo & test mode)
# ---------------------------------------------------------------------------

class FileFrameSource(FrameSource):
    """
    Reads frames from:
      - a folder of image files (sorted by name)
      - a video file (via cv2.VideoCapture)

    Provides a continuous loop with configurable fps for demo mode.
    """

    def __init__(self, path: str, fps: float = 5.0, loop: bool = False):
        self._path = Path(path)
        self._fps = max(1.0, fps)
        self._loop = loop
        self._frames: list[np.ndarray] = []
        self._index = 0
        self._cap = None
        self._error: Optional[str] = None
        self._load()

    def _load(self) -> None:
        p = self._path
        if p.is_dir():
            exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"}
            files = sorted(f for f in p.iterdir() if f.suffix.lower() in exts)
            if not files:
                self._error = f"Папка {p} не содержит изображений."
                return
            for f in files:
                img = _cv2_or_pil_imread(f)
                if img is not None:
                    self._frames.append(img)
            logger.info("[FileSource] Загружено %d кадров из %s", len(self._frames), p)
        elif p.is_file():
            try:
                import cv2
                self._cap = cv2.VideoCapture(str(p))
                if not self._cap.isOpened():
                    self._error = f"Не удалось открыть видеофайл: {p}"
                    self._cap = None
                else:
                    logger.info("[FileSource] Открыт видеофайл: %s", p)
            except Exception as e:
                self._error = str(e)
        else:
            self._error = f"Путь не найден: {p}"

    def is_available(self) -> bool:
        return bool(self._frames) or (self._cap is not None and self._cap.isOpened())

    @property
    def error(self) -> Optional[str]:
        return self._error

    def grab_frame(self) -> Optional[np.ndarray]:
        if self._cap is not None:
            ret, frame = self._cap.read()
            if not ret:
                if self._loop:
                    self._cap.set(1, 0)  # cv2.CAP_PROP_POS_FRAMES
                    ret, frame = self._cap.read()
                if not ret:
                    return None
            return frame
        if not self._frames:
            return None
        frame = self._frames[self._index]
        self._index = (self._index + 1) % len(self._frames) if self._loop else min(self._index + 1, len(self._frames) - 1)
        return frame.copy()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


# ---------------------------------------------------------------------------
# Screen / monitor source (mss)
# ---------------------------------------------------------------------------

class ScreenRegionSource(FrameSource):
    """Captures a rectangular region from a monitor using mss."""

    def __init__(self, monitor_index: int = 1, region: Optional[dict] = None):
        self._monitor_index = monitor_index
        self._region = region  # {"left": x, "top": y, "width": w, "height": h}
        self._sct = None
        self._error: Optional[str] = None
        self._init()

    def _init(self) -> None:
        try:
            import mss
            self._sct = mss.mss()
        except Exception as e:
            self._error = str(e)
            logger.warning("[ScreenSource] mss недоступен: %s", e)

    def is_available(self) -> bool:
        return self._sct is not None

    def grab_frame(self) -> Optional[np.ndarray]:
        if self._sct is None:
            return None
        try:
            import cv2
            if self._region:
                mon = self._region
            else:
                mon = self._sct.monitors[self._monitor_index]
            sct_img = self._sct.grab(mon)
            frame = np.array(sct_img)
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            logger.warning("[ScreenSource] Ошибка захвата: %s", e)
            return None

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None


# ---------------------------------------------------------------------------
# Window capture (Windows only — Windows Graphics Capture / mss fallback)
# ---------------------------------------------------------------------------

class WindowSource(FrameSource):
    """
    Captures the client area of a named window.

    On Windows: attempts wgcapture (Windows Graphics Capture API), then falls
    back to mss window cropping.
    On Linux/macOS: always returns None with a clear error message.
    """

    def __init__(self, window_title: str, process_name: str = ""):
        self._title = window_title
        self._process_name = process_name
        self._hwnd = None
        self._wgc = None
        self._error: Optional[str] = None
        self._last_black_warn = 0.0
        self._init()

    def _init(self) -> None:
        if sys.platform != "win32":
            self._error = "Захват окна доступен только на Windows."
            return
        self._find_window()
        if self._hwnd:
            self._try_wgc()

    def _find_window(self) -> None:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, self._title)
            if hwnd:
                self._hwnd = hwnd
                return
            # Partial match by enumerating
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
            def enum_cb(hwnd, _):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                title = buf.value
                if self._title.lower() in title.lower():
                    found.append(hwnd)
                return True

            user32.EnumWindows(enum_cb, 0)
            if found:
                self._hwnd = found[0]
            else:
                self._error = f"Окно '{self._title}' не найдено. Убедитесь, что приложение запущено."
        except Exception as e:
            self._error = str(e)

    def _try_wgc(self) -> None:
        try:
            import wgcapture
            self._wgc = wgcapture.WindowCapture(self._hwnd)
        except Exception:
            self._wgc = None

    def is_available(self) -> bool:
        return self._hwnd is not None

    @property
    def error(self) -> Optional[str]:
        return self._error

    def grab_frame(self) -> Optional[np.ndarray]:
        if self._hwnd is None:
            return None
        # Try wgcapture first
        if self._wgc is not None:
            try:
                frame = self._wgc.grab()
                if frame is not None:
                    if self._is_black(frame):
                        self._warn_black()
                        return None
                    return frame
            except Exception:
                self._wgc = None
        # Fallback: win32gui + mss
        return self._grab_mss_fallback()

    def _grab_mss_fallback(self) -> Optional[np.ndarray]:
        try:
            import ctypes
            import ctypes.wintypes as wintypes
            import cv2
            import mss

            user32 = ctypes.windll.user32
            rect = wintypes.RECT()
            user32.GetClientRect(self._hwnd, ctypes.byref(rect))
            pt = wintypes.POINT()
            user32.ClientToScreen(self._hwnd, ctypes.byref(pt))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                self._warn_black("Окно свёрнуто или имеет нулевой размер.")
                return None
            mon = {"left": pt.x, "top": pt.y, "width": w, "height": h}
            with mss.mss() as sct:
                sct_img = sct.grab(mon)
                frame = np.array(sct_img)
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            logger.warning("[WindowSource] mss fallback error: %s", e)
            return None

    @staticmethod
    def _is_black(frame: np.ndarray) -> bool:
        return np.mean(frame) < 3.0

    def _warn_black(self, msg: str = "") -> None:
        now = time.time()
        if now - self._last_black_warn > 10.0:
            logger.warning(
                "[WindowSource] Кадр чёрный или пустой. %s"
                "Возможные причины: Remote Play свёрнут, потерян, "
                "или игра защищена HDCP.", msg
            )
            self._last_black_warn = now

    def close(self) -> None:
        if self._wgc is not None:
            try:
                self._wgc.close()
            except Exception:
                pass
            self._wgc = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_source(config) -> FrameSource:
    """
    Create a FrameSource from an AppConfig.
    ``config`` is a gamevoice_ru.config.AppConfig instance.
    """
    src = config.source_type
    if src in ("remoteplay", "window"):
        return WindowSource(
            window_title=config.source_window_title,
            process_name=config.source_process_name,
        )
    if src == "monitor":
        return ScreenRegionSource(monitor_index=1)
    # Default: file/demo mode
    return FileFrameSource(
        path=config.source_file_path,
        fps=config.ocr_fps,
        loop=True,
    )


# ---------------------------------------------------------------------------
# Region crop
# ---------------------------------------------------------------------------

def crop_region(frame: np.ndarray, region, frame_width: int, frame_height: int) -> np.ndarray:
    """
    Crop the subtitle rectangle from a full frame.

    If region has zero size, returns the bottom 25% of the frame (default
    subtitle position for most games).
    """
    if region.width <= 0 or region.height <= 0:
        h = frame.shape[0]
        w = frame.shape[1]
        y0 = int(h * 0.73)
        return frame[y0:h, 0:w]
    x0 = max(0, region.x)
    y0 = max(0, region.y)
    x1 = min(frame.shape[1], region.x + region.width)
    y1 = min(frame.shape[0], region.y + region.height)
    return frame[y0:y1, x0:x1]
