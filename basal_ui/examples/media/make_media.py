"""The bundled media samples of basal_ui/examples/media.jsonl: three images (Pillow) and two voice clips (macOS `say`).

    uv run --with pillow python basal_ui/examples/media/make_media.py

Writes checkout.png, invoice.png, chart.png, complaint_pl.wav and appointment_en.wav next to this file. The element
boxes of the checkout screenshot (CHECKOUT, [x, y, w, h] in pixels) are copied verbatim into the "elements" of the
media-01-checkout-elements example; each box carries a numbered badge so the model can tie an option to its pixels.

A clip of your own: put a WAV, FLAC or MP3 of at most 30 s (the basal-omni audio window) in this directory and add a
line to media.jsonl, e.g.

    {"id": "media-06-my-clip", "state": "", "lang": "en", "media": [{"type": "audio", "file": "my_clip.wav"}],
     "questions": {"intent": {"type": "choice", "instructions": "What does the caller want?", "criteria": {...}}}}
"""

import shutil
import subprocess
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONT = Path(__file__).resolve().parents[2] / "ui" / "static" / "fonts" / "manrope.ttf"

INK, MUTED, LINE, PAPER = "#1d211e", "#5d665f", "#d5dad3", "#ffffff"
GREEN, BLUE, RED, BADGE = "#2f7d4f", "#1f5fb4", "#b3261e", "#e07b1f"

CHECKOUT = {"1": [60, 430, 200, 52], "2": [60, 370, 24, 24], "3": [300, 445, 130, 24], "4": [60, 300, 360, 44]}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
REVENUE = [42, 45, 47, 49, 51, 50, 53, 55, 56, 23, 54, 58]
VOICES = [
    (
        "complaint_pl.wav",
        "Zosia",
        (
            "Dzień dobry, dzwonię w sprawie zamówienia numer 88412. Czajnik przyszedł z "
            "pękniętą obudową. Proszę o wymianę albo zwrot pieniędzy."
        ),
    ),
    (
        "appointment_en.wav",
        "Samantha",
        (
            "Hi, this is Mark Evans. I need to move my appointment from Tuesday to "
            "Thursday afternoon, if that is possible. Please call me back at 555 0142."
        ),
    ),
]
MAX_SECONDS = 15  # well inside the 30 s audio window, and the clips stay small (16 kHz 16-bit mono: 32 kB/s)


def font(size, weight="Regular"):
    f = ImageFont.truetype(str(FONT), size)
    f.set_variation_by_name(weight)
    return f


def badge(d, n, x, y):
    """The set-of-mark badge [n] just above-left of the element box at (x, y)."""
    d.rounded_rectangle([x - 16, y - 18, x + 12, y + 4], radius=5, fill=BADGE)
    d.text((x - 2, y - 7), n, font=font(15, "Bold"), fill=PAPER, anchor="mm")


def checkout():
    im = Image.new("RGB", (800, 600), PAPER)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 800, 64], fill=INK)
    d.text((32, 32), "shop.example · Checkout", font=font(22, "Bold"), fill=PAPER, anchor="lm")
    d.text((60, 96), "Your order", font=font(20, "Bold"), fill=INK)
    rows = [
        ("Kettle KT-200 × 1", "189,00 zł", "Regular"),
        ("Shipping", "15,00 zł", "Regular"),
        ("Total", "204,00 zł", "Bold"),
    ]
    for i, (name, price, weight) in enumerate(rows):
        y = 150 + 40 * i
        d.text((60, y), name, font=font(18, weight), fill=INK, anchor="lm")
        d.text((740, y), price, font=font(18, weight), fill=INK, anchor="rm")
    d.line([60, 250, 740, 250], fill=LINE, width=2)
    x, y, w, h = CHECKOUT["4"]
    d.rounded_rectangle([x, y, x + w, y + h], radius=6, outline=MUTED, width=2, fill=PAPER)
    d.text((x + 14, y + h / 2), "Promo code", font=font(17), fill=MUTED, anchor="lm")
    x, y, w, h = CHECKOUT["2"]
    d.rounded_rectangle([x, y, x + w, y + h], radius=4, outline=INK, width=2, fill=PAPER)
    d.text((x + w + 12, y + h / 2), "I accept the terms and conditions", font=font(17), fill=INK, anchor="lm")
    x, y, w, h = CHECKOUT["1"]
    d.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=GREEN)
    d.text((x + w / 2, y + h / 2), "Pay now", font=font(20, "Bold"), fill=PAPER, anchor="mm")
    x, y, w, h = CHECKOUT["3"]
    f = font(18, "SemiBold")
    d.text((x, y + h / 2), "Back to cart", font=f, fill=BLUE, anchor="lm")
    d.line([x, y + h - 1, x + d.textlength("Back to cart", font=f), y + h - 1], fill=BLUE, width=2)
    for n, (x, y, _, _) in CHECKOUT.items():
        badge(d, n, x, y)
    return im


