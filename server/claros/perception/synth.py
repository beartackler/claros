"""Synthetic fake-form screenshots for UNIT TESTS / replay smoke only (never for evals of real apps).

    python -m claros.perception.synth data/fixtures/frames   # writes en/, de/, ru/ sequences
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int, bold: bool = False):
    for p in (BOLD_CANDIDATES if bold else []) + FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


L10N = {
    "en": {"app": "Acme ERP", "crumb": "Accounting / Purchase Invoice", "doc": "Purchase Invoice",
           "draft": "Draft", "submitted": "Submitted", "saved": "Saved", "save": "Save",
           "fields": ["Supplier", "Posting Date", "Cost Center", "Grand Total", "Contact", "Contact Email"],
           "dialog": "Submit this document permanently?", "ok": "Yes", "cancel": "Cancel"},
    "de": {"app": "Acme ERP", "crumb": "Buchhaltung / Eingangsrechnung", "doc": "Eingangsrechnung",
           "draft": "Entwurf", "submitted": "Gebucht", "saved": "Gespeichert", "save": "Speichern",
           "fields": ["Lieferant", "Buchungsdatum", "Kostenstelle", "Gesamtbetrag", "Kontakt", "Kontakt E-Mail"],
           "dialog": "Dieses Dokument endgültig buchen?", "ok": "Ja", "cancel": "Abbrechen"},
    "ru": {"app": "Acme ERP", "crumb": "Бухгалтерия / Счёт поставщика", "doc": "Счёт поставщика",
           "draft": "Черновик", "submitted": "Проведён", "saved": "Сохранено", "save": "Сохранить",
           "fields": ["Поставщик", "Дата проводки", "Место возникновения затрат", "Итого", "Контакт",
                      "Эл. почта"],
           "dialog": "Провести документ окончательно?", "ok": "Да", "cancel": "Отмена"},
}

VALUES = {
    "en": ["Global Office Supply Ltd", "10/03/2026", "Administration", "$1,234.56", "Mr. John Smith",
           "john.smith@acme-supply.com"],
    "de": ["Büromöbel Schmidt GmbH", "03.10.2026", "Verwaltung", "12.400,00 €", "Herr Hans Müller",
           "hans.mueller@schmidt-moebel.de"],
    "ru": ["ООО Канцтовары Плюс", "03.10.2026", "Администрация", "12 400,00 ₽", "Иван Петрович Сидоров",
           "sidorov@kanctovary.ru"],
}
PRODUCTION = {"en": "Production", "de": "Produktion", "ru": "Производство"}


def render_form(lang: str, values: list[str], status: str = "draft", toast: Optional[str] = None,
                dialog: bool = False, entity_id: str = "ACC-PINV-0004", size=(1280, 800)) -> Image.Image:
    t = L10N[lang]
    W, H = size
    im = Image.new("RGB", size, (247, 248, 250))
    d = ImageDraw.Draw(im)
    f_small, f_med, f_big = _font(14), _font(16), _font(26, bold=True)
    d.rectangle((0, 0, W, 48), fill=(30, 34, 40))
    d.text((24, 14), t["app"], font=_font(18, bold=True), fill="white")
    d.text((W - 260, 16), "Help", font=f_small, fill=(200, 200, 200))
    d.text((32, 66), t["crumb"], font=f_small, fill=(110, 110, 120))
    d.text((32, 92), f"{t['doc']} {entity_id}", font=f_big, fill=(20, 20, 20))
    st = t["submitted"] if status == "submitted" else t["draft"]
    tw = d.textlength(st, font=f_small)
    px = W - 360
    d.rounded_rectangle((px, 98, px + tw + 24, 124), radius=12,
                        fill=(255, 236, 200) if status != "submitted" else (210, 240, 215))
    d.text((px + 12, 103), st, font=f_small, fill=(90, 60, 10))
    d.rounded_rectangle((W - 160, 94, W - 32, 128), radius=6, fill=(40, 40, 46))
    d.text((W - 140, 102), t["save"], font=f_med, fill="white")
    d.rectangle((24, 150, W - 24, H - 40), fill="white", outline=(225, 225, 230))
    for i, (label, val) in enumerate(zip(t["fields"], values)):
        col, row = i % 2, i // 2
        x = 56 + col * 600
        y = 180 + row * 110
        d.text((x, y), label, font=f_small, fill=(100, 100, 110))
        d.rectangle((x, y + 24, x + 520, y + 64), fill=(244, 245, 247), outline=(220, 220, 228))
        d.text((x + 12, y + 34), val, font=f_med, fill=(20, 20, 20))
    if toast:
        tw = d.textlength(toast, font=f_med)
        d.rounded_rectangle((W - tw - 90, H - 100, W - 40, H - 56), radius=8, fill=(34, 139, 84))
        d.text((W - tw - 66, H - 88), toast, font=f_med, fill="white")
    if dialog:
        ov = Image.new("RGBA", size, (0, 0, 0, 90))
        im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((W // 2 - 260, H // 2 - 90, W // 2 + 260, H // 2 + 90), radius=10, fill="white")
        d.text((W // 2 - 230, H // 2 - 60), t["dialog"], font=f_med, fill=(20, 20, 20))
        d.text((W // 2 + 60, H // 2 + 40), t["cancel"], font=f_med, fill=(60, 60, 60))
        d.text((W // 2 + 180, H // 2 + 40), t["ok"], font=f_med, fill=(20, 90, 200))
    return im


def sequence(lang: str) -> list[tuple[str, Image.Image]]:
    v = list(VALUES[lang])
    t = L10N[lang]
    edited = list(v)
    edited[2] = PRODUCTION[lang]
    return [
        ("001_open", render_form(lang, v)),
        ("002_edit_cost_center", render_form(lang, edited)),
        ("003_saved", render_form(lang, edited, toast=t["saved"])),
        ("004_confirm_dialog", render_form(lang, edited, dialog=True)),
        ("005_submitted", render_form(lang, edited, status="submitted")),
    ]


def to_jpeg(im: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def main(out: str) -> None:
    root = Path(out)
    for lang in L10N:
        (root / lang).mkdir(parents=True, exist_ok=True)
        for name, im in sequence(lang):
            (root / lang / f"{name}.jpg").write_bytes(to_jpeg(im))
    print(f"wrote fixtures to {root}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/fixtures/frames")
