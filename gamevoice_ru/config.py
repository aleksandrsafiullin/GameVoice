"""Persistent settings for GameVoice RU stored in a JSON file."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path.home() / ".gamevoice_ru" / "settings.json"


@dataclass
class SubtitleRegion:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    relative_to_window: bool = True  # coordinates relative to source window client area


@dataclass
class AppConfig:
    # --- Source ---
    source_type: str = "file"          # "remoteplay" | "window" | "monitor" | "file"
    source_window_title: str = "PS Remote Play"
    source_process_name: str = "RemotePlay"
    source_file_path: str = ""
    region: SubtitleRegion = field(default_factory=SubtitleRegion)

    # --- OCR ---
    ocr_engine: str = "tesseract"      # "tesseract" | "winrt"
    ocr_fps: float = 5.0               # 2–10
    ocr_language: str = "ru"

    # --- TTS ---
    tts_engine: str = "piper"          # "piper" | "silero"
    tts_voice: str = "ru_RU-dmitri-medium"
    tts_rate: float = 1.0              # 0.85–1.4
    tts_volume: float = 1.0            # 0–1
    tts_queue_mode: str = "natural"    # "natural" | "fast"
    tts_max_queue: int = 2
    tts_max_age_sec: float = 3.0

    # --- Audio ---
    audio_device: Optional[str] = None  # None = system default

    # --- Stabilizer ---
    stabilizer_settle_ms: int = 300
    stabilizer_stability_count: int = 2
    stabilizer_text_expiry_sec: float = 4.0
    stabilizer_cooldown_ms: int = 200

    # --- UI ---
    ui_language: str = "ru"
    hotkey_toggle: str = "ctrl+shift+g"
    minimize_to_tray: bool = True
    first_run: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["region"] = asdict(self.region)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        region_d = d.pop("region", {})
        cfg = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        cfg.region = SubtitleRegion(**{k: v for k, v in region_d.items() if k in SubtitleRegion.__dataclass_fields__})
        return cfg


class Settings:
    def __init__(self, path: Path = _DEFAULT_CONFIG_PATH):
        self._path = path
        self.config = AppConfig()
        self.load()

    def load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self.config = AppConfig.from_dict(data)
                logger.debug("Settings loaded from %s", self._path)
        except Exception as e:
            logger.warning("Could not load settings (%s), using defaults", e)
            self.config = AppConfig()

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.config.to_dict(), f, ensure_ascii=False, indent=2)
            logger.debug("Settings saved to %s", self._path)
        except Exception as e:
            logger.warning("Could not save settings: %s", e)
