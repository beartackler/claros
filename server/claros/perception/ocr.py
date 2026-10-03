"""OCR wrapper: RapidOCR (PP-OCRv5 `cyrillic` recognizer by default — its dictionary covers
Latin incl. accents (de/fr/es), Cyrillic (ru) and €/₽) with tile-restricted OCR and
Latin/Cyrillic homoglyph repair.

Env: CLAROS_OCR_REC = cyrillic (default) | eslav | latin | en ; CLAROS_OCR_THREADS (default 4).
Falls back to the legacy `rapidocr_onnxruntime` package (no Cyrillic) if `rapidocr` v3 is absent.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

log = logging.getLogger("claros.perception.ocr")


@dataclass
class OcrLine:
    text: str
    bbox: list[int]          # [x, y, w, h] in frame pixels
    conf: float
    pii: list[str] = field(default_factory=list)  # PII labels found in this line
    pii_boxes: list[list[int]] = field(default_factory=list)
    redacted: bool = False  # True once `text` has had PII spans replaced by [LABEL] placeholders

    def to_dict(self) -> dict:
        return {"text": self.text, "bbox": self.bbox, "conf": round(self.conf, 3)}


# ---------------- homoglyph repair ----------------

_CYR2LAT = str.maketrans("аеорсухіјѕАВЕКМНОРСТХІЈЅ", "aeopcyxijsABEKMHOPCTXIJS")
_LAT2CYR = str.maketrans("aeopcyxABEKMHOPCTX", "аеорсухАВЕКМНОРСТХ")
_CYR_HOMO = set("аеорсухіјѕАВЕКМНОРСТХІЈЅ")
_LAT_HOMO = set("aeopcyxijsABEKMHOPCTXIJS")
_LAT_EXTRA = str.maketrans("иптгкм", "untrkm")  # only applied inside clearly Latin tokens


def _is_cyr(c: str) -> bool:
    return "Ѐ" <= c <= "ӿ"


def fix_homoglyphs(text: str) -> str:
    out = []
    for tok in text.split(" "):
        if not tok:
            out.append(tok)
            continue
        cyr_u = sum(1 for c in tok if _is_cyr(c) and c not in _CYR_HOMO)
        lat_u = sum(1 for c in tok if c.isascii() and c.isalpha() and c not in _LAT_HOMO)
        has_cyr = any(_is_cyr(c) for c in tok)
        has_lat = any(c.isascii() and c.isalpha() for c in tok)
        if not (has_cyr and has_lat):
            out.append(tok)
            continue
        if "@" in tok or "://" in tok or tok.lower().startswith("www.") or lat_u >= max(1, 3 * cyr_u):
            tok = tok.translate(_CYR2LAT)
            if lat_u >= 3 * max(cyr_u, 1):
                tok = tok.translate(_LAT_EXTRA)
        elif cyr_u >= max(1, lat_u):
            tok = tok.translate(_LAT2CYR)
        out.append(tok)
    return " ".join(out)


# ---------------- engine ----------------

class _Engine:
    def __init__(self) -> None:
        self._eng = None
        self._kind = None
        self._lock = threading.Lock()
        self.error: Optional[str] = None

    def _load(self):
        if self._eng is not None or self.error:
            return self._eng
        with self._lock:
            if self._eng is not None or self.error:
                return self._eng
            rec = os.getenv("CLAROS_OCR_REC", "cyrillic").lower()
            threads = int(os.getenv("CLAROS_OCR_THREADS", "4"))
            try:
                from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR  # type: ignore
                lang = {"cyrillic": LangRec.CYRILLIC, "eslav": LangRec.ESLAV,
                        "latin": LangRec.LATIN, "en": LangRec.EN}.get(rec, LangRec.CYRILLIC)
                self._eng = RapidOCR(params={
                    "Global.use_cls": False,
                    "Global.log_level": "warning",
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.lang_type": lang,
                    "Rec.model_type": ModelType.MOBILE,
                    "EngineConfig.onnxruntime.intra_op_num_threads": threads,
                })
                self._kind = "rapidocr3"
            except ImportError:
                try:
                    from rapidocr_onnxruntime import RapidOCR  # type: ignore
                    self._eng = RapidOCR(use_cls=False)
                    self._kind = "rapidocr_onnxruntime"
                    log.warning("rapidocr v3 missing: using rapidocr_onnxruntime (no Cyrillic support)")
                except ImportError as e:
                    self.error = f"no OCR engine: {e}"
                    log.error(self.error)
            except Exception as e:  # noqa: BLE001
                self.error = f"OCR init failed: {e}"
                log.exception("OCR init failed")
            return self._eng

    def warm(self) -> bool:
        return self._load() is not None

    def run(self, img: np.ndarray, offset: tuple[int, int] = (0, 0)) -> list[OcrLine]:
        eng = self._load()
        if eng is None or img.size == 0 or min(img.shape[:2]) < 8:
            return []
        ox, oy = offset
        out: list[OcrLine] = []
        if self._kind == "rapidocr3":
            r = eng(img)
            if r is None or r.boxes is None or r.txts is None:
                return []
            items = zip(r.boxes, r.txts, r.scores)
        else:
            res, _ = eng(img)
            items = ((b, t, s) for b, t, s in (res or []))
        for box, txt, score in items:
            txt = (txt or "").strip()
            if not txt:
                continue
            pts = np.asarray(box, dtype=float)
            x0, y0 = pts[:, 0].min(), pts[:, 1].min()
            x1, y1 = pts[:, 0].max(), pts[:, 1].max()
            out.append(OcrLine(fix_homoglyphs(txt),
                               [int(x0) + ox, int(y0) + oy, int(x1 - x0), int(y1 - y0)], float(score)))
        return out


ENGINE = _Engine()


# ---------------- geometry helpers ----------------

def intersects(a: list[int], b: list[int], pad: int = 0) -> bool:
    return not (a[0] + a[2] + pad <= b[0] or b[0] + b[2] + pad <= a[0]
                or a[1] + a[3] + pad <= b[1] or b[1] + b[3] + pad <= a[1])


def union(a: list[int], b: list[int]) -> list[int]:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return [x0, y0, x1 - x0, y1 - y0]


def merge_rects(rects: list[list[int]], pad: int = 0) -> list[list[int]]:
    rs = [list(map(int, r)) for r in rects if r and r[2] > 0 and r[3] > 0]
    changed = True
    while changed:
        changed = False
        out: list[list[int]] = []
        for r in rs:
            for i, o in enumerate(out):
                if intersects(r, o, pad):
                    out[i] = union(r, o)
                    changed = True
                    break
            else:
                out.append(r)
        rs = out
    return rs


def clamp(r: list[int], w: int, h: int) -> list[int]:
    x0, y0 = max(0, r[0]), max(0, r[1])
    x1, y1 = min(w, r[0] + r[2]), min(h, r[1] + r[3])
    return [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]


def ocr_frame(img: np.ndarray, changed_tiles: Optional[list[list[int]]] = None,
              prev_lines: Optional[list[OcrLine]] = None, full_ratio: float = 0.45
              ) -> tuple[list[OcrLine], list[list[int]] | None]:
    """OCR the whole frame, or only the changed tiles (expanded to whole previous text lines they
    touch). Returns (lines for the full frame, regions OCR'd or None if full frame).
    Lines outside the changed regions are carried over from `prev_lines`."""
    h, w = img.shape[:2]
    if not changed_tiles or prev_lines is None:
        return ENGINE.run(img), None
    regions = merge_rects([clamp([t[0] - 6, t[1] - 6, t[2] + 12, t[3] + 12], w, h) for t in changed_tiles])
    # grow regions to fully contain any previous line they touch (avoid cutting words)
    for _ in range(2):
        grown = []
        for r in regions:
            for ln in prev_lines:
                if intersects(r, ln.bbox):
                    r = union(r, clamp([ln.bbox[0] - 4, ln.bbox[1] - 4, ln.bbox[2] + 8, ln.bbox[3] + 8], w, h))
            grown.append(r)
        regions = merge_rects(grown)
    area = sum(r[2] * r[3] for r in regions)
    if area >= full_ratio * w * h:
        return ENGINE.run(img), None
    lines = [ln for ln in prev_lines if not any(intersects(ln.bbox, r) for r in regions)]
    for r in regions:
        crop = img[r[1]:r[1] + r[3], r[0]:r[0] + r[2]]
        lines.extend(ENGINE.run(crop, (r[0], r[1])))
    lines.sort(key=lambda ln: (ln.bbox[1] // 8, ln.bbox[0]))
    return lines, regions
