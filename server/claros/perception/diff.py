"""Deterministic ScreenState → ScreenEvent diff (app-agnostic).

Kinds: open (entity changed) · navigate (view changed, no entity) · edit / select (field value changed)
· undo (A→B→A within 20 s) · save (save toast, or "not saved" indicator cleared) · submit/approve/reject/hold
(status transition or toast) · dialog (new modal).
Value source: typed (typing activity overlapping the field shortly before) · pasted (changed without
typing but with user activity) · system (no user activity, or changed alongside a typed field = autofill).
"""
from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from claros.models import ScreenEvent, ScreenState

from . import lexicon
from .ocr import intersects
from .state import label_key

UNDO_WINDOW_MS = 20_000
ACTIVITY_WINDOW_MS = 4_000
USER_KINDS = {"typing", "scrolling", "navigating"}


@dataclass
class Activity:
    t: float
    kind: str
    tiles: list[list[int]] = field(default_factory=list)


def _tiles(v: Any) -> list[list[int]]:
    if isinstance(v, list):
        return [list(map(int, x)) for x in v if isinstance(x, (list, tuple)) and len(x) == 4]
    return []


_TRUNC = ("...", "…", "..")


def _clean(v: str) -> str:
    return v.strip().rstrip("×xX✕").strip()


def _num_of(n: Any) -> Optional[float]:
    if isinstance(n, (int, float)) and not isinstance(n, bool):
        return float(n)
    if isinstance(n, dict) and isinstance(n.get("value"), (int, float)):
        return float(n["value"])
    return None


def _lev(a: str, b: str, cap: int = 3) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_CONFUSE = str.maketrans({"0": "o", "1": "l", "|": "l", "5": "s", "8": "b"})


def _norm(v: str) -> str:
    """Comparable form: no trailing arrows/ellipses/dots, lowercase, OCR look-alikes folded."""
    v = v.strip().rstrip("→›>…. ").strip()
    return " ".join(v.lower().translate(_CONFUSE).split())


def _same_text(a: str, b: str) -> bool:
    """Equal modulo OCR noise / UI truncation: 'Administration - O...' ≈ 'Administration - Ol',
    'Plants and Machineries - OPF' ≈ '… - OPP' (one glyph on a long string)."""
    a, b = _clean(a), _clean(b)
    if a == b:
        return True
    ta, tb = a.endswith(_TRUNC), b.endswith(_TRUNC)
    a2, b2 = a.rstrip(".…").rstrip(), b.rstrip(".…").rstrip()
    if (ta or tb) and min(len(a2), len(b2)) >= 4 and (a2.startswith(b2) or b2.startswith(a2)):
        return True  # "EQ-GENERAL: Wo..." vs "EQ-GENERAL", "Administration - O..." vs "Administration - Ol"
    if min(len(a2), len(b2)) >= 12 and not any(ch.isdigit() for ch in a2 + b2):
        if _lev(a2.lower(), b2.lower()) <= 1:
            return True
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    # a cut-off cell read as "Plants and Machineries - O" vs "… - OPF": same value, narrower column
    if len(short) >= 8 and long_.startswith(short) and len(short) >= 0.7 * len(long_):
        return True
    return len(short) >= 12 and _lev(na, nb) <= 2 and not any(ch.isdigit() for ch in na + nb)


def same_value(p: Any, f: Any) -> bool:
    """Two observations of one field that differ only by formatting/OCR (no user edit)."""
    np_, nf = _num_of(getattr(p, "normalized", None)), _num_of(getattr(f, "normalized", None))
    if np_ is not None and nf is not None:
        return abs(np_ - nf) < 1e-9
    return _same_text(p.value or "", f.value or "")


