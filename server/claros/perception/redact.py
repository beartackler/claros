"""Keyframe → OCR → PII → blurred JPEG. The raw frame is only ever held in memory.

    res = redact_frame(jpeg_bytes, changed_tiles=..., prev_lines=...)
    res.lines        # list[OcrLine] for the whole frame (raw text, for downstream state extraction)
    res.jpeg         # redacted JPEG bytes (safe to persist / send to vision LLM)
    save_redacted(session_id, seq, res, t)  -> keyframe_id   (data/keyframes/{session}/{seq}.jpg)
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import ocr as ocr_mod
from . import pii as pii_mod
from .ocr import OcrLine

log = logging.getLogger("claros.perception.redact")

try:
    from claros import REPO_ROOT
except Exception:  # noqa: BLE001
    REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class RedactResult:
    lines: list[OcrLine]
    jpeg: bytes
    dims: tuple[int, int]
    pii_boxes: list[list[int]] = field(default_factory=list)
    pii_labels: list[str] = field(default_factory=list)
    regions: Optional[list[list[int]]] = None  # OCR'd regions (None = full frame)
    ocr_ms: float = 0.0
    pii_ms: float = 0.0
    blur_ms: float = 0.0

    @property
    def mean_conf(self) -> float:
        return float(np.mean([ln.conf for ln in self.lines])) if self.lines else 0.0


def span_box(line: OcrLine, a: int, b: int, w: int, h: int) -> list[int]:
    x, y, lw, lh = line.bbox
    n = max(1, len(line.text))
    cw = lw / n
    x0 = int(x + cw * a - cw * 0.6 - 3)
    x1 = int(x + cw * b + cw * 0.6 + 3)
    return ocr_mod.clamp([x0, y - 3, x1 - x0, lh + 6], w, h)


def mask_text(text: str, spans: list) -> str:
    """Replace PII spans with [LABEL] placeholders (text that leaves this module is PII-free)."""
    for a, b, lab in sorted(spans, reverse=True):
        text = text[:a] + f"[{lab.upper()}]" + text[b:]
    return text


def decode(jpeg: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(jpeg))
    return im.convert("RGB")


def redact_frame(jpeg: bytes, changed_tiles: Optional[list[list[int]]] = None,
                 prev_lines: Optional[list[OcrLine]] = None, quality: int = 80) -> RedactResult:
    im = decode(jpeg)
    w, h = im.size
    arr = np.asarray(im)
    t0 = time.perf_counter()
    lines, regions = ocr_mod.ocr_frame(arr, changed_tiles, prev_lines)
    t1 = time.perf_counter()
    fresh = [ln for ln in lines if not ln.redacted]
    spans = pii_mod.detect([ln.text for ln in fresh])
    t2 = time.perf_counter()
    for ln, sp in zip(fresh, spans):
        ln.pii = [lab for _, _, lab in sp]
        ln.pii_boxes = [span_box(ln, a, b, w, h) for a, b, _ in sp]
        ln.text = mask_text(ln.text, sp)
        ln.redacted = True
    boxes = [b for ln in lines for b in ln.pii_boxes]
    labels = [lab for ln in lines for lab in ln.pii]
    out = blur_boxes(im, boxes)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=quality)
    t3 = time.perf_counter()
    return RedactResult(lines=lines, jpeg=buf.getvalue(), dims=(w, h), pii_boxes=boxes, pii_labels=labels,
                        regions=regions, ocr_ms=(t1 - t0) * 1000, pii_ms=(t2 - t1) * 1000,
                        blur_ms=(t3 - t2) * 1000)


def blur_boxes(im: Image.Image, boxes: list[list[int]]) -> Image.Image:
    if not boxes:
        return im
    out = im.copy()
    for x, y, bw, bh in boxes:
        if bw <= 0 or bh <= 0:
            continue
        region = out.crop((x, y, x + bw, y + bh))
        # heavy blur + pixelation so text is unrecoverable, not just soft
        small = region.resize((max(1, bw // 12), max(1, bh // 12)), Image.BILINEAR)
        region = small.resize((bw, bh), Image.NEAREST).filter(ImageFilter.GaussianBlur(radius=max(4, bh // 3)))
        out.paste(region, (x, y))
        ImageDraw.Draw(out).rectangle((x, y, x + bw - 1, y + bh - 1), outline=(180, 180, 180))
    return out


def keyframe_path(session_id: str, seq: int) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    return REPO_ROOT / "data" / "keyframes" / safe / f"{int(seq)}.jpg"


def keyframe_id(session_id: str, seq: int) -> str:
    return f"{session_id}_{int(seq)}"


def save_redacted(session_id: str, seq: int, res: RedactResult, t: Optional[float] = None,
                  reason: Optional[str] = None) -> str:
    """Persist the REDACTED jpeg + keyframe row. Returns keyframe_id."""
    kid = keyframe_id(session_id, seq)
    p = keyframe_path(session_id, seq)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(res.jpeg)
    try:
        from claros.store import store
        store.put_keyframe(kid, session_id, str(p), t, {
            "seq": seq, "dims": list(res.dims), "reason": reason, "pii_count": len(res.pii_boxes),
            "pii_labels": sorted(set(res.pii_labels)), "redacted": True})
    except Exception as e:  # noqa: BLE001
        log.warning("store.put_keyframe failed: %s", e)
    return kid
