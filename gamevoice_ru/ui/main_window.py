"""
Main application window (Russian-language tkinter GUI).

Sections:
  - Source panel (Remote Play / file / monitor)
  - Subtitle region selector
  - Audio output selector + test button
  - Voice/TTS selector
  - Start/Stop button
  - Status indicators
  - Diagnostics panel (crop, raw OCR, cleaned text, spoken history)

All user-facing strings are in Russian.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, font, messagebox, scrolledtext, ttk
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Max history lines in diagnostics
_MAX_HISTORY = 100

# UI update interval
_UI_REFRESH_MS = 200


class MainWindow:
    def __init__(self):
        from gamevoice_ru.config import Settings
        self._settings = Settings()
        self._cfg = self._settings.config
        self._runner: Optional[object] = None
        self._ui_queue: queue.Queue = queue.Queue()

        self._root = tk.Tk()
        self._root.title("GameVoice RU — Русская озвучка субтитров")
        self._root.minsize(700, 600)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._populate_devices()
        self._populate_voices()
        self._restore_settings()
        self._schedule_ui_refresh()

        # Always check component readiness (not just first run)
        self._root.after(500, self._check_readiness_on_startup)

    def run(self) -> None:
        self._root.mainloop()

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = self._root

        # ── Top frame: source + region ───────────────────────────────────────
        top = ttk.LabelFrame(root, text="Источник видео", padding=6)
        top.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(top, text="Тип источника:").grid(row=0, column=0, sticky="w")
        self._source_var = tk.StringVar(value=self._cfg.source_type)
        source_cb = ttk.Combobox(top, textvariable=self._source_var, state="readonly", width=20,
                                  values=["remoteplay", "window", "monitor", "file"])
        source_cb.grid(row=0, column=1, padx=4, sticky="w")
        source_cb.bind("<<ComboboxSelected>>", self._on_source_changed)

        ttk.Label(top, text="Файл/папка:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._file_var = tk.StringVar(value=self._cfg.source_file_path)
        self._file_entry = ttk.Entry(top, textvariable=self._file_var, width=38)
        self._file_entry.grid(row=1, column=1, padx=4, sticky="ew")
        ttk.Button(top, text="Обзор…", command=self._browse_file, width=8).grid(row=1, column=2)

        ttk.Label(top, text="Заголовок окна:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._window_title_var = tk.StringVar(value=self._cfg.source_window_title)
        ttk.Entry(top, textvariable=self._window_title_var, width=38).grid(row=2, column=1, padx=4, sticky="ew")

        region_frame = ttk.Frame(top)
        region_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(region_frame, text="Выбрать область субтитров…", command=self._select_region).pack(side="left")
        self._region_label = ttk.Label(region_frame, text="(область не задана — берётся нижние 27%)", foreground="gray")
        self._region_label.pack(side="left", padx=8)

        top.columnconfigure(1, weight=1)

        # ── Audio + Voice ─────────────────────────────────────────────────────
        av_frame = ttk.Frame(root)
        av_frame.pack(fill="x", padx=8, pady=4)

        audio_frame = ttk.LabelFrame(av_frame, text="Аудиовыход", padding=6)
        audio_frame.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._device_var = tk.StringVar()
        self._device_combo = ttk.Combobox(audio_frame, textvariable=self._device_var, state="readonly", width=30)
        self._device_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(audio_frame, text="Тест звука", command=self._test_audio).grid(row=0, column=1)
        audio_frame.columnconfigure(0, weight=1)

        voice_frame = ttk.LabelFrame(av_frame, text="Голос TTS", padding=6)
        voice_frame.pack(side="left", fill="x", expand=True)

        self._voice_var = tk.StringVar()
        self._voice_combo = ttk.Combobox(voice_frame, textvariable=self._voice_var, state="readonly", width=28)
        self._voice_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(voice_frame, text="Прослушать", command=self._preview_voice).grid(row=0, column=1)
        voice_frame.columnconfigure(0, weight=1)

        # ── OCR FPS + Speed ───────────────────────────────────────────────────
        ctrl_frame = ttk.LabelFrame(root, text="Параметры", padding=6)
        ctrl_frame.pack(fill="x", padx=8, pady=4)

        ttk.Label(ctrl_frame, text="Частота OCR (кадр/с):").grid(row=0, column=0, sticky="w")
        self._fps_var = tk.DoubleVar(value=self._cfg.ocr_fps)
        ttk.Scale(ctrl_frame, variable=self._fps_var, from_=2, to=10, orient="horizontal", length=140).grid(row=0, column=1, padx=4)
        self._fps_label = ttk.Label(ctrl_frame, text=f"{self._cfg.ocr_fps:.0f}")
        self._fps_label.grid(row=0, column=2)
        self._fps_var.trace_add("write", lambda *_: self._fps_label.config(text=f"{self._fps_var.get():.0f}"))

        ttk.Label(ctrl_frame, text="Скорость речи:").grid(row=0, column=3, padx=(16, 0), sticky="w")
        self._rate_var = tk.DoubleVar(value=self._cfg.tts_rate)
        ttk.Scale(ctrl_frame, variable=self._rate_var, from_=0.85, to=1.4, orient="horizontal", length=140).grid(row=0, column=4, padx=4)
        self._rate_label = ttk.Label(ctrl_frame, text=f"{self._cfg.tts_rate:.2f}x")
        self._rate_label.grid(row=0, column=5)
        self._rate_var.trace_add("write", lambda *_: self._rate_label.config(text=f"{self._rate_var.get():.2f}x"))

        ttk.Label(ctrl_frame, text="Режим очереди:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._queue_mode_var = tk.StringVar(value=self._cfg.tts_queue_mode)
        ttk.Combobox(ctrl_frame, textvariable=self._queue_mode_var, state="readonly",
                     values=["natural", "fast"], width=10).grid(row=1, column=1, padx=4, sticky="w")

        # ── Start/Stop ────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill="x", padx=8, pady=(4, 2))

        self._start_btn = ttk.Button(btn_frame, text="▶  Начать озвучку", command=self._toggle_pipeline, width=22)
        self._start_btn.pack(side="left", padx=(0, 8))

        self._status_label = ttk.Label(btn_frame, text="● Остановлено", foreground="gray", font=("", 10, "bold"))
        self._status_label.pack(side="left")

        # ── Status indicators ─────────────────────────────────────────────────
        ind_frame = ttk.LabelFrame(root, text="Состояние", padding=4)
        ind_frame.pack(fill="x", padx=8, pady=2)

        self._ind_video = self._make_indicator(ind_frame, "Видео")
        self._ind_ocr   = self._make_indicator(ind_frame, "OCR")
        self._ind_tts   = self._make_indicator(ind_frame, "TTS")
        self._ind_audio = self._make_indicator(ind_frame, "Аудио")

        # ── Diagnostics ───────────────────────────────────────────────────────
        diag = ttk.Notebook(root)
        diag.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        # OCR tab
        ocr_tab = ttk.Frame(diag)
        diag.add(ocr_tab, text="OCR / субтитры")
        ttk.Label(ocr_tab, text="Последний OCR (сырой):").pack(anchor="w", padx=4, pady=(4, 0))
        self._raw_ocr_var = tk.StringVar(value="—")
        ttk.Label(ocr_tab, textvariable=self._raw_ocr_var, foreground="blue", wraplength=600, justify="left").pack(anchor="w", padx=12)
        ttk.Label(ocr_tab, text="Очищенный текст:").pack(anchor="w", padx=4, pady=(4, 0))
        self._clean_ocr_var = tk.StringVar(value="—")
        ttk.Label(ocr_tab, textvariable=self._clean_ocr_var, foreground="darkgreen", wraplength=600, justify="left").pack(anchor="w", padx=12)
        ttk.Separator(ocr_tab, orient="horizontal").pack(fill="x", padx=4, pady=4)
        ttk.Label(ocr_tab, text="История произнесённых реплик:").pack(anchor="w", padx=4)
        self._history_text = scrolledtext.ScrolledText(ocr_tab, height=8, state="disabled", font=("Consolas", 9))
        self._history_text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Stats tab
        stats_tab = ttk.Frame(diag)
        diag.add(stats_tab, text="Статистика")
        self._stats_text = scrolledtext.ScrolledText(stats_tab, height=12, state="disabled", font=("Consolas", 9))
        self._stats_text.pack(fill="both", expand=True, padx=4, pady=4)

        # Errors tab
        err_tab = ttk.Frame(diag)
        diag.add(err_tab, text="Ошибки")
        self._error_text = scrolledtext.ScrolledText(err_tab, height=12, state="disabled", font=("Consolas", 9))
        self._error_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _make_indicator(self, parent, label: str) -> ttk.Label:
        f = ttk.Frame(parent)
        f.pack(side="left", padx=8)
        ttk.Label(f, text=label).pack()
        lbl = ttk.Label(f, text="●", foreground="gray", font=("", 14))
        lbl.pack()
        return lbl

    # -------------------------------------------------------------------------
    # Populate combos
    # -------------------------------------------------------------------------

    def _populate_devices(self) -> None:
        from gamevoice_ru.pipeline.audio import list_output_devices
        try:
            devices = list_output_devices()
        except Exception:
            devices = []
        names = [str(d) for d in devices]
        self._device_list = devices
        if names:
            self._device_combo["values"] = names
            # Select configured device or default
            if self._cfg.audio_device:
                for i, d in enumerate(devices):
                    if self._cfg.audio_device.lower() in d.name.lower():
                        self._device_combo.current(i)
                        break
                else:
                    self._device_combo.current(0)
            else:
                self._device_combo.current(0)
        else:
            self._device_combo["values"] = ["(нет устройств)"]
            self._device_combo.current(0)

    def _populate_voices(self) -> None:
        from gamevoice_ru.pipeline.tts import PIPER_RUSSIAN_VOICES, SILERO_VOICES
        voice_names = []
        self._voice_keys = []
        for k, v in PIPER_RUSSIAN_VOICES.items():
            voice_names.append(v["display"])
            self._voice_keys.append(k)
        for k, v in SILERO_VOICES.items():
            voice_names.append(v["display"])
            self._voice_keys.append(k)
        self._voice_combo["values"] = voice_names
        # Select configured voice
        try:
            idx = self._voice_keys.index(self._cfg.tts_voice)
            self._voice_combo.current(idx)
        except ValueError:
            self._voice_combo.current(0)

    def _restore_settings(self) -> None:
        cfg = self._cfg
        self._source_var.set(cfg.source_type)
        self._file_var.set(cfg.source_file_path)
        self._window_title_var.set(cfg.source_window_title)
        self._update_region_label()

    # -------------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------------

    def _on_source_changed(self, _event=None) -> None:
        src = self._source_var.get()
        is_file = src == "file"
        state = "normal" if is_file else "disabled"
        self._file_entry.config(state=state)

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Выберите видео или папку с кадрами",
            filetypes=[("Видео / изображения", "*.mp4 *.avi *.mkv *.mov *.png *.jpg *.bmp"), ("Все файлы", "*.*")],
        )
        if path:
            self._file_var.set(path)

    def _select_region(self) -> None:
        """Open a region selector overlay (simplified: just ask for coords)."""
        # On Linux/CI, a full graphical selector overlay is impractical
        # In production Windows: would open a transparent fullscreen overlay
        win = tk.Toplevel(self._root)
        win.title("Область субтитров")
        win.grab_set()

        region = self._cfg.region
        entries = {}
        for i, (label, attr) in enumerate([("X:", "x"), ("Y:", "y"), ("Ширина:", "width"), ("Высота:", "height")]):
            ttk.Label(win, text=label).grid(row=i, column=0, padx=8, pady=4, sticky="w")
            var = tk.IntVar(value=getattr(region, attr))
            ttk.Entry(win, textvariable=var, width=10).grid(row=i, column=1, padx=8)
            entries[attr] = var

        def _apply():
            from gamevoice_ru.config import SubtitleRegion
            self._cfg.region = SubtitleRegion(**{k: v.get() for k, v in entries.items()})
            self._update_region_label()
            win.destroy()

        def _clear():
            for v in entries.values():
                v.set(0)

        ttk.Label(win, text="(0,0,0,0 = автоматически нижние 27%)").grid(row=4, column=0, columnspan=2, padx=8, pady=(0, 4))
        btn_f = ttk.Frame(win)
        btn_f.grid(row=5, column=0, columnspan=2, pady=8)
        ttk.Button(btn_f, text="Применить", command=_apply).pack(side="left", padx=4)
        ttk.Button(btn_f, text="Сбросить", command=_clear).pack(side="left", padx=4)
        ttk.Button(btn_f, text="Отмена", command=win.destroy).pack(side="left", padx=4)

    def _update_region_label(self) -> None:
        r = self._cfg.region
        if r.width <= 0 or r.height <= 0:
            self._region_label.config(text="(область не задана — нижние 27%)", foreground="gray")
        else:
            self._region_label.config(text=f"x={r.x}, y={r.y}, {r.width}×{r.height} px", foreground="black")

    def _test_audio(self) -> None:
        """Speak a test phrase on the selected device."""
        def _run():
            try:
                from gamevoice_ru.pipeline.tts import make_tts_engine, PiperTTS
                from gamevoice_ru.pipeline.audio import AudioPlayer, TEST_PHRASE_RU, find_device_by_name
                voice_key = self._selected_voice_key()
                tts = make_tts_engine(self._cfg.tts_engine, voice_key)
                if not tts.is_ready():
                    err = getattr(tts, "init_error", "TTS не готов")
                    self._ui_queue.put(("error", f"Тест аудио: {err}"))
                    return
                wav, err = tts.synthesize(TEST_PHRASE_RU)
                if err or wav is None:
                    self._ui_queue.put(("error", f"Тест аудио — синтез: {err}"))
                    return
                device_idx = self._selected_device_idx()
                player = AudioPlayer(device_index=device_idx, volume=self._cfg.tts_volume)
                play_err = player.play(wav, block=True)
                if play_err:
                    self._ui_queue.put(("error", f"Тест аудио — воспроизведение: {play_err}"))
            except Exception as e:
                self._ui_queue.put(("error", f"Тест аудио — исключение: {e}"))
        threading.Thread(target=_run, daemon=True).start()

    def _preview_voice(self) -> None:
        """Preview selected voice with a sample phrase."""
        self._test_audio()

    def _toggle_pipeline(self) -> None:
        if self._runner and getattr(self._runner, "is_running", lambda: False)():
            self._stop_pipeline()
        else:
            self._start_pipeline()

    def _start_pipeline(self) -> None:
        self._save_settings()
        from gamevoice_ru.pipeline.runner import PipelineRunner
        self._runner = PipelineRunner(
            config=self._cfg,
            on_ocr=lambda raw, clean: self._ui_queue.put(("ocr", raw, clean)),
            on_speak=lambda text: self._ui_queue.put(("speak", text)),
            on_error=lambda msg: self._ui_queue.put(("error", msg)),
            on_status_change=lambda s: self._ui_queue.put(("status", s)),
        )
        err = self._runner.start()
        if err:
            messagebox.showerror("Ошибка запуска", err)
            self._runner = None
            return
        self._start_btn.config(text="■  Остановить")
        self._status_label.config(text="● Работает", foreground="green")
        self._set_indicator(self._ind_video, True)

    def _stop_pipeline(self) -> None:
        if self._runner:
            self._runner.stop()
            self._runner = None
        self._start_btn.config(text="▶  Начать озвучку")
        self._status_label.config(text="● Остановлено", foreground="gray")
        for ind in (self._ind_video, self._ind_ocr, self._ind_tts, self._ind_audio):
            self._set_indicator(ind, False)

    def _save_settings(self) -> None:
        cfg = self._cfg
        cfg.source_type = self._source_var.get()
        cfg.source_file_path = self._file_var.get()
        cfg.source_window_title = self._window_title_var.get()
        cfg.ocr_fps = round(self._fps_var.get(), 1)
        cfg.tts_rate = round(self._rate_var.get(), 2)
        cfg.tts_queue_mode = self._queue_mode_var.get()
        cfg.tts_voice = self._selected_voice_key()
        # Audio device
        idx = self._device_combo.current()
        if 0 <= idx < len(self._device_list):
            cfg.audio_device = self._device_list[idx].name
        cfg.first_run = False
        self._settings.save()

    def _selected_voice_key(self) -> str:
        idx = self._voice_combo.current()
        if 0 <= idx < len(self._voice_keys):
            return self._voice_keys[idx]
        return self._cfg.tts_voice

    def _selected_device_idx(self) -> Optional[int]:
        idx = self._device_combo.current()
        if 0 <= idx < len(self._device_list):
            return self._device_list[idx].index
        return None

    # -------------------------------------------------------------------------
    # UI refresh (from main thread via queue)
    # -------------------------------------------------------------------------

    def _schedule_ui_refresh(self) -> None:
        self._root.after(_UI_REFRESH_MS, self._refresh_ui)

    def _refresh_ui(self) -> None:
        try:
            while True:
                msg = self._ui_queue.get_nowait()
                self._handle_ui_msg(msg)
        except queue.Empty:
            pass
        # Update stats
        if self._runner and getattr(self._runner, "is_running", lambda: False)():
            self._update_stats()
        self._root.after(_UI_REFRESH_MS, self._refresh_ui)

    def _handle_ui_msg(self, msg) -> None:
        kind = msg[0]
        if kind == "ocr":
            _, raw, clean = msg
            self._raw_ocr_var.set(raw[:200] if raw else "—")
            self._clean_ocr_var.set(clean[:200] if clean else "—")
            self._set_indicator(self._ind_ocr, bool(clean))
        elif kind == "speak":
            _, text = msg
            self._append_history(text)
            self._set_indicator(self._ind_tts, True)
            self._set_indicator(self._ind_audio, True)
        elif kind == "error":
            _, msg_text = msg
            self._append_error(msg_text)
        elif kind == "status":
            _, status = msg
            if status == "source_lost":
                self._set_indicator(self._ind_video, False)

    def _update_stats(self) -> None:
        if not self._runner:
            return
        s = self._runner.stats
        lines = [
            f"Кадров захвачено:    {s.frames_captured}",
            f"OCR запусков:        {s.ocr_runs}",
            f"OCR пропущено:       {s.ocr_skipped}",
            f"Произнесено реплик:  {s.phrases_spoken}",
            f"Дроп реплик:         {s.phrases_dropped}",
            f"Ошибки OCR:          {s.ocr_error_count}",
            f"Ошибки TTS:          {s.tts_error_count}",
            f"Задержка OCR:        {s.last_ocr_latency_ms}мс",
            f"Задержка TTS:        {s.last_tts_latency_ms}мс",
        ]
        self._set_text(self._stats_text, "\n".join(lines))

    def _append_history(self, text: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._stats_text["state"] = "normal"  # brief unlock to allow autoscroll check
        self._history_text.config(state="normal")
        self._history_text.insert("end", f"[{ts}] {text}\n")
        # Prune if too long
        lines = int(self._history_text.index("end-1c").split(".")[0])
        if lines > _MAX_HISTORY:
            self._history_text.delete("1.0", f"{lines - _MAX_HISTORY}.0")
        self._history_text.see("end")
        self._history_text.config(state="disabled")

    def _append_error(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._error_text.config(state="normal")
        self._error_text.insert("end", f"[{ts}] {msg}\n")
        self._error_text.see("end")
        self._error_text.config(state="disabled")

    @staticmethod
    def _set_text(widget: scrolledtext.ScrolledText, text: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.config(state="disabled")

    @staticmethod
    def _set_indicator(label: ttk.Label, active: bool) -> None:
        label.config(foreground="green" if active else "gray")

    # -------------------------------------------------------------------------
    # First-run wizard
    # -------------------------------------------------------------------------

    def _show_first_run_wizard(self) -> None:
        win = tk.Toplevel(self._root)
        win.title("Первый запуск — мастер настройки")
        win.grab_set()
        win.geometry("520x320")
        ttk.Label(win, text="Добро пожаловать в GameVoice RU!", font=("", 13, "bold")).pack(pady=(16, 4))
        steps = [
            "1. Выберите источник видео (Remote Play / файл).",
            "2. Выберите прямоугольную область субтитров.",
            "3. Выберите Bluetooth-колонку из списка аудиовыходов.",
            "4. Нажмите «Тест звука», чтобы проверить колонку.",
            "5. Нажмите «Начать озвучку».",
        ]
        for step in steps:
            ttk.Label(win, text=step, justify="left").pack(anchor="w", padx=24, pady=1)
        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=16, pady=8)
        ttk.Label(win, text="Если голосовая модель не скачана, запустите:\n"
                             "  python -m gamevoice_ru.pipeline.tts --download",
                  foreground="gray", justify="left").pack(padx=24, anchor="w")
        ttk.Button(win, text="Понятно, начинаем!", command=win.destroy).pack(pady=12)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def _check_readiness_on_startup(self) -> None:
        """Show setup wizard if OCR or TTS is missing."""
        try:
            from gamevoice_ru.ui.setup_wizard import check_readiness, _SetupWizard
            state = check_readiness()
            if not state["ocr_ok"] or not state["tts_ok"]:
                # Show non-blocking warning in diagnostics + optionally wizard
                msgs = []
                if not state["ocr_ok"]:
                    msgs.append("⚠️ OCR не найден. Установите Tesseract или Windows OCR ru-RU.")
                if not state["tts_ok"]:
                    msgs.append("⚠️ Голос TTS не скачан. Нажмите «Скачать голос» в настройках.")
                for m in msgs:
                    self._append_error(m)
                    self._status_label.config(text=f"● {msgs[0][:50]}", foreground="orange")
                # Show wizard on first run
                if self._cfg.first_run:
                    _SetupWizard(self._root, state).run()
        except Exception as e:
            logger.debug("Readiness check failed: %s", e)

    def _on_close(self) -> None:
        self._stop_pipeline()
        self._save_settings()
        self._root.destroy()


def run_gui() -> None:
    """Entry point for the GUI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = MainWindow()
    app.run()
