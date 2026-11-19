# REPORT.md — Статус критериев приёмки MVP

**Дата:** 19 ноября 2026 г.  
**Версия:** 0.1.1 (после QA-ревью PR #2)

---

## Критерии приёмки (секция 11 ТЗ)

| Критерий | Статус | Способ проверки |
|---|---|---|
| Приложение запускается на Windows 11 | ✅ EXE собран в CI; ✅ `--self-test` запущен в CI (см. ниже) | `build-windows` CI: PyInstaller строит EXE, затем `.\GameVoiceRU.exe --self-test` запускается и возвращает 0 |
| Работает выбор источника видео и прямоугольника субтитров | ✅ Verified by test | `tests/test_config.py` + GUI регион-диалог |
| Демонстрационный поток распознаёт русские реплики без перевода | ✅ Verified in CI | `tests/test_pipeline_integration.py::TestOCRPipeline`: 20/20 (100%) на синтетических кадрах; `--self-test` → OCR = "Привет! Это тестовая реплика." |
| При неподвижной фразе русская озвучка не повторяется | ✅ Verified by test | `tests/test_stabilizer.py::test_static_subtitle_spoken_once`, `tests/qa/test_section11_black_box.py::TestStaticSubtitleSpokenOnce` |
| Новая реплика и повтор после паузы распознаются отдельно | ✅ Verified by test | `tests/test_stabilizer.py::test_same_phrase_after_blank_re_spoken`, `tests/qa/TestSameLineAfterPauseReSpoken` |
| Выбор конкретной Bluetooth-колонки и тестовая фраза | ✅ Code; ⚠️ Needs user hardware | Код: `AudioPlayer`, `list_output_devices()`, GUI кнопка «Тест звука». Bluetooth-колонка недоступна в CI |
| Кнопки Start/Stop и визуальная диагностика | ✅ Code review | `ui/main_window.py`: Start/Stop, вкладки OCR/статистика/ошибки |
| Ошибки источника/OCR/моделей/аудиоустройства обрабатываются без краша | ✅ Verified by test | `tests/qa/TestMissingTTSModel`, `TestMissingAudioDevice`, `TestBlackOrMissingSource`; OCR init failure → `on_error` (исправлено QA Finding #9) |
| Результаты на симуляторе подтверждены тестами | ✅ | Все проверки на синтетических кадрах; PS5/GTA VI не заявлено |
| Исходный код, инструкция, зависимости, лицензии, отчёт | ✅ | Все файлы в репозитории |

---

## CI — реальные результаты

| Job | Runner | Статус | Что проверено |
|---|---|---|---|
| `test-linux` | ubuntu-latest | ✅ | 155+ тестов + QA тесты + `--self-test` (Python) |
| `build-windows` | windows-latest | ✅ | PyInstaller строит EXE; **EXE запускается** через `--self-test`; JSON артефакт выгружается |
| Linux `--self-test` | ubuntu-latest | ✅ | OCR=TesseractOCR, Cyrillic=true, overall=PASS |
| Windows EXE `--self-test` | windows-latest | ✅ | OCR=TesseractOCR (или WindowsOCR если установлен русский пакет), overall=PASS |

**Примечание по OCR движку в Windows CI:**
- Если в `windows-latest` установлен русский языковой пакет → `WindowsOCR` запускается.
- Если нет → `TesseractOCR` как fallback.
- `--self-test` явно отчитывается: `"ocr_engine_used"` и `"ocr_fallback_to_tesseract"`.
- **Windows OCR (WinRT) с реальным Russian Language Pack не тестировался** — только через `--self-test` в CI runner (который может не иметь языкового пакета).

---

## QA Findings — статус после исправлений

| # | Severity | Статус | Что сделано |
|---|---|---|---|
| 1 | Critical / Honesty | ✅ **Исправлено** | `--self-test` режим добавлен; `build-windows` запускает EXE и проверяет exit code |
| 2 | Critical | ✅ **Исправлено** | `WindowsOCR.recognize()`: теперь использует BitmapDecoder + InMemoryRandomAccessStream для правильной загрузки пикселей в SoftwareBitmap |
| 3 | High | ✅ **Исправлено** | `TTSQueueManager` принимает `interrupt_fn`; `PipelineRunner` передаёт `AudioPlayer.stop` |
| 4 | High | ✅ **Исправлено** | `mark_spoken` вызывается при enqueue; `unmark_spoken` вызывается в `_on_tts_error` → subtitle retry при ошибке TTS без бесконечных re-enqueue |
| 5 | High | ✅ **Исправлено** | `FileFrameSource.grab_frame()` возвращает `None` при EOF в non-loop режиме; добавлено свойство `at_eof` |
| 6 | Medium | ✅ **Исправлено** | `is_garbage(require_cyrillic=True)` по умолчанию — Latin-only текст отклоняется |
| 7 | Medium | ✅ **Исправлено** | `_effective_settle_ms()` возвращает `max(90, settle_ms * 0.3)` в instant-режиме вместо хардкода 90 мс |
| 8 | Medium | ✅ **Исправлено** | `AudioPlayer._playing` флаг обновляется в `play()`/`stop()`/`wait_until_done()`; `is_playing()` читает его |
| 9 | Medium | ✅ **Исправлено** | OCR init failure → `self._emit_error()` → `on_error` callback → отображается в UI |
| 10 | Low | Не критично | `poll()` → `STABLE` без `APPEARING` — функциональный guard через `SPOKEN` защищает от double-speak |
| 11 | Honesty | ✅ **Исправлено** | REPORT.md переписан: EXE теперь действительно проверяется через `--self-test` |
| 12 | Honesty | ✅ **Исправлено** | OCR точность: CI результат = 100% на синтетических кадрах (измерено, а не "оценочно") |
| 13 | Honesty | ✅ | `DECISIONS.md` → атрибуция корректна |
| 14 | Low | ✅ **Исправлено** | Lingolay copyright notice добавлен в `capture.py` docstring |

---

## Метрики задержки (ТЗ §7)

| Метрика | Цель ТЗ | Факт (Linux CI, synthetic, Tesseract) | Факт (target HW) |
|---|---|---|---|
| Mediana до начала воспроизведения (≤80 символов) | ≤1500 мс | OCR: ~160 мс + Stabilizer: <5 мс + TTS: не измерено (модель не установлена в CI) | ⚠️ Not verified on target hardware |
| p95 | ≤2500 мс | Оценочно ~400 мс (OCR) + TTS latency (depends on model) | ⚠️ Not verified on target hardware |
| ≥95% реплик выделены корректно | ≥95% | **100% на 20 из 20 синтетических кадрах** (CI измерено) | ⚠️ Needs user hardware with real GTA VI |

---

## Что не проверялось (требует аппаратуры пользователя)

| Компонент | Статус |
|---|---|
| PS5 + Remote Play (захват окна) | ⚠️ Не проверялось |
| GTA VI субтитры (шрифт, позиция, фон) | ⚠️ Не проверялось |
| Bluetooth-колонка (устройство, WASAPI, аудиозадержка) | ⚠️ Не проверялось |
| Windows OCR с Russian Language Pack на реальной Windows 11 | ⚠️ Не проверялось (WindowsOCR код исправлен, но не верифицирован на железе) |
| Второй PSN-аккаунт для Remote Play | ⚠️ Неофициальный сценарий |
| Задержка цепочки Remote Play + Bluetooth | ⚠️ Не измерялось |

---

## Лицензии зависимостей

| Пакет | Лицензия |
|---|---|
| piper-tts (Python package) | GPL-3.0 |
| Piper voice: ru_RU-dmitri | CC0-1.0 |
| Piper voice: ru_RU-irina | CC0-1.0 |
| Silero model weights | CC-BY 4.0 |
| pytesseract | Apache-2.0 |
| Tesseract OCR | Apache-2.0 |
| sounddevice | MIT |
| numpy | BSD-3-Clause |
| opencv-python | Apache-2.0 |
| Pillow | HPND |
| mss | MIT |
| Lingolay (алгоритмы адаптированы) | GPL-3.0 |

**GameVoice RU:** GPL-3.0 (обусловлено Lingolay + piper-tts).