def invoice():
    im = Image.new("RGB", (700, 900), PAPER)
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 680, 880], outline=LINE, width=2)
    d.text((60, 70), "FAKTURA VAT nr FV/2026/03/117", font=font(26, "Bold"), fill=INK)
    lines = [
        "Data wystawienia: 2026-03-28",
        "Termin płatności: 2026-04-11",
        "",
        "Sprzedawca: Nowak Sp. z o.o., NIP 526-000-11-22",
        "Nabywca: Jan Kowalski",
    ]
    for i, text in enumerate(lines):
        d.text((60, 140 + 34 * i), text, font=font(18), fill=INK)
    cols = [(60, "Towar"), (290, "Ilość"), (370, "Netto"), (480, "VAT"), (560, "Brutto")]
    row = ["Czajnik KT-200", "1", "1 000,00", "23%", "1 230,00"]
    d.rectangle([50, 340, 650, 380], fill="#eef1ec")
    for (x, head), value in zip(cols, row, strict=True):
        d.text((x, 360), head, font=font(16, "Bold"), fill=INK, anchor="lm")
        d.text((x, 410), value, font=font(16), fill=INK, anchor="lm")
    d.line([50, 440, 650, 440], fill=LINE, width=2)
    d.text((60, 480), "Razem do zapłaty: 1 230,00 PLN", font=font(22, "Bold"), fill=INK)
    d.text((60, 530), "Forma płatności: przelew", font=font(18), fill=INK)
    d.text((60, 580), "Status: NIEZAPŁACONA", font=font(22, "ExtraBold"), fill=RED)
    return im


def chart():
    im = Image.new("RGB", (800, 500), PAPER)
    d = ImageDraw.Draw(im)
    d.text((400, 36), "Monthly revenue 2025 (k PLN)", font=font(22, "Bold"), fill=INK, anchor="mm")
    left, right, top, bottom, top_value = 80, 770, 80, 430, 60
    for v in range(0, top_value + 1, 20):
        y = bottom - (bottom - top) * v / top_value
        d.line([left, y, right, y], fill=LINE, width=1)
        d.text((left - 10, y), str(v), font=font(14), fill=MUTED, anchor="rm")
    slot = (right - left) / len(MONTHS)
    for i, (month, v) in enumerate(zip(MONTHS, REVENUE, strict=True)):
        x = left + slot * i + slot * 0.18
        y = bottom - (bottom - top) * v / top_value
        d.rectangle([x, y, x + slot * 0.64, bottom], fill=BLUE)
        d.text((x + slot * 0.32, y - 6), str(v), font=font(14, "Bold"), fill=INK, anchor="mb")
        d.text((x + slot * 0.32, bottom + 10), month, font=font(14), fill=INK, anchor="mt")
    d.line([left, bottom, right, bottom], fill=INK, width=2)
    d.text((24, (top + bottom) / 2), "k PLN", font=font(14), fill=MUTED, anchor="mm")
    d.text(((left + right) / 2, 486), "Month", font=font(14), fill=MUTED, anchor="mm")
    return im


def voices():
    if not shutil.which("say"):
        for name, voice, text in VOICES:
            print(f'say -v {voice} -o {HERE / name} --file-format=WAVE --data-format=LEI16@16000 "{text}"')
        sys.exit("`say` not found (macOS only): run the commands above on a Mac")
    for name, voice, text in VOICES:
        path = HERE / name
        subprocess.run(
            ["say", "-v", voice, "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@16000", text], check=True
        )
        with wave.open(str(path)) as w:
            seconds = w.getnframes() / w.getframerate()
            assert w.getframerate() == 16000 and w.getnchannels() == 1, (name, w.getframerate(), w.getnchannels())
            assert seconds <= MAX_SECONDS, (name, seconds)
        print(f"{name}: {seconds:.1f} s")


def main():
    for name, make in (("checkout.png", checkout), ("invoice.png", invoice), ("chart.png", chart)):
        make().save(HERE / name, optimize=True)
        print(name)
    voices()


if __name__ == "__main__":
    main()
