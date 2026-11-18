# DECISIONS.md — Архитектурные решения GameVoice RU

## Аудит Lingolay (keremss7/lingolay, GPL-3.0)

**Дата аудита:** 18 ноября 2026 г.  
**Репозиторий:** https://github.com/keremss7/lingolay  
**Лицензия:** GPL-3.0

### Что взято из Lingolay (с сохранением атрибуции)

| Компонент | Файл Lingolay | Файл GameVoice RU | Объём переиспользования |
|---|---|---|---|
| `TextStabilizer` — алгоритм стабилизации субтитров | `src/lingolay/text/cleaning.py` | `gamevoice_ru/pipeline/stabilizer.py` | Алгоритм адаптирован (~60%): seq_ratio, is_minor_jitter, is_growth, логика settle/cooldown/expiry |
| `PiperEngine` — синтез через Piper TTS | `src/lingolay/tts/piper_engine.py` | `gamevoice_ru/pipeline/tts.py` | Паттерн загрузки модели, _pcm_to_wav, length_scale из rate (~40%) |
| `TesseractOCR` — OCR через pytesseract | `src/lingolay/ocr/tesseract_ocr.py` | `gamevoice_ru/pipeline/ocr.py` | Структура класса, lang-map для русского (~30%) |
| `clean_ocr_text` / `is_garbage` | `src/lingolay/text/cleaning.py` | `gamevoice_ru/pipeline/stabilizer.py` | Переработано для кириллицы |

### Что НЕ взято из Lingolay

| Компонент | Причина |
|---|---|
| Переводческий слой (NLLB, DeepL) | GameVoice RU работает **без перевода** — прямой путь OCR ru → TTS ru |
| Turkish-specific postprocessing | Не нужен для русского |
| `app.py` / `overlay` | UI написан заново (tkinter вместо Qt/SDL) |
| `translation/` | Явно исключено из зависимостей |
| Silero gender detection | P1-фича, не MVP |

### GPL-3.0 соответствие

- Исходный код GameVoice RU распространяется под GPL-3.0.
- Уведомление об авторских правах Lingolay сохранено в заголовках соответствующих файлов.
- Любое распространение модифицированного кода должно сопровождаться исходниками.

---

## Выбор OCR движка

**Решение:** Tesseract с `rus.traineddata` (первичный); Windows OCR (WinRT) — опционально.

**Почему:** Tesseract кросс-платформенный, работает в CI без Windows. Windows OCR даёт более высокое качество на реальных субтитрах, но требует установленного языкового пакета и Windows 10+. Fallback реализован через `make_ocr_engine("winrt")`.

---

## Выбор TTS движка

**Решение:** Piper TTS (`piper-tts` Python-пакет) + опциональный Silero.

**Голоса Piper для русского:**
- `ru_RU-dmitri-medium` — мужской, лицензия CC0-1.0 (свободно для любых целей)
- `ru_RU-irina-medium` — женский, лицензия CC0-1.0

Источник: https://huggingface.co/rhasspy/piper-voices

**Silero:** CC-BY 4.0 — допускает коммерческое использование с указанием авторства. Пометить в UI как "CC-BY 4.0 — с указанием авторства".

**Почему не edge-tts / Azure TTS:** нет API-ключей, MVP требует офлайн-синтез.

---

## Выбор библиотеки захвата экрана

**Решение:** `mss` (кросс-платформенный) для региона экрана; `wgcapture` (Windows Graphics Capture) — опциональный для захвата конкретного окна.

**Почему не DXcam:** требует DirectX, нет Python-пакета без C++ зависимостей.  
**Почему не BitBlt:** устаревший WinAPI, не работает с UWP/hardware-accelerated окнами.

---

## Выбор библиотеки аудиовывода

**Решение:** `sounddevice` (PortAudio) — поддерживает WASAPI эксклюзивный/общий режим на Windows, ALSA на Linux.

**Почему не winsound:** нет выбора устройства.  
**Почему не pygame.mixer:** нет прямого WASAPI device selection.

---

## Выбор UI-фреймворка

**Решение:** `tkinter` — встроен в Python, не требует дополнительных зависимостей, работает в CI.

**Почему не PyQt5/PySide6:** лицензия Qt LGPL создаёт сложности при бандлинге. Tkinter достаточен для MVP.

---

## Режим «Прямой дубляж» (Direct Russian Dub)

Схема: `OCR (ru) → Stabilizer → TTS (ru) → Audio`

Переводческий конвейер (`translation/`) полностью отсутствует. Это упрощает зависимости (~1.5 ГБ NLLB модель не нужна), уменьшает задержку и устраняет класс ошибок переводчика.

---

## Почему не чистый форк Lingolay

Lingolay проектировался с translation-first архитектурой: каждый компонент передаёт `source_lang`/`target_lang` вниз по стеку. Добавить `direct_dub` режим без translation потребовало бы изменения сигнатур во всём стеке или сложных условных обходов. Проще адаптировать ключевые алгоритмы (stabilizer, piper engine) в новое приложение, сохраняя лицензию и атрибуцию.
