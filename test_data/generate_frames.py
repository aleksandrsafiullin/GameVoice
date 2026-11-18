"""
Generate synthetic Russian subtitle test frames.

Creates 40+ frame events covering:
  - single-line, 2-line subtitles
  - white/yellow text with dark outline
  - short/long replies
  - typewriter (char-by-char) arrival
  - same phrase twice (after disappearance)
  - OCR noise / flicker
  - blank frames (subtitle disappears)
  - HUD text outside the subtitle region (should be ignored)

Output: test_data/frames/*.png  +  test_data/test_events.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

FRAMES_DIR = Path(__file__).parent / "frames"
EVENTS_FILE = Path(__file__).parent / "test_events.json"

# Frame dimensions
W, H = 1280, 720

# Subtitle area (bottom portion)
SUB_Y_START = int(H * 0.73)
SUB_HEIGHT = H - SUB_Y_START

# Font size
FONT_SIZE = 32

# Russian test subtitles (synthetic — not GTA VI text)
SUBTITLE_EVENTS = [
    # (event_id, text, style, notes)
    # --- Single-line normal subtitles ---
    ("e01", "Привет! Как дела?", "white_outline", "single line, short"),
    ("e02", "Мне нужно поговорить с тобой.", "white_outline", "single line, medium"),
    ("e03", "Это очень важно — не забудь.", "white_outline", "single line with dash"),
    ("e04", "Стой! Не двигайся!", "yellow_outline", "yellow text, short"),
    ("e05", "Ты слышишь меня?", "white_outline", "question mark"),
    ("e06", "Нет. Это невозможно.", "white_outline", "sentence with period"),
    ("e07", "Следуй за мной прямо сейчас.", "white_outline", "command"),
    ("e08", "Я думал, ты знаешь правду.", "white_outline", "past tense"),
    ("e09", "Скажи им, что всё в порядке.", "white_outline", "longer line"),
    ("e10", "Осторожно!", "white_outline", "single word exclamation"),
    # --- Two-line subtitles ---
    ("e11", "Эта ситуация вышла из-под контроля.\nМы должны действовать немедленно.", "white_outline", "2 lines"),
    ("e12", "— Где ты был всё это время?\n— Не твоё дело.", "white_outline", "dialogue 2 lines"),
    ("e13", "Деньги уже переведены.\nПроверь свой счёт.", "white_outline", "2 lines, money topic"),
    ("e14", "Он не вернётся.\nЯ уверен в этом.", "white_outline", "2 lines negative"),
    # --- Typewriter arrivals (same text as multiple partial frames) ---
    ("e15a", "Это важное со", "white_outline", "typewriter partial 1"),
    ("e15b", "Это важное сообщение", "white_outline", "typewriter partial 2"),
    ("e15c", "Это важное сообщение для всех.", "white_outline", "typewriter complete"),
    # --- Same phrase twice (after blank) ---
    ("e16", "Встретимся у моста.", "white_outline", "phrase 1st occurrence"),
    ("e16_blank", "", "blank", "blank between repeats"),
    ("e16r", "Встретимся у моста.", "white_outline", "same phrase 2nd occurrence"),
    # --- OCR noise / flicker ---
    ("e17a", "Брось оружие!", "white_outline", "normal"),
    ("e17b", "Брось оруж1е!", "white_outline", "flicker: OCR noise"),
    ("e17c", "Брось оружие!", "white_outline", "stable again"),
    # --- Long line ---
    ("e18", "Я не могу поверить в то, что всё это происходит прямо у нас под носом.", "white_outline", "long single line"),
    # --- Short lines ---
    ("e19", "Да.", "white_outline", "one word"),
    ("e20", "Нет!", "white_outline", "one word exclamation"),
    ("e21", "Уходи.", "white_outline", "one word command"),
    # --- Dynamic background (simulate different backgrounds) ---
    ("e22", "Объект найден в здании.", "white_outline_dynamic", "dynamic bg"),
    ("e23", "Система под угрозой.", "white_outline_dynamic", "dynamic bg 2"),
    # --- Blank frames ---
    ("blank1", "", "blank", "blank subtitle area"),
    ("blank2", "", "blank", "another blank"),
    # --- HUD text (should NOT be spoken; outside subtitle region) ---
    # These are rendered in the HUD area (top of frame), not subtitle area
    ("hud1", "", "hud", "HUD: $125,000"),
    ("hud2", "", "hud", "HUD: 3 wanted stars"),
    # --- Mixed content ---
    ("e24", "Дорога закрыта до утра.", "white_outline", "night setting"),
    ("e25", "Слушай меня внимательно, это жизненно важно.", "white_outline", "long emphasis"),
    ("e26", "Удачи, брат.", "white_outline", "farewell"),
    ("e27", "Мы выиграли!", "yellow_outline", "victory"),
    ("e28", "Система обнаружила нарушение.", "white_outline", "system message"),
    ("e29", "Телефон не отвечает.", "white_outline", "phone"),
    ("e30", "Не подведи меня.", "white_outline", "trust"),
    # --- Punctuation edge cases ---
    ("e31", "«Это невероятно», — сказал он.", "white_outline", "Russian quotes and dash"),
    ("e32", "Ты… ты серьёзно?!", "white_outline", "ellipsis and double punct"),
    ("e33", "Третий, четвёртый, пятый — все живы!", "white_outline", "numbers text"),
    # --- Blank end ---
    ("end_blank", "", "blank", "end of sequence"),
]


def _get_font(size: int):
    """Try to load a system font that supports Cyrillic."""
    font_candidates = [
        # Linux paths
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        # Windows paths
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_subtitle_frame(
    text: str,
    style: str,
    hud_text: str = "",
    bg_vary: int = 0,
) -> Image.Image:
    """Draw one frame with subtitle rendered in the bottom region."""
    # Background: dark gradient / game-like
    base_color = (20 + bg_vary, 15 + bg_vary // 2, 30 + bg_vary)
    img = Image.new("RGB", (W, H), base_color)
    draw = ImageDraw.Draw(img)

    # Draw some fake game scenery lines (HUD area)
    draw.rectangle([0, 0, W, 60], fill=(10, 10, 10, 200))
    if hud_text:
        hud_font = _get_font(20)
        draw.text((16, 16), hud_text, fill=(255, 220, 0), font=hud_font)

    if not text:
        return img

    font = _get_font(FONT_SIZE)

    # Determine text color
    if "yellow" in style:
        text_color = (255, 230, 0)
    else:
        text_color = (255, 255, 255)

    outline_color = (0, 0, 0)

    lines = text.split("\n")
    total_text_height = len(lines) * (FONT_SIZE + 8)
    y_start = H - 20 - total_text_height

    for i, line in enumerate(lines):
        # Measure text width
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except AttributeError:
            text_w, text_h = draw.textsize(line, font=font)

        x = (W - text_w) // 2
        y = y_start + i * (FONT_SIZE + 8)

        # Draw dark outline (4 directions)
        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2), (0, -2), (0, 2), (-2, 0), (2, 0)]:
            draw.text((x + dx, y + dy), line, fill=outline_color, font=font)

        # Draw text
        draw.text((x, y), line, fill=text_color, font=font)

    return img


def generate_all():
    if not PIL_OK:
        print("ERROR: Pillow not installed. pip install Pillow")
        sys.exit(1)

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    events = []
    frame_index = 0

    for event_id, text, style, notes in SUBTITLE_EVENTS:
        hud_text = ""
        if style == "hud":
            if "125" in event_id:
                hud_text = "$125,000"
            else:
                hud_text = "★★★"
            # Blank subtitle area for HUD frames
            text = ""

        bg_vary = (frame_index * 7) % 40
        img = _draw_subtitle_frame(text, style, hud_text=hud_text, bg_vary=bg_vary)

        fname = f"{frame_index:04d}_{event_id}.png"
        fpath = FRAMES_DIR / fname
        img.save(fpath)

        events.append({
            "frame_index": frame_index,
            "event_id": event_id,
            "file": fname,
            "text": text,
            "style": style,
            "notes": notes,
            "expected_spoken": bool(text) and style not in ("blank", "hud"),
        })
        frame_index += 1

        # Duplicate some frames to simulate stable subtitle (3 copies)
        if text and "blank" not in style and "hud" not in style and not event_id.endswith("_blank"):
            for rep in range(1, 3):
                img2 = _draw_subtitle_frame(text, style, bg_vary=(bg_vary + rep * 5) % 40)
                fname2 = f"{frame_index:04d}_{event_id}_dup{rep}.png"
                img2.save(FRAMES_DIR / fname2)
                events.append({
                    "frame_index": frame_index,
                    "event_id": f"{event_id}_dup{rep}",
                    "file": fname2,
                    "text": text,
                    "style": style,
                    "notes": f"duplicate {rep} of {event_id}",
                    "expected_spoken": False,  # duplicate should NOT be spoken again
                })
                frame_index += 1

    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

    print(f"Generated {frame_index} frames in {FRAMES_DIR}")
    print(f"Events manifest: {EVENTS_FILE}")
    unique_speak = sum(1 for e in events if e["expected_spoken"])
    print(f"Expected spoken phrases: {unique_speak}")
    return events


if __name__ == "__main__":
    generate_all()