class Differ:
    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self.activity: deque[Activity] = deque(maxlen=200)
        self.history: dict[tuple[str, str], list[tuple[float, Optional[str]]]] = {}
        self.last_change_t: Optional[float] = None
        self.last_t: float = float("-inf")

    def note_activity(self, msg: dict) -> None:
        try:
            self.activity.append(Activity(float(msg.get("t") or 0), str(msg.get("kind") or ""),
                                          _tiles(msg.get("tiles_changed"))))
        except (TypeError, ValueError):
            pass

    def _window(self, t: float) -> list[Activity]:
        lo = max(t - ACTIVITY_WINDOW_MS, self.last_t - 500 if self.last_t > float("-inf") else t - ACTIVITY_WINDOW_MS)
        return [a for a in self.activity if lo <= a.t <= t + 250]

    def _ev(self, st: ScreenState, kind: str, summary: str, **kw: Any) -> ScreenEvent:
        return ScreenEvent(id=f"ev_{uuid.uuid4().hex[:10]}", seq=st.seq, t=st.t, kind=kind, app=st.app,
                           entity_type=st.entity_type, entity_id=st.entity_id, keyframe_id=st.keyframe_id,
                           confidence=st.confidence, summary=summary, **kw)

    def diff(self, prev: Optional[ScreenState], cur: ScreenState,
             changed_tiles: Optional[list[list[int]]] = None,
             kinds: Optional[dict[str, str]] = None) -> list[ScreenEvent]:
        evs: list[ScreenEvent] = []
        kinds = kinds or {}
        ent = cur.entity_type or cur.view or "screen"
        name = f"{ent} {cur.entity_id}".strip() if cur.entity_id else ent
        if prev is None or (cur.entity_id and cur.entity_id != prev.entity_id):
            if cur.entity_id or cur.view:
                evs.append(self._ev(cur, "open", f"Opened {name}"))
            self._seed(cur)
            self._tail(prev, cur, evs)
            self.last_t = cur.t
            return evs
        if (cur.view or "") != (prev.view or "") and not cur.entity_id and cur.view:
            evs.append(self._ev(cur, "navigate", f"Navigated to {cur.view}"))
            self._seed(cur)
            self._tail(prev, cur, evs)
            self.last_t = cur.t
            return evs

        # ----- record marks (tag/chip "On Hold") -----
        pm = {m for f in prev.fields if f.canonical == "ui.mark" and f.bbox for m in (f.value or "").split(", ") if m}
        for f in cur.fields:
            if f.canonical != "ui.mark" or not f.bbox:
                continue
            for mk in [m for m in (f.value or "").split(", ") if m and m not in pm]:
                sk = lexicon.status_of(mk)
                evs.append(self._ev(cur, lexicon.STATUS_EVENT.get(sk or "", "other"), f"{f.label}: {mk} set on {name}",
                                    field=f.label, canonical="ui.mark", old=None, new=mk, source="typed"))
        # ----- field edits -----
        # list/grid views (no open record, multi-row tables): cells and filters change on every scroll/filter
        list_view = not cur.entity_id and any((t.get("row_count") or len(t.get("rows") or [])) > 1
                                              for t in (cur.tables or []))
        prev_f = {label_key(f.label): f for f in prev.fields}
        changes = []
        for f in cur.fields:
            k = label_key(f.label)
            p = prev_f.get(k)
            if list_view or f.canonical == "ui.mark":
                continue
            if p is None or f.bbox is None or p.bbox is None:  # newly seen / scrolled in or off: not an edit
                continue
            if (p.value or "") != (f.value or "") and not same_value(p, f):
                changes.append((k, p, f))
        win = self._window(cur.t)
        typing = [a for a in win if a.kind == "typing"]
        user = [a for a in win if a.kind in USER_KINDS]
        if not typing:
            only_scroll = bool(user) and all(a.kind == "scrolling" for a in user)
            # without typing, a value that appears or vanishes is a field scrolling in/out or a missed OCR read,
            # and re-reads while only scrolling are layout shifts — neither is an edit
            changes = [(k, p, f) for k, p, f in changes if p.value and f.value and not only_scroll]
        sources: dict[str, str] = {}
        for k, p, f in changes:
            spatial_tiles = [tl for a in typing for tl in a.tiles] + (list(changed_tiles or []) if typing else [])
            if typing and f.bbox and spatial_tiles:
                sources[k] = "typed" if any(intersects(f.bbox, tl, 4) for tl in spatial_tiles) else "?"
            elif typing:
                sources[k] = "typed" if len(changes) == 1 else "?"
            elif self.last_change_t is not None and 0 <= cur.t - self.last_change_t < 2_000:
                sources[k] = "system"  # cascaded right after another change (autofill)
            elif user:
                sources[k] = "typed" if kinds.get(k) in ("select", "link", "checkbox") else "pasted"
            else:
                sources[k] = "system"
        if any(v == "?" for v in sources.values()):
            typed = [k for k, v in sources.items() if v == "typed"]
            for k, v in list(sources.items()):
                if v == "?":
                    sources[k] = "system" if typed else "typed"
            if not typed:  # no spatial match at all: first change is the typed one, rest autofill
                for i, (k, _, _) in enumerate(changes):
                    sources[k] = "typed" if i == 0 else "system"
        # even while typing: a value appearing in / vanishing from ANOTHER field is a dropdown or popup drawn
        # over it (live: cost-center options read as "Incoterm: Maintenance - OPP"), not an edit
        changes = [(k, p, f) for k, p, f in changes if (p.value and f.value) or sources.get(k) == "typed"]
        ekey = cur.entity_id or ent
        for k, p, f in changes:
            hist = self.history.setdefault((ekey, k), [(prev.t, p.value)])
            kind = "select" if kinds.get(k) in ("select", "link") else "edit"
            # a tag/label/status field set to "On Hold", "Approved", … is that action, whatever the app calls it
            sk = lexicon.status_of(_clean(f.value or ""))
            if sk in lexicon.STATUS_EVENT and lexicon.status_of(_clean(p.value or "")) != sk:
                kind = lexicon.STATUS_EVENT[sk]
            if len(hist) >= 2 and _same_text(hist[-2][1] or "", f.value or "") and cur.t - hist[-1][0] <= UNDO_WINDOW_MS:
                kind = "undo"
            hist.append((cur.t, f.value))
            del hist[:-6]
            src = sources.get(k, "unknown")
            if kind == "undo":
                summary = f"{f.label} reverted {p.value or '∅'} → {f.value or '∅'} on {name}"
            else:
                summary = f"{f.label} changed {p.value or '∅'} → {f.value or '∅'} on {name}"
            evs.append(self._ev(cur, kind, summary, field=f.label, canonical=f.canonical, old=p.value,
                                new=f.value, source=src))
        if changes:
            self.last_change_t = cur.t

        # ----- status transitions -----
        if cur.status and (cur.status or "") != (prev.status or ""):
            sk = lexicon.status_of(cur.status) or ""
            kind = lexicon.STATUS_EVENT.get(sk, "other")
            if prev.status or kind != "other":  # "∅ → Draft" = badge just became readable, not a transition
                evs.append(self._ev(cur, kind, f"{name} status {prev.status or '∅'} → {cur.status}",
                                    field="status", old=prev.status, new=cur.status))
        if prev.status and lexicon.status_of(prev.status) == "not_saved" and \
                (not cur.status or lexicon.status_of(cur.status) != "not_saved"):
            evs.append(self._ev(cur, "save", f"Saved {name}"))
        self._tail(prev, cur, evs)
        self.last_t = cur.t
        return evs

    def _tail(self, prev: Optional[ScreenState], cur: ScreenState, evs: list[ScreenEvent]) -> None:
        name = f"{cur.entity_type or cur.view or 'screen'} {cur.entity_id or ''}".strip()
        prev_d = set(prev.dialogs) if prev else set()
        for d in cur.dialogs:
            if d not in prev_d:
                evs.append(self._ev(cur, "dialog", f"Dialog: {d}", new=d))
        prev_t = set(prev.toasts) if prev else set()
        kinds_now = {e.kind for e in evs}
        for tx in cur.toasts:
            if tx in prev_t:
                continue
            if lexicon.TOAST_ERROR.search(tx):
                evs.append(self._ev(cur, "other", f"Error: {tx}", new=tx))
            elif lexicon.TOAST_SUBMIT.search(tx) and "submit" not in kinds_now:
                evs.append(self._ev(cur, "submit", f"Submitted {name} ({tx})", new=tx))
            elif lexicon.TOAST_SAVE.search(tx) and "save" not in kinds_now:
                evs.append(self._ev(cur, "save", f"Saved {name} ({tx})", new=tx))

    def _seed(self, cur: ScreenState) -> None:
        ekey = cur.entity_id or cur.entity_type or cur.view or "screen"
        for f in cur.fields:
            self.history.setdefault((ekey, label_key(f.label)), [(cur.t, f.value)])
