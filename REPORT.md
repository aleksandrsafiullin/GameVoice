# REPORT.md — Статус критериев приёмки MVP

**Дата:** 19 ноября 2026 г.  
**Версия:** 0.1.2 (после QA-ревью PR #2 и PR #3)

---

## Критерии приёмки (секция 11 ТЗ)

| Критерий | Статус | Способ проверки |
|---|---|---|
| Приложение запускается на Windows 11 | ✅ EXE собран в CI; ✅ `--self-test --strict` запускается в CI | `build-windows`: PyInstaller → EXE → `--self-test --strict` → exit 0 |
| Работает выбор источника видео и прямоугольника субтитров | ✅ Verified by test | `tests/test_config.py` + GUI регион-диалог |
| Демо-поток распознаёт русские реплики без перевода | ✅ Linux CI verified; ⚠️ Windows — см. ниже | Linux: Tesseract OCR 20/20 (100%) + `--strict` exit 0; Windows: зависит от Tesseract/WinRT в CI runner |
| Неподвижная фраза не повторяется | ✅ Verified by test | `test_stabilizer.py` + `tests/qa/TestStaticSubtitleSpokenOnce` |
| Новая реплика и повтор после паузы | ✅ Verified by test | `test_stabilizer.py` + `tests/qa/TestSameLineAfterPauseReSpoken` |
| Выбор Bluetooth-колонки и тестовая фраза | ✅ Code; ⚠️ Needs user hardware | `AudioPlayer`, `list_output_devices()`, GUI кнопка «Тест звука» |
| Start/Stop и визуальная диагностика | ✅ Code review | `ui/main_window.py` |
| Ошибки источника/OCR/моделей/аудио без краша | ✅ Verified by test | `tests/qa/TestMissingTTSModel`, `TestMissingAudioDevice`, `TestBlackOrMissingSource` |
| Результаты на симуляторе; нет фиктивных PS5-проверок | ✅ | Только синтетические кадры |
| Исходный код, инструкция, зависимости, лицензии | ✅ | `README.ru.md`, `requirements.txt`, `LICENSES`, `DECISIONS.md` |

---

## CI — реальные результаты

| Job | Runner | Статус | Что проверено |
|---|---|---|---|
| `test-linux` | ubuntu-latest | ✅ | 161+ тестов + `--self-test --strict` (Tesseract + Piper) |
| `build-windows` | windows-latest | ✅ | PyInstaller EXE + `--strict` Python + EXE strict self-test |

### Windows CI — `--self-test --strict`

**Целевой движок OCR:** WindowsOCR (если `Add-WindowsCapability Language.OCR~~~ru-RU~0.0.1.0` успешен).  
**Fallback:** TesseractOCR (если WindowsOCR недоступен на данном runner).

**Известный пробел Windows OCR (Finding #2, QA PR #3):**  
`winrt-runtime` теперь устанавливается в CI. `Add-WindowsCapability` запускается перед сборкой. Если runner Windows Server 2022 поддерживает Language.OCR~~~ru-RU → WindowsOCR используется. Если нет → Tesseract fallback с явным логированием.

**TTS в CI:** Голос `ru_RU-dmitri-medium` (CC0-1.0) скачивается из HuggingFace. `piper-tts` устанавливается через pip. `--strict` провалится, если модель не скачалась.

---

## QA Findings — финальный статус

| # | Severity | Статус | Верификация |
|---|---|---|---|
| 1 Critical: EXE никогда не запускался | ✅ Исправлено | `--self-test --strict`; CI проверяет exit code |
| 2 Critical: WindowsOCR пустой bitmap | ✅ Исправлено (код) + ⚠️ Частично в CI | BitmapDecoder fix; winrt теперь в CI; WindowsOCR работает если Language.OCR установлен |
| 3 High: `_stop_playback` no-op | ✅ Исправлено | `interrupt_fn` parameter; regression test |
| 4 High: `mark_spoken` до синтеза | ✅ Исправлено | `unmark_spoken` при ошибке TTS; regression test |
| 5 High: FileSource бесконечный последний кадр | ✅ Исправлено | Возвращает None при EOF; regression test |
| 6 Medium: `is_garbage` пропускает Latin | ✅ Исправлено | `require_cyrillic=True`; regression test |
| 7 Medium: `settle_ms` игнорируется | ✅ Исправлено | `max(90, ms*0.3)`; regression test |
| 8 Medium: `is_playing()` всегда False | ✅ Исправлено (Finding C тоже) | `_playing` флаг + watcher thread для `block=False` |
| 9 Medium: OCR init не в `on_error` | ✅ Исправлено | Deferred emit; regression test |
| 10 Low: `poll()` state path | Acknowledged | SPOKEN guard предотвращает double-speak |
| 14 Low: Copyright в `capture.py` | ✅ Исправлено | Docstring + LICENSES файл |
| **A** Windows SKIP self-test | ✅ Исправлено | `--strict` режим; Tesseract via choco + Piper voice download |
| **B** No `--strict` mode | ✅ Исправлено | `--self-test --strict` добавлен |
| **C** `is_playing()` block=False | ✅ Исправлено | Watcher thread сбрасывает флаг |
| **D** `winrt` отсутствует в deps | ✅ Исправлено | Комментарий в `requirements.txt` + шаг в CI |
| **E** REPORT.md ложное OCR=TesseractOCR | ✅ Исправлено | REPORT.md переписан |

---

## Метрики задержки (ТЗ §7)

| Метрика | Цель | Факт (Linux CI) | Факт (target HW) |
|---|---|---|---|
| Mediana до начала воспроизведения | ≤1500 мс | OCR: ~160 мс + TTS: ~600-800 мс (Piper) ≈ **~900 мс** | ⚠️ Not verified on target hardware |
| p95 | ≤2500 мс | Оценочно ~1500 мс | ⚠️ Not verified on target hardware |
| ≥95% реплик выделены корректно | ≥95% | **100% на 20/20 кадрах** (CI measured) | ⚠️ Needs real GTA VI subtitles |

---

## Что не проверялось

| Компонент | Статус |
|---|---|
| PS5 + Remote Play (захват окна) | ⚠️ Не проверялось |
| GTA VI субтитры (шрифт, позиция, фон) | ⚠️ Не проверялось |
| Bluetooth-колонка (устройство, WASAPI) | ⚠️ Не проверялось |
| WindowsOCR с реальным Language Pack на Windows 11 (не Server 2022) | ⚠️ CI использует Windows Server 2022 |
| EXE на реальной Windows 11 | ⚠️ CI использует Windows Server 2022 |
| Задержка цепочки Remote Play + Bluetooth | ⚠️ Не измерялось |
| Второй PSN-аккаунт для Remote Play | ⚠️ Неофициальный сценарий |

---

## Лицензии зависимостей

| Пакет | Лицензия | Верификация |
|---|---|---|
| piper-tts | GPL-3.0 | — |
| ru_RU-dmitri (голос) | **CC0-1.0** | Верифицировано 19.11.2026, см. LICENSES |
| ru_RU-irina (голос) | **CC0-1.0** | Верифицировано 19.11.2026, см. LICENSES |
| Silero model weights | CC-BY 4.0 | Опционально; не бандлится |
| pytesseract | Apache-2.0 | — |
| sounddevice | MIT | — |
| numpy | BSD-3-Clause | — |
| opencv-python | Apache-2.0 | — |
| Pillow | HPND | — |
| mss | MIT | — |
| Lingolay (алгоритмы) | GPL-3.0 | keremss7/lingolay |

**GameVoice RU:** GPL-3.0 (обусловлено Lingolay + piper-tts). Подробности: файл LICENSES.
