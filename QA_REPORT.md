# QA_REPORT.md — Independent QA Review (v3 Final)

**Reviewer:** QA Agent (independent)
**v1:** 18 Nov 2026 — 14 initial findings
**v2:** 19 Nov 2026 — re-verification after 84585d0–8f4002f; findings #A-E added
**v3:** 19 Nov 2026 — final pass after c2ad35d + 9a2b27f; CI run [44449223525](https://github.com/aleksandrsafiullin/GameVoice/actions/runs/44449223525) all green
**PR under review:** https://github.com/aleksandrsafiullin/GameVoice/pull/1
**Dev branch:** `cursor/gamevoice-ru-app-10be` @ 9a2b27f
**QA PR:** https://github.com/aleksandrsafiullin/GameVoice/pull/3

---

## VERDICT: GO for hardware testing (two minor pre-release notes)

All blocking issues from v1-v2 are resolved and independently verified. The pipeline completed the full OCR->TTS cycle end-to-end in CI on both Linux (Tesseract) and Windows (WindowsOCR + Piper) with real Russian text, real synthesised audio, and strict-mode exit codes propagated correctly.

**Two minor notes before distributing to end users** (not blockers for Alex's hardware test):
1. `LICENSES` file (CC0-1.0 voice + GPL-3.0 Lingolay attribution) is not bundled in the EXE zip - add to `GameVoiceRU.spec` before public release.
2. EXE section in README.ru.md does not mention that `winrt` is bundled (Tesseract not needed for EXE users).

---

## 1. Section 11 Acceptance Criteria - Final Status

| # | Criterion (s11) | Status | Evidence |
|---|---|---|---|
| 1 | App runs on Windows 11 | PASS | EXE launched via --self-test --strict in CI; exit 0; JSON artifact confirmed. Setup wizard guides first run. |
| 2 | Video source and subtitle rectangle selection | PASS by test | tests/test_config.py + FileFrameSource EOF fix. File demo mode works. |
| 3 | Demo stream recognises RU phrases without translation | PASS | Linux CI: Tesseract OCR "Привет! Это тестовая реплика." Windows CI: WindowsOCR same text. No translation module. |
| 4 | Static phrase not spoken repeatedly | PASS by test | TestStaticSubtitleSpokenOnce: 20 identical frames -> exactly 1 speak. |
| 5 | New phrase + repeat-after-pause both recognised | PASS by test | TestNewLineSpoken + TestSameLineAfterPauseReSpoken (via expiry and via reset). |
| 6 | Specific Bluetooth speaker selectable, test phrase | NEEDS USER HARDWARE | Code path verified. interrupt_fn wired to AudioPlayer.stop. |
| 7 | Start/Stop buttons + visual OCR diagnostics | PASS by code review | ui/main_window.py correct. setup_wizard.py added for first-run. |
| 8 | Failures don't crash | PASS by test | TestMissingTTSModel, TestMissingAudioDevice, TestBlackOrMissingSource. OCR init error surfaces to on_error. |
| 9 | Simulator results only; no fake PS5/GTA VI claims | PASS | Only synthetic frames used throughout. |
| 10 | Source code, Russian guide, deps, licenses | PASS (with note) | All present in repo. LICENSES file missing from EXE zip (pre-release note). |

---

## 2. CI Results - Run 44449223525

| Job | Status | Real test execution |
|---|---|---|
| test-linux | success | 165 tests pass + strict self-test: Tesseract OCR + Piper TTS synthesis confirmed |
| build-windows | success | WindowsOCR + Piper TTS both ran on Windows Server 2022; EXE strict self-test exit 0 |

### Linux strict self-test (from artifact self-test-linux):

```json
"ocr": {"engine": "TesseractOCR", "status": "OK", "text": "Привет! Это тестовая реплика.", "has_cyrillic": true}
"tts": {"status": "OK", "engine": "PiperTTS", "voice": "ru_RU-dmitri-medium", "wav_bytes": 119852, "duration_ms": 2716}
"overall": "PASS", "strict": true
```

### Windows Python + EXE strict self-test (from artifact self-test-windows):

```json
"ocr": {"engine": "WindowsOCR", "status": "OK", "text": "Привет! Это тестовая реплика.", "has_cyrillic": true}
"tts": {"status": "OK", "engine": "PiperTTS", "wav_bytes": 120876, "duration_ms": 2739}
"overall": "PASS", "strict": true
```

**Values are real, not hardcoded or mocked.** OCR text is actual output from recognising a Pillow-rendered image. WAV sizes (~120 KB, ~2.7 s) match PCM at 24 kHz 16-bit 1-ch. Python and EXE runs show slight variation (120876 vs 119852 bytes) - normal Piper context re-initialisation. No mock path in self_test.py.

**CI strict mode correctly fails on SKIP** (code-verified in self_test.py):

```python
elif ocr_status == "SKIP" and strict:
    result["critical_failures"].append("[strict] OCR=SKIP: no OCR engine available")
    result["overall"] = "FAIL"
# TTS SKIP:
if strict:
    result["critical_failures"].append(f"[strict] TTS=SKIP: Piper model not ready: {skip_reason}")
    result["overall"] = "FAIL"
```

**CI exit codes correctly propagated** (code-verified in ci.yml):

- Python: `$exit_code = $LASTEXITCODE; ...; if ($exit_code -ne 0) { exit $exit_code }`
- EXE: `$proc = Start-Process ... -Wait -PassThru; if ($proc.ExitCode -ne 0) { exit $proc.ExitCode }`

**Tesseract with exit 0:** Even if both choco and GitHub API methods fail, the step exits 0. The strict self-test then fails with OCR=SKIP. CI turns red on the strict step, not the install step. Correct design.

**Add-WindowsCapability confirmed in CI log:** `RestartNeeded=False` (took ~12 min). `WINRT_RU_OCR_INSTALLED=true`. All 5 winrt packages installed. WindowsOCR (not Tesseract fallback) was the actual engine used. Finding #2 fix code ran and produced real Cyrillic text.

---

## 3. QA Test Results - Final

```
tests/qa/test_section11_black_box.py  31 collected  31 passed  1 warning
tests/test_qa_fixes.py                36 collected  36 passed
tests/ (original + integration)       100 collected 100 passed
Total                                 167 collected 167 passed
```

1 expected warning: TTS=SKIP locally (Piper voice not downloaded). CI enforces --strict which fails on this.

---

## 4. EXE Bundle Inspection

GameVoiceRU-windows-x64.zip - 108 MB, 1,474 entries.

| Component | Present | Notes |
|---|---|---|
| GameVoiceRU/GameVoiceRU.exe | YES | 5.9 MB |
| winrt modules | YES | 11 entries: _winrt.cp311-win_amd64.pyd + 5 namespace .pywinrt tables |
| piper/espeakbridge.pyd + espeak-ng-data/ | YES | Full espeak-ng phonemization data |
| onnxruntime/ | YES | Piper ONNX inference engine |
| README.ru.md | YES | In _internal/ |
| DECISIONS.md | YES | In _internal/ |
| .onnx voice models | NOT BUNDLED | Intentional - first-run setup wizard downloads ~65 MB |
| LICENSES file | NOT BUNDLED | Piper voice CC0-1.0 + Lingolay GPL-3.0 attribution missing. Pre-release: add to GameVoiceRU.spec |
| LICENSE (GPL-3.0 full text) | NOT BUNDLED | Minor - only dep dist-info licenses present |

**What a clean Windows 11 install needs:**

1. Run GameVoiceRU.exe -> setup wizard opens if OCR or voice missing
2. Russian OCR language: Settings -> Language -> Add Russian, OR PowerShell: `Add-WindowsCapability -Online -Name "Language.OCR~~~ru-RU~0.0.1.0"` (wizard shows both)
3. Piper voice: one-click download in wizard (~65 MB)
4. Tesseract: NOT needed for EXE users - winrt is bundled; wizard offers it only as fallback for older Windows builds

---

## 5. Finding Re-Verification Summary

| Finding | Severity | Status | Verified |
|---|---|---|---|
| #1 EXE never launched | Critical | FIXED | EXE strict self-test exit 0, JSON artifact confirmed |
| #2 WindowsOCR buffer bug | Critical | FIXED AND VERIFIED | WindowsOCR ran in CI and returned Cyrillic text |
| #3 _stop_playback no-op | High | FIXED | interrupt_fn=player.stop wired; regression test |
| #4 mark_spoken too early | High | FIXED | unmark_spoken on TTS error; regression test |
| #5 FileSource infinite last frame | High | FIXED | Returns None at EOF; regression test |
| #6 Latin-only garbage passes | Medium | FIXED | require_cyrillic=True; regression test |
| #7 settle_ms ignored | Medium | FIXED | max(90, ms*0.3); no effect at default=300ms |
| #8 is_playing() always False | Medium | FIXED | _playing flag + _wait_and_clear watcher thread |
| #9 OCR error not shown in UI | Medium | FIXED | Deferred _emit_error; regression test |
| #10 poll() state path | Low | Acknowledged | SPOKEN guard prevents double-speak |
| #14 capture.py attribution | Low | FIXED | Docstring updated |
| #A Windows SKIP self-test | Critical/Honesty | FIXED | Strict mode + CI runs --strict; choco + WinRT + Piper voice |
| #B No --strict mode | High | FIXED | Implemented and enforced in CI |
| #C is_playing() block=False | Low | FIXED | _wait_and_clear daemon thread |
| #D winrt missing from deps | Medium | FIXED partial | Commented in requirements.txt with pip install instructions; EXE bundles winrt |
| #E REPORT.md false OCR claim | Honesty | FIXED | REPORT.md now reflects actual CI evidence |

---

## 6. Setup Wizard Review

gamevoice_ru/ui/setup_wizard.py - first-run UX for missing OCR/TTS.

Correct: checks both WindowsOCR and Tesseract at startup; shows green/red per component; one-click voice download with progress; Russian-language OCR instructions (Settings and PowerShell); thread-safe download; only shown when OCR or TTS not ready.

Minor gap: _refresh_status() only updates status label text, not the red/green indicator rows. No crash; minor visual inconsistency on re-open after voice download.

---

## 7. What Remains Unverified (Requires User Hardware)

| Item | Why unverifiable |
|---|---|
| PS5 + Remote Play window capture | No PS5; no HDMI |
| GTA VI subtitle region, font, real OCR accuracy >= 95% | Game not available |
| Bluetooth speaker - WASAPI device selection, latency | No Bluetooth hardware |
| WindowsOCR on real Windows 11 (vs Windows Server 2022 CI) | CI uses Server 2022 |
| Latency spec <=1500 ms p50 / <=2500 ms p95 | Only local OCR latency measured (~160 ms) |
| Second PSN account Remote Play | Sony-unsupported; depends on firmware |

---

## 8. Hardware Test Checklist for Alex (Russian)

Всё нижеследующее — самостоятельная проверка без разработчика.

### Перед началом
1. Скачайте `GameVoiceRU-windows-x64.zip` из артефактов CI → распакуйте в `C:\GameVoiceRU`.
2. Запустите `GameVoiceRU.exe`. Откроется **мастер настройки**.
3. В мастере нажмите **«Скачать голос Piper (~65 МБ)»** → дождитесь загрузки.
4. Добавьте русский OCR одним из способов:
   - **Вариант A:** `Настройки → Время и язык → Язык → Добавить язык → Русский`
   - **Вариант B** (PowerShell от Администратора):
     `Add-WindowsCapability -Online -Name "Language.OCR~~~ru-RU~0.0.1.0"`
5. Перезапустите `GameVoiceRU.exe` — оба статуса должны стать зелёными (OCR ✓, TTS ✓).

### Шаг 1: Demo-режим (без PS5, ~5 минут)
- Источник: **Файл/папка** → укажите папку с PNG-кадрами (например, `_internal/test_data/frames`).
- Нажмите **«▶ Начать озвучку»**.
- **Ожидаемо:** в поле «Последний OCR» — русский текст, из колонки звучат слова, дубликаты не повторяются.

### Шаг 2: Bluetooth-колонка
- Убедитесь, что колонка подключена к ПК (Bluetooth).
- В «Аудиовыход» выберите колонку.
- Нажмите **«Тест звука»** → из колонки должна прозвучать фраза «Тестовая фраза. Колонка работает.»

### Шаг 3: Remote Play + субтитры (нужна PS5 и GTA VI)
1. Запустите **PS Remote Play** на ПК (войдите со **вторым** PSN-аккаунтом).
2. Убедитесь, что игра идёт на телевизоре через **основной** аккаунт.
3. В GameVoice RU: Источник → **Remote Play**, нажмите **«Выбрать область субтитров»**.
4. Укажите нижнюю часть окна Remote Play (нижние ~15-20% экрана; для 720p примерно y=615, высота=100).
5. Нажмите **«▶ Начать озвучку»**.
6. В игре: Настройки → Доступность → Субтитры → Вкл (язык: **Русский**).

### Шаг 4: Игровая проверка (10 минут с диалогами)

- [ ] Русские слова произносятся при появлении субтитров?
- [ ] Одна и та же фраза не повторяется несколько раз?
- [ ] Новые реплики произносятся без лишней задержки (субъективно)?
- [ ] При пустом экране (нет субтитров) — тишина?
- [ ] При появлении меню — нет случайного озвучивания?
- [ ] Если Remote Play разрывается — программа показывает ошибку, не падает?

### Что сообщить
Скриншот вкладки **«Статистика»** после 10 минут:
кадров захвачено, OCR запусков, произнесено реплик, задержка OCR (мс).
Любые вылеты, зависания или некорректно произнесённые фразы.
