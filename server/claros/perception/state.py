"""ScreenState extraction: OCR heuristics (cheap path, every frame) + vision-LLM templates
(structural changes only). App-agnostic: no app-specific strings or code paths.

Per-session `StateTracker`:
    structural, why = tracker.route(lines, dims, reason)
    state = tracker.update(seq, t, lines, dims, keyframe_id)          # cheap path, always
    state = tracker.apply_vision(seq, vision_state)                    # when a vision result lands
Vision templates are keyed by entity (or view) so a stale result never overwrites a newer screen;
older-seq vision results than the last applied one are discarded.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

from claros.models import Field_, ScreenState

from . import lexicon
from .locale import guess_lang, normalize
from .ocr import OcrLine, intersects

log = logging.getLogger("claros.perception.state")


# ---------------- vision schema ----------------

class VField(BaseModel):
    label: str
    value: Optional[str] = None
    bbox: Optional[list[int]] = Field(default=None, description="[x,y,w,h] of the VALUE in image pixels")
    kind: Optional[str] = Field(default=None, description="text|number|date|currency|select|link|checkbox|textarea")


class VTable(BaseModel):
    name: Optional[str] = None
    columns: list[str] = Field(default_factory=list)
    row_count: Optional[int] = None
    rows: list[list[str]] = Field(default_factory=list, description="first rows, max 5")


class VisionState(BaseModel):
    app: Optional[str] = None
    view: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    status: Optional[str] = None
    fields: list[VField] = Field(default_factory=list)
    tables: list[VTable] = Field(default_factory=list)
    dialogs: list[str] = Field(default_factory=list)
    toasts: list[str] = Field(default_factory=list)
    ui_lang: Optional[str] = None
    confidence: float = 0.7


VISION_SYSTEM = (
    "You read screenshots of business software (any app, any language) and return the FULL current "
    "screen state as JSON. Keep labels and values EXACTLY as shown on screen (do not translate). "
    "Use the OCR lines (with [x,y,w,h] pixel boxes) as grounding for exact text and for field bboxes; the "
    "image is the source of truth for structure (which text is a label, a value, a button, a dialog). "
    "Blurred regions are redacted personal data: report their value as \"[REDACTED]\". "
    "entity_type = kind of record (e.g. 'Purchase Invoice', 'Ticket'); entity_id = its identifier if shown. "
    "status = the record's workflow status badge if any. dialogs = text of any modal/confirm dialog. "
    "toasts = transient notifications. ui_lang = ISO 639-1 of the UI. Include every visible form field "
    "(label → current value, empty string if empty) with bbox of the value box; kind if obvious. "
    "Summarize tables (columns, row_count, first rows). confidence 0..1."
)


def vision_messages(lines: list[OcrLine], prev_summary: str, dims: tuple[int, int]) -> list[dict]:
    ocr = "\n".join(f"[{','.join(map(str, ln.bbox))}] {ln.text}" for ln in lines[:250])
    user = (f"Image size: {dims[0]}x{dims[1]}.\nPrevious screen state: {prev_summary or 'none'}\n\n"
            f"OCR lines:\n{ocr}\n\nReturn the full current ScreenState JSON.")
    return [{"role": "system", "content": VISION_SYSTEM}, {"role": "user", "content": user}]


# ---------------- helpers ----------------

ENTITY_ID = re.compile(
    r"(?<![\w-])(?:[A-ZА-ЯЁ]{2,}[A-ZА-ЯЁ0-9]*(?:[-/.][A-ZА-ЯЁ0-9]+)*[-/.]\d{2,}[A-Z0-9-]*|"
    r"#\s?\d{3,}|№\s?[\w\-/]*\d+|\b\d{5,}\b)")


def label_key(label: str) -> str:
    t = re.sub(r"[\s*:：]+$", "", label.strip().lower())
    t = re.sub(r"^[\s*]+", "", t)
    return re.sub(r"\s+", " ", t)


def char_px(ln: OcrLine) -> float:
    return ln.bbox[2] / max(1, len(ln.text))


def _center(b: list[int]) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def _valueish(text: str) -> bool:
    return bool(re.search(r"\d", text)) and normalize(text) is not None


@dataclass
class Pair:
    label: str
    value: Optional[str]
    label_bbox: list[int]
    value_bbox: Optional[list[int]]


@dataclass
class Heuristic:
    title: Optional[str] = None
    title_bbox: Optional[list[int]] = None
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    status: Optional[str] = None       # text as shown
    status_key: Optional[str] = None
    toasts: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)
    ui_lang: Optional[str] = None


def analyze(lines: list[OcrLine], dims: tuple[int, int]) -> Heuristic:
    """App-agnostic OCR layout heuristics."""
    W, H = dims
    h = Heuristic(ui_lang=guess_lang([ln.text for ln in lines]))
    if not lines:
        return h
    top = [ln for ln in lines if ln.bbox[1] < H * 0.35 and len(ln.text) >= 3]
    used: set[int] = set()
    # title: largest per-character width in the upper third
    if top:
        cands = [ln for ln in top if not lexicon.status_of(ln.text)]
        if cands:
            med = sorted(char_px(ln) for ln in lines)[len(lines) // 2]

            def score(ln: OcrLine) -> float:
                sc = char_px(ln)
                if ENTITY_ID.search(ln.text):
                    sc *= 1.3
                if len(ln.text) < 6 or lexicon.is_button(ln.text, "ok") or lexicon.is_button(ln.text, "cancel") \
                        or lexicon.is_action(ln.text):
                    sc *= 0.6
                return sc
            t = max(cands, key=score)
            if char_px(t) >= med * 1.25 and score(t) >= char_px(t) * 0.99:
                h.title, h.title_bbox = t.text, t.bbox
                used.add(id(t))
    search = ([h.title] if h.title else []) + [ln.text for ln in top]
    for s in search:
        m = ENTITY_ID.search(s or "")
        if m:
            h.entity_id = m.group(0).strip()
            break
    if h.title:
        et = h.title.replace(h.entity_id, "") if h.entity_id and h.entity_id in h.title else h.title
        et = re.sub(r"\s{2,}", " ", et).strip(" :-–—#№")
        h.entity_type = et or None
    for ln in lines:
        k = lexicon.status_of(ln.text)
        if k and ln.bbox[1] < H * 0.5 and k != "not_saved":
            h.status, h.status_key = ln.text.strip(), k
            used.add(id(ln))
            break
    for ln in lines:
        if len(ln.text) <= 60 and (lexicon.TOAST_SAVE.search(ln.text) or lexicon.TOAST_ERROR.search(ln.text)
                                   or (lexicon.TOAST_SUBMIT.search(ln.text) and not lexicon.status_of(ln.text))):
            if lexicon.status_of(ln.text) is None:
                h.toasts.append(ln.text.strip())
                used.add(id(ln))
    # dialog: an OK-ish and a Cancel-ish button on the same row, in the central area
    oks = [ln for ln in lines if lexicon.is_button(ln.text, "ok")]
    cancels = [ln for ln in lines if lexicon.is_button(ln.text, "cancel")]
    for o in oks:
        for c in cancels:
            if abs(_center(o.bbox)[1] - _center(c.bbox)[1]) < max(o.bbox[3], 12) and \
                    W * 0.15 < _center(o.bbox)[0] < W * 0.9 and H * 0.15 < _center(o.bbox)[1] < H * 0.9:
                above = [ln for ln in lines if ln.bbox[1] + ln.bbox[3] <= o.bbox[1] and
                         o.bbox[1] - ln.bbox[1] < H * 0.4 and len(ln.text) > 8 and ln is not o and
                         abs(_center(ln.bbox)[0] - (o.bbox[0] + c.bbox[0]) / 2) < W * 0.35]
                if above:
                    q = min(above, key=lambda ln: o.bbox[1] - ln.bbox[1])
                    h.dialogs.append(q.text.strip())
                    used.update({id(o), id(c), id(q)})
                break
        if h.dialogs:
            break
    h.pairs = pair_fields([ln for ln in lines if id(ln) not in used and
                           (h.title_bbox is None or ln.bbox[1] > h.title_bbox[1])])
    return h


def pair_fields(lines: list[OcrLine]) -> list[Pair]:
    """label:value inline, label-above-value and label-left-of-value pairing."""
    pairs: list[Pair] = []
    used: set[int] = set()
    lines = sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    for ln in lines:
        m = re.match(r"^([^\d:：]{2,40})[:：]\s+(.+)$", ln.text)
        if m:
            pairs.append(Pair(m.group(1).strip(), m.group(2).strip(), ln.bbox, ln.bbox))
            used.add(id(ln))
    for i, lab in enumerate(lines):
        if id(lab) in used or _valueish(lab.text) or len(lab.text) > 48 or "[" in lab.text[:1]:
            continue
        lh = lab.bbox[3]
        best = None
        # right of label on the same row (label ends with ':' or is followed closely)
        for v in lines:
            if v is lab or id(v) in used:
                continue
            same_row = abs(_center(v.bbox)[1] - _center(lab.bbox)[1]) < lh * 0.5
            gap = v.bbox[0] - (lab.bbox[0] + lab.bbox[2])
            if same_row and 0 <= gap < max(60, lh * 3) and lab.text.rstrip().endswith((":", "：")):
                best = v
                break
        if best is None:
            for v in lines:
                if v is lab or id(v) in used:
                    continue
                dy = v.bbox[1] - (lab.bbox[1] + lab.bbox[3])
                if -2 <= dy < lh * 1.2 and abs(v.bbox[0] - lab.bbox[0]) < max(40, lh * 2):
                    best = v
                    break
        if best is not None:
            # the value must not itself be a label of the line right below it with a tighter gap
            pairs.append(Pair(lab.text.rstrip(" :："), best.text, lab.bbox, best.bbox))
            used.update({id(lab), id(best)})
    return pairs


# ---------------- tracker ----------------

@dataclass
class Template:
    seq: int
    vs: VisionState
    label_boxes: dict[str, list[int]] = field(default_factory=dict)  # key -> label bbox (from OCR)
    kinds: dict[str, str] = field(default_factory=dict)


def _ekey(entity_type: Optional[str], entity_id: Optional[str], view: Optional[str]) -> str:
    if entity_id:
        return f"id:{entity_id}"
    return f"view:{(entity_type or view or '').lower()}"


class StateTracker:
    def __init__(self, lang_hint: Optional[str] = None) -> None:
        self.lang_hint = lang_hint
        self.prev_lines: Optional[list[OcrLine]] = None
        self.prev_heur: Optional[Heuristic] = None
        self.state: Optional[ScreenState] = None
        self.templates: dict[str, Template] = {}
        self.last_vision_seq = -1
        self.entity_models: dict[str, dict[str, Field_]] = {}
        self.kinds: dict[str, dict[str, str]] = {}
        self.dims: tuple[int, int] = (0, 0)
        self.last_lines: list[OcrLine] = []
        self.last_seq = -1
        self.last_t = 0.0
        self.last_keyframe_id: Optional[str] = None

    # ----- router -----
    def route(self, lines: list[OcrLine], dims: tuple[int, int], reason: Optional[str] = None
              ) -> tuple[bool, str]:
        heur = analyze(lines, dims)
        if self.state is None or self.prev_lines is None:
            return True, "first"
        if reason == "boundary":
            return True, "boundary"
        if lines and sum(ln.conf for ln in lines) / len(lines) < 0.7:
            return True, "low_conf"
        ph = self.prev_heur
        if ph and (ph.title or "") != (heur.title or ""):
            return True, "title"
        if ph and (ph.entity_id or "") != (heur.entity_id or ""):
            return True, "entity"
        if heur.dialogs and (not ph or heur.dialogs != ph.dialogs):
            return True, "dialog"
        a = [ln.text for ln in self.prev_lines]
        b = [ln.text for ln in lines]
        if a or b:
            sa, sb = set(a), set(b)
            changed = len(sa ^ sb) / max(1, len(sa | sb))
            if changed > 0.30:
                return True, f"lines_changed:{changed:.2f}"
        k = self._current_key(heur)
        if k not in self.templates:
            return True, "no_template"
        return False, "cheap"

    def _current_key(self, heur: Heuristic) -> str:
        if heur.entity_id:
            return _ekey(None, heur.entity_id, None)
        # find a template whose title/entity_type matches
        for k, tpl in self.templates.items():
            if heur.title and tpl.vs.entity_type and tpl.vs.entity_type.lower() in heur.title.lower() \
                    and not tpl.vs.entity_id:
                return k
        return _ekey(heur.entity_type, None, heur.title)

    # ----- cheap path -----
    def update(self, seq: int, t: float, lines: list[OcrLine], dims: tuple[int, int],
               keyframe_id: Optional[str] = None) -> ScreenState:
        heur = analyze(lines, dims)
        st = self._compose(seq, t, lines, dims, heur, keyframe_id)
        self.prev_lines, self.prev_heur, self.state = lines, heur, st
        self.dims, self.last_lines, self.last_seq, self.last_t, self.last_keyframe_id = \
            dims, lines, seq, t, keyframe_id
        return st

    def _compose(self, seq: int, t: float, lines: list[OcrLine], dims: tuple[int, int], heur: Heuristic,
                 keyframe_id: Optional[str]) -> ScreenState:
        key = self._current_key(heur)
        tpl = self.templates.get(key)
        lang = heur.ui_lang or self.lang_hint or "en"
        vs = tpl.vs if tpl else None
        ui_lang = (vs.ui_lang if vs and vs.ui_lang else None) or lang
        texts = [ln.text for ln in lines]
        visible: dict[str, Field_] = {}
        kinds = self.kinds.setdefault(key, {})
        # 1) heuristic pairs from OCR (current values)
        for p in heur.pairs:
            k = label_key(p.label)
            visible[k] = Field_(label=p.label, value=p.value, bbox=p.value_bbox,
                                normalized=normalize(p.value, ui_lang))
        # 2) template fields (vision labels); value from OCR at the template's value bbox, shifted by
        #    the label's displacement (scroll), else from heuristic pair, else keep vision value if the
        #    template is for this very frame.
        if tpl:
            for vf in vs.fields:
                k = label_key(vf.label)
                if vf.kind:
                    kinds[k] = vf.kind
                if k in visible:
                    continue
                lab_now = _find_label(lines, vf.label)
                lb = tpl.label_boxes.get(k)
                if vf.bbox and lab_now is not None and lb is not None:
                    dx, dy = lab_now.bbox[0] - lb[0], lab_now.bbox[1] - lb[1]
                    vb = [vf.bbox[0] + dx, vf.bbox[1] + dy, vf.bbox[2], vf.bbox[3]]
                    vals = [ln for ln in lines if ln is not lab_now and intersects(ln.bbox, vb)]
                    val = " ".join(ln.text for ln in sorted(vals, key=lambda ln: ln.bbox[0])) if vals else ""
                    visible[k] = Field_(label=vf.label, value=val or None, bbox=vb,
                                        normalized=normalize(val, ui_lang))
                elif tpl.seq == seq:
                    visible[k] = Field_(label=vf.label, value=vf.value, bbox=vf.bbox,
                                        normalized=normalize(vf.value, ui_lang))
        # accumulate per entity (fields seen so far, even if scrolled off)
        model = self.entity_models.setdefault(key, {})
        for k, f in visible.items():
            model[k] = f
        fields = list(visible.values()) + [
            Field_(label=f.label, value=f.value, canonical=f.canonical, normalized=f.normalized, bbox=None)
            for k, f in model.items() if k not in visible]
        status = heur.status
        if status is None and vs and vs.status and any(vs.status.lower() in s.lower() for s in texts):
            status = vs.status
        dialogs = list(heur.dialogs)
        toasts = list(heur.toasts)
        if vs and tpl.seq == seq:
            dialogs += [d for d in vs.dialogs if d not in dialogs]
            toasts += [x for x in vs.toasts if x not in toasts]
        elif vs:
            dialogs += [d for d in vs.dialogs if d not in dialogs and _text_present(d, texts)]
        mean_conf = sum(ln.conf for ln in lines) / len(lines) if lines else 0.0
        conf = (0.6 + 0.35 * (vs.confidence if vs else 0)) * mean_conf if vs else 0.55 * mean_conf
        return ScreenState(
            seq=seq, t=t,
            app=vs.app if vs else None,
            view=(vs.view if vs else None) or heur.title,
            entity_type=(vs.entity_type if vs else None) or heur.entity_type,
            entity_id=heur.entity_id or (vs.entity_id if vs else None),
            status=status, fields=fields,
            tables=[tb.model_dump() for tb in vs.tables] if vs else [],
            dialogs=dialogs, toasts=toasts, ui_lang=ui_lang, confidence=round(conf, 3),
            keyframe_id=keyframe_id)

    # ----- vision path -----
    def apply_vision(self, seq: int, vs: VisionState, lines_at_seq: list[OcrLine]) -> Optional[ScreenState]:
        """Install a vision template from frame `seq`; recompute the CURRENT state (latest frame)."""
        if seq <= self.last_vision_seq:
            log.info("dropping stale vision result seq=%s (applied %s)", seq, self.last_vision_seq)
            return None
        self.last_vision_seq = seq
        if vs.entity_id:
            vs.entity_id = vs.entity_id.strip()
        key = _ekey(vs.entity_type, vs.entity_id, vs.view)
        tpl = Template(seq=seq, vs=vs)
        for vf in vs.fields:
            lab = _find_label(lines_at_seq, vf.label)
            if lab is not None:
                tpl.label_boxes[label_key(vf.label)] = lab.bbox
        self.templates[key] = tpl
        if not vs.entity_id:
            # also reachable via the heuristic key of that frame
            hk = self._current_key(analyze(lines_at_seq, self.dims))
            self.templates.setdefault(hk, tpl)
        if self.state is None:
            return None
        st = self._compose(self.last_seq, self.last_t, self.last_lines, self.dims,
                           self.prev_heur or analyze(self.last_lines, self.dims), self.last_keyframe_id)
        self.state = st
        return st

    def summary(self) -> str:
        return summarize(self.state)


def _find_label(lines: list[OcrLine], label: str) -> Optional[OcrLine]:
    k = label_key(label)
    if not k:
        return None
    for ln in lines:
        if label_key(ln.text) == k:
            return ln
    for ln in lines:
        lk = label_key(ln.text)
        if lk.startswith(k + ":") or lk.startswith(k + " :"):
            return ln
    return None


def _text_present(s: str, texts: list[str]) -> bool:
    s = s.strip().lower()[:30]
    return any(s and s in t.lower() for t in texts)


def summarize(st: Optional[ScreenState], max_fields: int = 8) -> str:
    if st is None:
        return ""
    head = " ".join(x for x in [st.app and f"[{st.app}]", st.entity_type or st.view, st.entity_id,
                                st.status and f"({st.status})"] if x)
    vis = [f for f in st.fields if f.bbox is not None and f.value][:max_fields]
    fl = "; ".join(f"{f.label}={f.value}" for f in vis)
    extra = []
    if st.dialogs:
        extra.append("dialog: " + " | ".join(st.dialogs))
    if st.toasts:
        extra.append("toast: " + " | ".join(st.toasts))
    return " — ".join(x for x in [head, fl, "; ".join(extra)] if x)


def state_json_schema() -> dict:
    return VisionState.model_json_schema()


def dumps_state(st: ScreenState) -> str:
    return json.dumps(st.model_dump(mode="json"), ensure_ascii=False)


__all__ = ["StateTracker", "VisionState", "vision_messages", "analyze", "summarize", "label_key"]
