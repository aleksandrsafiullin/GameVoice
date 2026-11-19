"""
First-run setup wizard: shown when OCR engine or TTS model is missing.

Displays a clear Russian-language message explaining what is needed,
with one-click voice download and instructions for Windows OCR language pack.
"""
from __future__ import annotations

import logging
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def check_readiness() -> dict:
    """Return a dict with keys: ocr_ok, tts_ok, ocr_name, tts_error."""
    result = {"ocr_ok": False, "tts_ok": False, "ocr_name": "none", "tts_error": None}
    # OCR check
    try:
        if sys.platform == "win32":
            from gamevoice_ru.pipeline.ocr import WindowsOCR
            w = WindowsOCR("ru")
            if w.is_available():
                result["ocr_ok"] = True
                result["ocr_name"] = "WindowsOCR"
        if not result["ocr_ok"]:
            from gamevoice_ru.pipeline.ocr import TesseractOCR
            t = TesseractOCR("ru")
            if t.is_available():
                result["ocr_ok"] = True
                result["ocr_name"] = "TesseractOCR"
    except Exception:
        pass
    # TTS check
    try:
        from gamevoice_ru.pipeline.tts import PiperTTS
        tts = PiperTTS("ru_RU-dmitri-medium")
        result["tts_ok"] = tts.is_ready()
        if not tts.is_ready():
            result["tts_error"] = tts.init_error
    except Exception as e:
        result["tts_error"] = str(e)
    return result


def maybe_show_setup_wizard(parent: Optional[tk.Tk] = None) -> None:
    """
    Show the setup wizard if OCR or TTS is not ready.
    Does nothing if everything is configured correctly.
    """
    state = check_readiness()
    if state["ocr_ok"] and state["tts_ok"]:
        return
    _SetupWizard(parent, state).run()


class _SetupWizard:
    def __init__(self, parent, state: dict):
        self._state = state
        self._win = tk.Toplevel(parent) if parent else tk.Tk()
        self._win.title("GameVoice RU — Первоначальная настройка")
        self._win.geometry("580x480")
        self._win.resizable(False, False)
        if parent:
            self._win.grab_set()
        self._build()

    def run(self):
        self._win.mainloop() if not self._win.master else self._win.wait_window()

    def _build(self):
        win = self._win
        s = self._state

        ttk.Label(win, text="Настройка GameVoice RU", font=("", 14, "bold")).pack(pady=(12, 4))
        ttk.Label(
            win,
            text="Перед запуском требуется установить компоненты.",
            font=("", 10),
        ).pack(pady=(0, 8))

        # Status frame
        status_frame = ttk.LabelFrame(win, text="Статус компонентов", padding=8)
        status_frame.pack(fill="x", padx=16, pady=4)

        ocr_color = "green" if s["ocr_ok"] else "red"
        ocr_text = f"✓ OCR: {s['ocr_name']}" if s["ocr_ok"] else "✗ OCR: не найден"
        ttk.Label(status_frame, text=ocr_text, foreground=ocr_color, font=("", 10)).grid(
            row=0, column=0, sticky="w", padx=4, pady=2
        )

        tts_color = "green" if s["tts_ok"] else "red"
        tts_text = "✓ TTS: Piper (готов)" if s["tts_ok"] else "✗ TTS: модель не скачана"
        ttk.Label(status_frame, text=tts_text, foreground=tts_color, font=("", 10)).grid(
            row=1, column=0, sticky="w", padx=4, pady=2
        )

        # Instructions
        instr_frame = ttk.LabelFrame(win, text="Инструкции", padding=8)
        instr_frame.pack(fill="both", expand=True, padx=16, pady=4)
        self._instr_text = scrolledtext.ScrolledText(instr_frame, height=10, state="normal", font=("", 9), wrap="word")
        self._instr_text.pack(fill="both", expand=True)
        self._instr_text.insert("end", self._build_instructions())
        self._instr_text.config(state="disabled")

        # Action buttons
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", padx=16, pady=8)

        self._download_btn = ttk.Button(
            btn_frame,
            text="⬇  Скачать голос Piper (Дмитрий, ~65 МБ)",
            command=self._download_voice,
            state="normal" if not s["tts_ok"] else "disabled",
        )
        self._download_btn.pack(side="left", padx=(0, 8))

        ttk.Button(btn_frame, text="Закрыть", command=self._win.destroy).pack(side="right")

        self._progress = ttk.Progressbar(win, mode="indeterminate")
        self._status_lbl = ttk.Label(win, text="")
        self._progress.pack(fill="x", padx=16)
        self._status_lbl.pack()

    def _build_instructions(self) -> str:
        s = self._state
        lines = []
        if not s["tts_ok"]:
            lines += [
                "── Голос Piper TTS (обязательно) ──────────────────────────────\n",
                "Нажмите кнопку «Скачать голос Piper» — программа автоматически\n",
                "скачает голос «Дмитрий» (~65 МБ, лицензия CC0-1.0, бесплатно).\n",
                "\n",
                "Или вручную:\n",
                "  python -m gamevoice_ru.pipeline.tts --download piper\n\n",
            ]
        if not s["ocr_ok"] and sys.platform == "win32":
            lines += [
                "── Windows OCR (рекомендуется для Windows 10/11) ──────────────\n",
                "1. Настройки → Время и язык → Язык\n",
                "2. Нажмите «Добавить язык» → выберите «Русский»\n",
                "3. При установке отметьте «Распознавание рукописного текста»\n",
                "   (это включает движок Windows OCR)\n\n",
                "Или через PowerShell (Администратор):\n",
                "  Add-WindowsCapability -Online -Name Language.OCR~~~ru-RU~0.0.1.0\n\n",
            ]
        if not s["ocr_ok"] and sys.platform != "win32":
            lines += [
                "── Tesseract OCR (Linux/macOS) ─────────────────────────────────\n",
                "Ubuntu/Debian:\n",
                "  sudo apt install tesseract-ocr tesseract-ocr-rus\n\n",
                "macOS (Homebrew):\n",
                "  brew install tesseract tesseract-lang\n\n",
            ]
        if not lines:
            lines = ["Все компоненты готовы к работе.\n"]
        return "".join(lines)

    def _download_voice(self):
        self._download_btn.config(state="disabled")
        self._progress.start()
        self._status_lbl.config(text="Скачивание голоса…")

        def _run():
            from gamevoice_ru.pipeline.tts import download_piper_voice

            def _progress_cb(msg):
                self._status_lbl.config(text=msg)

            err = download_piper_voice("ru_RU-dmitri-medium", progress_cb=_progress_cb)
            self._progress.stop()
            if err:
                self._status_lbl.config(text=f"Ошибка: {err}")
                self._download_btn.config(state="normal")
            else:
                self._status_lbl.config(text="✓ Голос скачан! Перезапустите приложение.")
                self._refresh_status()

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_status(self):
        new_state = check_readiness()
        self._state = new_state
        # Rebuild would require more refactoring; just update labels
        self._status_lbl.config(
            text="✓ TTS готов! Нажмите «Закрыть» и запустите озвучку."
        )
