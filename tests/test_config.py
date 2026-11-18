"""Tests for settings persistence."""
import json
from pathlib import Path

import pytest

from gamevoice_ru.config import AppConfig, Settings, SubtitleRegion


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.source_type == "file"
        assert cfg.ocr_language == "ru"
        assert cfg.tts_engine == "piper"
        assert 0 < cfg.tts_rate <= 2.0
        assert 0 < cfg.ocr_fps <= 10

    def test_region_defaults(self):
        cfg = AppConfig()
        assert cfg.region.width == 0
        assert cfg.region.height == 0

    def test_to_dict_and_from_dict_roundtrip(self):
        cfg = AppConfig()
        cfg.source_window_title = "PS Remote Play Test"
        cfg.tts_rate = 1.2
        cfg.region = SubtitleRegion(x=10, y=20, width=100, height=50)

        d = cfg.to_dict()
        restored = AppConfig.from_dict(d)
        assert restored.source_window_title == cfg.source_window_title
        assert restored.tts_rate == pytest.approx(cfg.tts_rate)
        assert restored.region.x == 10
        assert restored.region.height == 50


class TestSettings:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "settings.json"
        s = Settings(path=path)
        s.config.source_window_title = "Test Window"
        s.config.tts_voice = "ru_RU-irina-medium"
        s.save()

        s2 = Settings(path=path)
        assert s2.config.source_window_title == "Test Window"
        assert s2.config.tts_voice == "ru_RU-irina-medium"

    def test_corrupt_file_uses_defaults(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{{broken json")
        s = Settings(path=path)
        # Should fall back to defaults without crashing
        assert s.config.source_type == "file"

    def test_missing_file_uses_defaults(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        s = Settings(path=path)
        assert s.config is not None

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "settings.json"
        s = Settings(path=path)
        s.save()
        assert path.exists()
