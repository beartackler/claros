"""Unknowns ledger: what Claros saw but cannot explain yet (per session).

screen.events → classify (routine/judgment/guardrail/slip/habit) → open Unknown → scope →
  universal/app/occupation: try_resolve via context tools (no expert cost)
  company/personal_judgment: keep open, pre-generate spoken question (session lang)
utterance → any open/asked unknown may be resolved → answer + extracted rule.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from typing import Any, Optional

from claros.models import ContextNote, ExtractedRule, Moment, ScreenEvent, Unknown

from . import deps, lang as L, systemone

EXPIRE_MS = 60_000
DEDUPE_COS = 0.88
RECENCY_TAU_MS = 60_000
GUARDRAIL_TYPES = {"limit", "stop_and_ask", "never"}
TYPE_WEIGHT = {"limit": 3.0, "stop_and_ask": 3.0, "never": 3.0, "why": 2.0, "conflict": 2.0,
               "coverage": 2.0, "deliberate": 1.0}
CLASS_WEIGHT = {"guardrail": 3.0, "judgment": 2.0, "exception": 2.0, "slip": 1.0, "habit": 1.0}
CLASSIFY_KINDS = {"edit", "select", "hold", "escalate", "approve", "reject", "undo", "submit", "dialog"}
EXPERT_SCOPES = {"company", "personal_judgment"}
LIVE_TARGET = 3


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(" ", " ")
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        s = s.replace(",", "") if len(parts[-1]) == 3 else s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def near_round_threshold(v: Optional[float]) -> Optional[float]:
    """Return the round threshold a value sits close to (within 10%, below or above), e.g. 4,950 → 5000."""
    if v is None or v <= 0:
        return None
    best = None
    for mag in (100, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000):
        for m in (1, 2, 5) if mag in (100, 1_000, 10_000, 100_000) else (1,):
            th = mag * m
            if abs(v - th) / th <= 0.10 and v != th:
                if best is None or abs(v - th) < abs(v - best):
                    best = th
    return best


_AMOUNT_FIELD = re.compile(r"amount|total|price|rate|value|limit|sum|betrag|summe|preis|wert|montant|prix|"
                           r"importe|precio|valor|сумм|цена|стоимост|итог", re.I)
_MONEYISH = re.compile(r"[$€£₽¥]|\d[.,]\d{2}\b|\d[,.\s]\d{3}\b")


def amount_value(field: Optional[str], v: Any) -> Optional[float]:
    """Numeric value only if it plausibly is an amount (not a code like cost center 4711)."""
    if v in (None, ""):
        return None
    if not (_AMOUNT_FIELD.search(field or "") or _MONEYISH.search(str(v))):
        return None
    return _num(v)


def _fmt_num(x: float) -> str:
    return f"{int(x):,}" if float(x).is_integer() else f"{x:,.2f}"


def _ev(e: Any) -> ScreenEvent:
    if isinstance(e, ScreenEvent):
        return e
    if isinstance(e, dict):
        return ScreenEvent.model_validate(e)
    return ScreenEvent.model_validate(getattr(e, "model_dump", lambda: dict(e))())


class Ledger:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.unknowns: dict[str, Unknown] = {}
        self.meta: dict[str, dict] = {}  # id -> {class, salience, dupes, vec, value, field, threshold}
        self.events: list[ScreenEvent] = []
        self.event_class: dict[str, str] = {}
        self.context_notes: list[ContextNote] = []
        self.started_ms = deps.now_ms()
        self.asked_ids: list[str] = []
        self.off_record = False
        self.field_history: dict[str, list[str]] = {}
        self.seen_utterances: set[str] = set()
        self.lock = asyncio.Lock()
        self._pending: list = []

    # ---------- queries ----------
    def lang(self) -> str:
        return deps.session_lang(self.session_id)

    def live(self) -> list[Unknown]:
        return [u for u in self.unknowns.values() if u.status in ("open", "asked")]

    def priority(self, u: Unknown, now: Optional[float] = None) -> float:
        now = now if now is not None else deps.now_ms()
        m = self.meta.get(u.id, {})
        w = TYPE_WEIGHT.get(u.type, 1.0)
        cls_w = CLASS_WEIGHT.get(m.get("class", ""), w)
        crit = max(w, cls_w)  # criticality: guardrail 3, decision 2, step/slip 1
        sal = m.get("salience", 0.6)
        rec = math.exp(-max(0.0, now - u.created_t) / RECENCY_TAU_MS)
        unc = 1.0 - (u.hypothesis_confidence if u.hypothesis else 0.0)
        voi = sal * rec * (0.6 + 0.4 * unc)  # EVPI proxy: confident hypotheses are worth less (but still confirm)
        return round(max(0.0, voi * crit - self.redundancy(u)), 4)

    def redundancy(self, u: Unknown) -> float:
        """Penalty when the same aspect (field × type) was already asked/answered."""
        asp = self.meta.get(u.id, {}).get("aspect")
        if not asp:
            return 0.0
        n = sum(1 for o in self.unknowns.values() if o.id != u.id and o.status in ("asked", "answered")
                and self.meta.get(o.id, {}).get("aspect") == asp)
        return 0.8 * n

    def tag(self, u: Unknown) -> str:
        """mandatory → must be closed (debrief if not live); opportunistic → only if a good pause comes."""
        return "mandatory" if (u.type in GUARDRAIL_TYPES or u.scope == "company"
                               or self.meta.get(u.id, {}).get("synthetic")) else "opportunistic"

    def guardrail_asked(self) -> bool:
        return any(self.unknowns[i].type in GUARDRAIL_TYPES for i in self.asked_ids if i in self.unknowns)

    def ask_threshold(self, now: Optional[float] = None) -> float:
        """Minimum priority to be worth interrupting; relaxes as the session ages without enough asks."""
        now = now if now is not None else deps.now_ms()
        if len(self.asked_ids) >= LIVE_TARGET and self.guardrail_asked():
            return 1.2
        age_min = (now - self.started_ms) / 60_000
        return max(0.15, 1.0 - 0.15 * age_min)

    def top_candidate(self, now: Optional[float] = None) -> Optional[Unknown]:
        now = now if now is not None else deps.now_ms()
        self.expire(now)
        opens = [u for u in self.unknowns.values() if u.status == "open" and u.scope in EXPERT_SCOPES
                 and u.spoken_question and not self.meta.get(u.id, {}).get("scoping")]
        if not opens:
            return None
        for u in opens:
            u.priority = self.priority(u, now)
        need_guard = not self.guardrail_asked() and len(self.asked_ids) >= LIVE_TARGET - 1
        if need_guard:
            g = [u for u in opens if u.type in GUARDRAIL_TYPES]
            if g:
                return max(g, key=lambda u: u.priority)
        best = max(opens, key=lambda u: u.priority)
        return best if best.priority >= self.ask_threshold(now) else None

    def last_asked(self, within_ms: float = 90_000) -> Optional[Unknown]:
        now = deps.now_ms()
        for uid in reversed(self.asked_ids):
            u = self.unknowns.get(uid)
            if u and u.status == "asked" and now - self.meta.get(uid, {}).get("asked_ms", 0) <= within_ms:
                return u
        return None

    def snapshot(self) -> dict:
        live = self.live()
        top = max(live, key=lambda u: self.priority(u)) if live else None
        return {"type": "ledger", "open": len(live),
                "saved_for_later": sum(1 for u in self.unknowns.values() if u.status == "deferred"),
                "top": ({**top.model_dump(mode="json"), "tag": self.meta.get(top.id, {}).get("tag")}
                        if top else None)}

    # ---------- lifecycle ----------
    def expire(self, now: Optional[float] = None) -> list[Unknown]:
        now = now if now is not None else deps.now_ms()
        out = []
        for u in self.unknowns.values():
            if u.status == "open" and u.expires_t is not None and now >= u.expires_t:
                u.status = "deferred"
                out.append(u)
        return out

    def mark_asked(self, uid: str) -> Optional[Unknown]:
        u = self.unknowns.get(uid)
        if not u:
            return None
        u.status = "asked"
        u.expires_t = None
        self.meta.setdefault(uid, {})["asked_ms"] = deps.now_ms()
        if uid not in self.asked_ids:
            self.asked_ids.append(uid)
        return u

    async def emit(self) -> None:
        await deps.send(self.session_id, self.snapshot())
        await deps.publish(self.session_id, "ledger.changed", self.snapshot())

    # ---------- classification ----------
    def heuristic_class(self, e: ScreenEvent) -> str:
        k = e.kind
        if k == "undo":
            return "slip"
        if k in ("hold", "escalate", "reject"):
            return "guardrail"
        if k == "approve":
            return "judgment"
        if k in ("edit", "select"):
            fld = e.canonical or e.field
            if near_round_threshold(amount_value(fld, e.new)) or near_round_threshold(amount_value(fld, e.old)):
                return "guardrail"
            key = e.canonical or e.field or ""
            hist = self.field_history.get(key, [])
            if key and len(hist) >= 2 and hist[-1] == hist[-2] == (e.new or ""):
                return "habit"
            if e.old not in (None, "") and e.old != e.new and e.source in ("typed", "pasted", "unknown"):
                return "judgment"  # overriding a system/default value
        return "routine"

    async def classify(self, e: ScreenEvent) -> str:
        if e.kind not in CLASSIFY_KINDS:
            return "routine"
        heur = self.heuristic_class(e)
        q = {"event_class": {
            "type": "choice",
            "instructions": "Classify this screen-work event of an expert. Deviations from defaults or system "
                            "values are judgment; holds/escalations/limits are guardrail.",
            "criteria": {"routine": "expected default step, nothing to learn",
                         "judgment": "deliberate decision overriding a default/system value",
                         "guardrail": "stop/hold/escalate/reject or a limit/threshold check",
                         "slip": "mistake then undo",
                         "habit": "repeated personal pattern"}}}
        state = {"event": e.model_dump(mode="json"),
                 "recent": [x.summary for x in self.events[-5:]]}
        try:
            res = await systemone.decide(state, q, timeout=1.2, fallback=False)
            a = res["answers"].get("event_class") or {}
            if a.get("choice") and a.get("confidence", 0) >= 0.25:
                return a["choice"]
        except Exception:  # noqa: BLE001
            pass
        return heur

    # ---------- events ----------
    async def on_events(self, items: Any) -> list[Unknown]:
        if self.off_record or getattr(deps.get_session(self.session_id), "off_record", False):
            return []
        if isinstance(items, dict):
            items = items.get("items", [items])
        opened: list[Unknown] = []
        async with self.lock:
            for raw in items or []:
                try:
                    e = _ev(raw)
                except Exception:  # noqa: BLE001
                    continue
                self.events.append(e)
                if e.kind == "undo":
                    self._drop_reverted(e)
                cls = await self.classify(e)
                self.event_class[e.id] = cls
                key = e.canonical or e.field
                if key and e.kind in ("edit", "select"):
                    self.field_history.setdefault(key, []).append(e.new or "")
                if cls != "routine":
                    u = await self._open_for(e, cls)
                    if u:
                        opened.append(u)
                if e.kind in ("save", "submit"):
                    g = await self._requirement_guard(e)
                    if g:
                        opened.append(g)
            self.expire()
            pending, self._pending = self._pending, []
        await self.emit()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            await self.emit()
        return opened

    def _drop_reverted(self, e: ScreenEvent) -> None:
        """A value that flipped and flipped back (a dropdown over another field, a misread) is not a decision:
        drop open, not-yet-asked questions about that field (live: "why did you set the delivery terms?")."""
        fld = e.canonical or e.field
        if not fld:
            return
        ids = {x.id for x in self.events if (x.canonical or x.field) == fld and x.kind in ("edit", "select")
               and (x.entity_id or x.entity_type) == (e.entity_id or e.entity_type)}
        for u in self.unknowns.values():
            if u.status == "open" and u.about_event_ids and set(u.about_event_ids) <= ids:
                u.status = "dropped"

    async def _requirement_guard(self, e: ScreenEvent) -> Optional[Unknown]:
        """Ensure ≥1 guardrail-type question exists once the task reaches a boundary."""
        now = deps.now_ms()
        if any(u.type in GUARDRAIL_TYPES and u.scope in EXPERT_SCOPES and u.status in ("open", "asked", "answered")
               for u in self.unknowns.values()):
            return None
        self.boundaries = getattr(self, "boundaries", 0) + 1
        # a backstop, not an opener: let questions about what the expert actually did come first
        # (live: the generic one jumped ahead of "you changed the expense head — why?" on the first save)
        if (now - self.started_ms < 60_000 or self.boundaries < 2) and e.kind != "submit":
            return None
        lang = self.lang()
        u = Unknown(id=f"u_{uuid.uuid4().hex[:10]}", type="stop_and_ask", scope="company",
                    about_event_ids=[e.id], entity=e.entity_id or e.entity_type,
                    spoken_question=L.clamp_words(L.fill("submit_guard", lang, L.ACTION_TEMPLATES), 22),
                    created_t=now, expires_t=now + EXPIRE_MS,
                    moment=Moment(session_id=self.session_id, keyframe_ids=[e.keyframe_id] if e.keyframe_id else [],
                                  t=e.t))
        self.unknowns[u.id] = u
        self.meta[u.id] = {"class": "guardrail", "salience": 0.5, "dupes": 0, "synthetic": True, "tag": "mandatory", "aspect": ("task", "stop_and_ask")}
        return u

    def _shape(self, e: ScreenEvent, cls: str) -> tuple[str, Optional[str], float, Optional[float]]:
        """→ (unknown type, hypothesis, hypothesis_confidence, threshold)."""
        lang = self.lang()
        f = e.field or e.canonical or "value"
        val = e.new if e.new not in (None, "") else e.old
        fld = e.canonical or e.field
        th = near_round_threshold(amount_value(fld, e.new)) or near_round_threshold(amount_value(fld, e.old))
        if cls == "slip" or e.kind == "undo":
            return "deliberate", None, 0.0, None
        if cls == "guardrail":
            if th:
                return "limit", L.fill("threshold", lang, L.HYP_TEMPLATES, f=f, th=L.fmt_num(th, lang)), 0.75, th
            if e.kind == "reject":
                return "never", None, 0.0, None
            return "stop_and_ask", None, 0.0, None
        # judgment / habit
        same = sum(1 for x in self.events[:-1] if (x.canonical or x.field) == (e.canonical or e.field)
                   and x.new == e.new and x.new)
        if same >= 1 and val:
            sv = speakable_value(val, self.field_history.get(e.canonical or e.field, []))
            if sv:
                return "why", L.fill("override", lang, L.HYP_TEMPLATES, f=f, v=sv), min(0.9, 0.55 + 0.15 * same), None
        return "why", None, 0.0, None

    def full_values(self, e: Optional[ScreenEvent]) -> list[str]:
        """Accumulated entity model for the event's field: every value seen for it (events + current screen fields
        and grid cells), so a truncated/OCR-fragment value can be replaced by its full form."""
        if e is None or not e.field:
            return []
        out = [v for v in self.field_history.get(e.canonical or e.field, []) if v]
        for x in self.events:
            if x.field == e.field and x.entity_id == e.entity_id:
                out += [v for v in (x.new, x.old) if v]
        try:
            from claros.perception import current_state
            st = current_state(self.session_id)
        except Exception:  # noqa: BLE001
            st = None
        if st is not None and (not e.entity_id or st.entity_id in (None, e.entity_id)):
            base = re.sub(r"\s*\*$", "", e.field).strip().lower()
            out += [f.value for f in st.fields if f.value and re.sub(r"\s*\*$", "", f.label or "").strip().lower() == base]
            for tb in st.tables or []:
                cols = [re.sub(r"\s*\*$", "", str(c)).strip().lower() for c in tb.get("columns") or []]
                if base in cols:
                    i = cols.index(base)
                    out += [str(r[i]) for r in tb.get("rows") or [] if isinstance(r, list) and len(r) > i and r[i]]
        return list(dict.fromkeys(out))

    def _action_parts(self, u: Unknown, e: Optional[ScreenEvent]) -> tuple[str, dict]:
        """Which action template fits this unknown + the template slots (plain-word field, speakable value...)."""
        m = self.meta.get(u.id, {})
        lang = self.lang()
        if m.get("synthetic"):
            return "submit_guard", {}
        th = m.get("threshold")
        if u.type == "limit" and th:
            return "limit", {"th": L.fmt_num(th, lang)}
        if e is None:
            return "generic", {"n": "that one"}
        n = record_noun(e, lang)
        f = plain_label(e.field or e.canonical)
        k = e.kind
        if k == "undo" or m.get("class") == "slip":
            return ("undo", {"f": f}) if f else ("generic", {"n": n})
        if k == "hold":
            return "hold", {"n": n}
        if k == "escalate":
            second = re.search(r"second|zweit|deuxi|segund|втор", f"{e.new or ''} {e.summary or ''}", re.I)
            return "escalate", {"n": n, "appr": L.APPROVAL_WORDS["second" if second else "plain"]}
        if k == "reject":
            return "reject", {"n": n}
        if k == "approve":
            return "approve", {"n": n}
        if k in ("edit", "select") and f:
            raw = e.new if e.new not in (None, "") else e.old
            v = speakable_value(raw, self.full_values(e)) if raw else None
            return ("edit", {"f": f, "v": v}) if v else ("edit_nov", {"f": f})
        return "generic", {"n": n}

    def make_question(self, u: Unknown, e: Optional[ScreenEvent]) -> str:
        """Action-phrased question: what the expert DID in plain words, then why (never a raw field dump or a
        truncated value). LLM phrasing (meta llm_q / action) wins when it passed validation; templates otherwise."""
        lang = self.lang()
        m = self.meta.get(u.id, {})
        key, kw = self._action_parts(u, e)
        if key in ("limit", "submit_guard"):
            return L.clamp_words(L.fill(key, lang, L.ACTION_TEMPLATES, **kw), 22)
        if u.hypothesis and u.hypothesis_confidence >= 0.7:
            clause = m.get("action")
            if not clause and key in L.ACTION_CLAUSES:
                clause = L.fill(key, lang, L.ACTION_CLAUSES, **kw)
            if clause:
                h = u.hypothesis.strip().rstrip(".?!")
                h = h[:1].lower() + h[1:] if h[:2] != h[:2].upper() else h
                return L.clamp_words(L.fill("confirm", lang, L.ACTION_TEMPLATES, a=clause.rstrip(" .—-"), h=h), 22)
            return L.clamp_words(L.fill("confirm", lang, h=u.hypothesis), 22)
        if m.get("llm_q"):
            return m["llm_q"]
        return L.clamp_words(L.fill(key, lang, L.ACTION_TEMPLATES, **kw), 22)

    def refresh_question(self, u: Unknown) -> None:
        """Right before speaking: re-read the asked value from the CURRENT screen (vision often fixes an OCR
        glyph the event captured, e.g. '… - OPF' → '… - OPP'). LLM-phrased questions passed the fragment check."""
        m = self.meta.get(u.id, {})
        e = m.get("event")
        if e is None or not e.field or not e.new or m.get("llm_q") or (u.hypothesis and u.hypothesis_confidence >= 0.7):
            return
        try:
            from claros.perception import current_state
            from claros.perception.diff import _same_text
            st = current_state(self.session_id)
        except Exception:  # noqa: BLE001
            return
        if st is None:
            return
        cur = next((f.value for f in st.fields if f.label == e.field and f.value), None)
        if cur and cur != e.new and _same_text(e.new, cur):
            u.spoken_question = self.make_question(u, e.model_copy(update={"new": cur}))

    async def _phrase(self, u: Unknown, e: ScreenEvent) -> None:
        """GLM (fast) phrases the expert's action like a colleague would ("You moved that one to capex — what made
        you do that?"). Validated; any failure keeps the deterministic template."""
        key, kw = self._action_parts(u, e)
        if key in ("limit", "submit_guard"):
            return
        lang = self.lang()
        raw_new = e.new if e.new not in (None, "") else None
        payload = {"event_kind": e.kind, "field": plain_label(e.field or e.canonical),
                   "old": speakable_value(e.old, self.full_values(e)) if e.old else None,
                   "new": speakable_value(raw_new, self.full_values(e)) if raw_new else None,
                   "summary": e.summary, "record_kind": e.entity_type, "question_type": u.type,
                   "recent": [x.summary for x in self.events[-4:] if x.summary and x.id != e.id]}
        sys = ("An apprentice watched an expert work on screen and will ask ONE short spoken question about what the "
               "expert just did. Describe the ACTION in plain colleague words, second person, past tense — its "
               "business meaning, not raw field names or codes (e.g. 'You moved that one to capex', 'You put that "
               "invoice on hold', 'You sent that one for a second approval'). Then ask why in a few words. Never "
               "include record ids, cut-off text, ALL-CAPS labels or account suffixes; never guess the reason. "
               f"Language: {L.LANG_NAMES.get(lang, 'English')}. Reply JSON {{\"action\": \"<≤10 words>\", "
               "\"question\": \"<action> — <short why question>?\"}, question ≤20 words.")
        try:
            txt = await asyncio.wait_for(deps.llm_chat(
                [{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                model_role="fast", json_schema={"type": "object"}, temperature=0.3), 4.0)
        except Exception:  # noqa: BLE001
            return
        d = systemone._parse_json(txt) if txt else None
        if not isinstance(d, dict):
            return
        q, a = valid_phrasing(d.get("question"), d.get("action"), lang)
        if q:
            m = self.meta.setdefault(u.id, {})
            m["llm_q"], m["action"] = q, a

    def _ctx(self, e: ScreenEvent) -> dict:
        return {"app": e.app, "entity_type": e.entity_type, "field": e.field, "value": e.new,
                "event": e.summary}

    async def _open_for(self, e: ScreenEvent, cls: str) -> Optional[Unknown]:
        utype, hyp, hconf, th = self._shape(e, cls)
        now = deps.now_ms()
        text_key = f"{utype} {e.canonical or e.field or ''} {e.summary or ''} {e.new or ''}"
        # ---- dedupe ----
        vecs = await deps.embed([text_key])
        vec = vecs[0] if vecs else None
        for u in self.unknowns.values():
            if u.status not in ("open", "asked", "deferred"):
                continue
            m = self.meta.get(u.id, {})
            dup = False
            if vec is not None and m.get("vec") is not None:
                dup = deps.cosine(vec, m["vec"]) > DEDUPE_COS
            elif vec is None:
                dup = m.get("key") == (utype, e.canonical or e.field, e.kind)
            if dup:
                u.about_event_ids.append(e.id)
                m["dupes"] = m.get("dupes", 0) + 1
                m["salience"] = min(1.0, m.get("salience", 0.6) + 0.1)
                if u.status == "open":
                    u.created_t = now
                    u.expires_t = now + EXPIRE_MS
                return None
        salience = 0.6 + 0.2 * (e.confidence >= 0.7) + 0.2 * (e.kind in ("hold", "escalate", "reject", "approve"))
        u = Unknown(id=f"u_{uuid.uuid4().hex[:10]}", type=utype, about_event_ids=[e.id],
                    entity=e.entity_id or e.entity_type, hypothesis=hyp, hypothesis_confidence=hconf,
                    created_t=now, expires_t=now + EXPIRE_MS,
                    moment=Moment(session_id=self.session_id,
                                  keyframe_ids=[e.keyframe_id] if e.keyframe_id else [], t=e.t))
        self.meta[u.id] = {"class": cls, "salience": salience, "dupes": 0, "vec": vec,
                           "key": (utype, e.canonical or e.field, e.kind), "event": e, "threshold": th,
                           "aspect": (e.canonical or e.field or e.kind, utype)}
        u.spoken_question = self.make_question(u, e)
        u.scope = "company" if utype in GUARDRAIL_TYPES else "personal_judgment"
        u.priority = self.priority(u, now)
        self.meta[u.id]["scoping"] = True  # not askable until scope is known
        self.unknowns[u.id] = u
        self._pending.append(self._scope(u, e))
        return u

    async def _scope(self, u: Unknown, e: ScreenEvent) -> None:
        """Ask less: generic-scope unknowns are answered by context tools, never by the expert."""
        scope = None
        try:
            scope = await asyncio.wait_for(deps.classify_scope(u, self.session_id, self._ctx(e)), 4.0)
        except Exception:  # noqa: BLE001
            deps.log.debug("classify_scope failed", exc_info=True)
        if scope in ("universal", "app", "occupation", "company", "personal_judgment"):
            u.scope = scope
        if u.scope not in EXPERT_SCOPES:
            note = None
            try:
                note = await asyncio.wait_for(deps.try_resolve(u, u.scope, self.session_id, self._ctx(e)), 10.0)
            except Exception:  # noqa: BLE001
                deps.log.debug("try_resolve failed", exc_info=True)
            note = _as_note(note, u.scope)
            if note:
                u.status = "resolved"
                u.resolution = note.text
                u.resolution_source = note.source
                u.expires_t = None
                self.context_notes.append(note)
                await deps.send(self.session_id, looked_up_msg(u, e, note))
            else:
                u.scope = "company" if u.type in GUARDRAIL_TYPES else "personal_judgment"
        if u.status == "open" and u.scope in EXPERT_SCOPES:
            async def hyp() -> None:
                try:
                    await asyncio.wait_for(self._hypothesize(u, e), 8.0)
                except Exception:  # noqa: BLE001
                    deps.log.debug("hypothesis sampling failed", exc_info=True)

            async def phrase() -> None:
                try:
                    await self._phrase(u, e)
                except Exception:  # noqa: BLE001
                    deps.log.debug("question phrasing failed", exc_info=True)
            await asyncio.gather(hyp(), phrase())
            u.spoken_question = self.make_question(u, e)
        self.meta.setdefault(u.id, {})["tag"] = self.tag(u)
        if u.status == "open":
            now = deps.now_ms()
            u.expires_t = max(u.expires_t or 0, now + EXPIRE_MS - 0)  # scoping time doesn't eat the window
        self.meta.get(u.id, {}).pop("scoping", None)
        deps.store_log(self.session_id, "unknown.opened", u.model_dump(mode="json"))

    async def _hypothesize(self, u: Unknown, e: ScreenEvent) -> None:
        """Sample 5 hypotheses, cluster; top cluster share ≥0.7 → confirm-style question, else open why."""
        hyps = await sample_hypotheses(u, e, self.lang(), [x.summary for x in self.events[-6:] if x.summary])
        if len(hyps) < 3:
            return
        rep, share = await cluster_top(hyps)
        m = self.meta.setdefault(u.id, {})
        m["hypotheses"] = hyps
        m["hypothesis_share"] = share
        if share >= 0.7 or share > u.hypothesis_confidence:
            u.hypothesis, u.hypothesis_confidence = rep, round(share, 2)
        u.spoken_question = self.make_question(u, e)
        u.priority = self.priority(u)

    # ---------- utterances ----------
    async def on_utterance(self, p: dict) -> list[Unknown]:
        if not isinstance(p, dict) or p.get("role", "user") != "user":
            return []
        if self.off_record or getattr(deps.get_session(self.session_id), "off_record", False):
            return []
        eid = p.get("event_id") or f"{p.get('t_start')}:{p.get('text')}"
        if eid in self.seen_utterances:
            return []
        self.seen_utterances.add(eid)
        text = (p.get("text") or "").strip()
        if len(text.split()) < 2:
            return []
        cands = [u for u in self.unknowns.values() if u.status in ("asked", "open", "deferred")
                 and u.scope in EXPERT_SCOPES]
        cands.sort(key=lambda u: (u.status != "asked", -self.meta.get(u.id, {}).get("asked_ms", 0),
                                  -self.priority(u)))
        cands = cands[:5]
        if not cands:
            return []
        verdicts = await self.check_resolution(text, cands)
        done = []
        for u in cands:
            v = verdicts.get(u.id)
            if v == "resolved" or (v == "partial" and u.status == "asked"):
                await self.record_answer(u.id, text, utterance_id=p.get("event_id"), emit=False)
                done.append(u)
        if done:
            await self.emit()
        return done

    async def check_resolution(self, text: str, cands: list[Unknown]) -> dict[str, str]:
        qs = {u.id: {"type": "choice",
                     "instructions": f"Does the expert's utterance answer this question: {u.spoken_question!r} "
                                     f"(hypothesis: {u.hypothesis!r})?",
                     "criteria": {"resolved": "fully answers it (reason/limit/rule given)",
                                  "partial": "partly answers it",
                                  "unresolved": "does not address it"}} for u in cands}
        last = self.last_asked(30_000)

        def rules(_state: Any, questions: dict) -> dict:
            out = {}
            low = text.lower()
            for uid in questions:
                u = self.unknowns[uid]
                m = self.meta.get(uid, {})
                e = m.get("event")
                hits = 0
                if e is not None:
                    for tok in (e.field, e.new, e.old, e.canonical):
                        if tok and str(tok).lower() in low:
                            hits += 1
                if last is not None and last.id == uid and len(text.split()) >= 3:
                    c = "resolved"
                elif hits >= 2:
                    c = "resolved"
                elif hits == 1:
                    c = "partial"
                else:
                    c = "unresolved"
                out[uid] = {"choice": c, "probabilities": {c: 1.0}}
            return out

        try:
            res = await systemone.decide({"utterance": text}, qs, timeout=2.0, rules=rules)
            return {k: (a.get("choice") or "unresolved") for k, a in res["answers"].items()}
        except Exception:  # noqa: BLE001
            return {k: a["choice"] for k, a in rules(None, qs).items()}

    async def record_answer(self, uid: Optional[str], text: str, *, utterance_id: Optional[str] = None,
                            source: str = "expert", emit: bool = True) -> Optional[Unknown]:
        u = self.unknowns.get(uid) if uid else self.last_asked()
        if u is None:
            return None
        u.resolution = (u.resolution + " " + text) if (u.status == "answered" and u.resolution) else text
        u.status = "answered"
        u.resolution_source = source
        u.expires_t = None
        if utterance_id:
            u.answer_utterance_ids.append(utterance_id)
        u.extracted_rule = await extract_rule(text, u)
        deps.store_log(self.session_id, "unknown.answered", u.model_dump(mode="json"))
        if emit:
            await self.emit()
        return u

    def strike_last(self) -> Optional[Unknown]:
        ans = [u for u in self.unknowns.values() if u.status == "answered" and u.resolution_source == "expert"]
        if not ans:
            return None
        u = ans[-1]
        u.status = "open"
        u.resolution = None
        u.extracted_rule = None
        u.answer_utterance_ids = []
        u.expires_t = deps.now_ms() + EXPIRE_MS
        return u

    def end_task(self) -> None:
        for u in self.unknowns.values():
            if u.status in ("open", "asked"):
                u.status = "deferred"


def plain_label(label: Optional[str]) -> Optional[str]:
    """'Expense Head *' → 'expense head', 'PRIORITY' → 'priority'; short acronyms (VAT, PO) stay."""
    t = _ui_text(label)
    if not t:
        return None
    t = re.sub(r"\s*\[\d+\]$", "", t).strip()
    words = [w if (w.isupper() and len(w) <= 4) else w.lower() for w in t.split()]
    return " ".join(words) or None


def record_noun(e: Optional[ScreenEvent], lang: str = "en") -> str:
    """'that invoice' / 'that ticket' (English only; other languages use the template's own pronoun)."""
    if (lang or "en")[:2] != "en" or e is None or not e.entity_type:
        return "that one"
    w = re.sub(r"[^A-Za-z ]", " ", e.entity_type).split()
    w = w[-1].lower() if w else ""
    return f"that {w}" if 3 <= len(w) <= 14 and w not in ("form", "list", "view", "record", "page") else "that one"


_ALLCAPS = re.compile(r"\b[A-ZÄÖÜА-ЯЁ]{5,}\b")
_IDLIKE = re.compile(r"\b[A-Z]{2,}[-/][\w-]*\d")
_FRAG = re.compile(r"\s[-–]\s?[A-Z]{2,4}\b")


def valid_phrasing(q: Any, a: Any, lang: str = "en") -> tuple[Optional[str], Optional[str]]:
    """Accept an LLM-phrased question only if it speaks like a colleague: ends with '?', ≤22 words, no ellipsis /
    ALL-CAPS label / record id / ERP suffix fragment, and (English) starts with 'You '."""
    if not isinstance(q, str) or not isinstance(a, str):
        return None, None
    q, a = re.sub(r"\s+", " ", q).strip(), re.sub(r"\s+", " ", a).strip().rstrip(".?!—- ")
    if not q.endswith("?") or not a or len(q.split()) > 22 or len(a.split()) > 12:
        return None, None
    for bad in (_ALLCAPS, _IDLIKE, _FRAG):
        if bad.search(q) or bad.search(a):
            return None, None
    if "..." in q or "…" in q or "⟦" in q:
        return None, None
    if (lang or "en")[:2] == "en" and not (q.startswith("You ") and a.startswith("You ")):
        return None, None
    return q, a


def note_source(src: Optional[str]) -> dict:
    """ContextNote.source → looked_up.source {kind, title, url?}."""
    s = (src or "").strip()
    if s.startswith("app_docs:"):
        s = s[len("app_docs:"):]
    if s.startswith("http"):
        from urllib.parse import urlparse
        u = urlparse(s)
        tail = [p for p in u.path.split("/") if p][-1:] if u.path else []
        title = (u.hostname or "docs") + (f" › {tail[0].replace('-', ' ').replace('_', ' ')}" if tail else "")
        return {"kind": "app_docs", "title": title, "url": s}
    if s.startswith("onet:"):
        return {"kind": "onet", "title": f"O*NET {s[5:]}"}
    return {"kind": "general", "title": "General knowledge"}


def looked_up_msg(u: Unknown, e: Optional[ScreenEvent], note: ContextNote) -> dict:
    ans = note.text if len(note.text) <= 300 else note.text[:299].rsplit(" ", 1)[0] + "…"
    return {"type": "looked_up", "unknown_id": u.id,
            "unknown_summary": u.spoken_question or (e.summary if e else None) or u.type,
            "answer": ans, "source": note_source(note.source)}


_TRUNC = re.compile(r"\s*(\.\.\.|…)\s*$")
_FRAG_SUFFIX = re.compile(r"\s+[-–—|/:]\s*[^\s\d]{0,3}$")  # ' - OPF', ' - O', ' -' (ERP company suffixes, cut cells)


def _is_numberish(t: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,'%€$£₽+-]+", t)) and bool(re.search(r"\d", t))


def speakable_value(v: Optional[str], full: Any = ()) -> Optional[str]:
    """A screen value fit to SPEAK in a question, or None (→ use the field label instead).
    Never a truncated/ellipsized cell ('Tools and Small Equipment - O...'), a trailing short fragment
    ('Plants and Machineries - OPF' → 'Plants and Machineries') or a non-numeric fragment under 4 chars ('Ol').
    `full` = other values seen for the same field (entity model): a complete value that the truncated one is a
    prefix of wins."""
    if v is None:
        return None
    t = str(v).strip()
    if not t:
        return None
    truncated = bool(_TRUNC.search(t))
    base = _TRUNC.sub("", t).strip()
    nb = re.sub(r"\W+", " ", base.lower()).strip()
    best = None
    for c in full or ():
        c = str(c).strip()
        if not c or _TRUNC.search(c) or c == t:
            continue
        nc = re.sub(r"\W+", " ", c.lower()).strip()
        if len(nb) >= 4 and nc.startswith(nb) and len(nc) > len(nb) and (best is None or len(c) > len(best)):
            best = c
    if best is not None:
        t, truncated = best, False
    t = _ui_text(t) or ""
    if truncated:
        t = _TRUNC.sub("", t).strip()
        t = t.rsplit(" ", 1)[0] if " " in t else ""  # the last word was cut mid-way
    if _is_numberish(t):
        return t
    prev = None
    while prev != t:
        prev = t
        t = _FRAG_SUFFIX.sub("", t).strip().rstrip("-–—|/:,;").strip()
    if len(t) < 4 and not _is_numberish(t):
        return None
    return t


def _ui_text(v: Optional[str]) -> Optional[str]:
    """Screen text fit to speak: no tag close glyphs ('On Hold ×'), truncation dots or required-field stars."""
    if not v:
        return v
    t = re.sub(r"\s*[×✕]\s*$", "", str(v)).strip()
    t = re.sub(r"\s*(\.\.\.|…)$", "", t)
    t = re.sub(r"\s*\*$", "", t)
    return t.strip() or v


def _as_note(r: Any, scope: str) -> Optional[ContextNote]:
    if r is None:
        return None
    if isinstance(r, ContextNote):
        return r
    if isinstance(r, str):
        return ContextNote(id=f"cn_{uuid.uuid4().hex[:8]}", text=r, scope=scope, source="llm") if r.strip() else None
    if isinstance(r, dict):
        if not r.get("text"):
            return None
        return ContextNote(id=r.get("id") or f"cn_{uuid.uuid4().hex[:8]}", text=r["text"],
                           scope=r.get("scope") or scope, source=r.get("source") or "llm")
    t = getattr(r, "text", None)
    if t:
        return ContextNote(id=getattr(r, "id", None) or f"cn_{uuid.uuid4().hex[:8]}", text=t,
                           scope=getattr(r, "scope", scope) or scope, source=getattr(r, "source", "llm") or "llm")
    return None


# ---------- hypotheses (sample → cluster) ----------
async def sample_hypotheses(u: Unknown, e: ScreenEvent, lang: str, recent: list[str], n: int = 5) -> list[str]:
    sys = (f"An expert did this on screen; guess WHY. Give {n} independent hypotheses for the rule/reason, each "
           f"≤10 words, phrased as a rule clause (e.g. 'invoices over 5,000 go to the CFO'), in "
           f"{L.LANG_NAMES.get(lang, 'English')}. Reply JSON {{\"hypotheses\": [..{n} strings..]}}.")
    user = json.dumps({"event": e.summary or f"{e.kind} {e.field}: {e.old} → {e.new}", "field": e.field,
                       "old": e.old, "new": e.new, "entity": e.entity_type, "recent": recent,
                       "question_type": u.type}, ensure_ascii=False)
    txt = await deps.llm_chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
                              model_role="fast", json_schema={"type": "object"}, temperature=0.9)
    if not txt:
        return []
    d = systemone._parse_json(txt)
    hs = d.get("hypotheses") if isinstance(d, dict) else None
    return [str(h).strip() for h in (hs or []) if str(h).strip()][:n]


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", s.lower()) if len(w) > 2}


async def cluster_top(hyps: list[str], cos: float = 0.8) -> tuple[str, float]:
    """Greedy clustering; returns (representative of largest cluster, its share)."""
    vecs = await deps.embed(hyps)

    def sim(i: int, j: int) -> float:
        if vecs:
            return deps.cosine(vecs[i], vecs[j])
        a, b = _tokens(hyps[i]), _tokens(hyps[j])
        return len(a & b) / max(1, len(a | b))

    thr = cos if vecs else 0.5
    clusters: list[list[int]] = []
    for i in range(len(hyps)):
        for c in clusters:
            if sim(i, c[0]) >= thr:
                c.append(i)
                break
        else:
            clusters.append([i])
    best = max(clusters, key=len)
    rep = max(best, key=lambda i: sum(sim(i, j) for j in best))
    return hyps[rep], len(best) / len(hyps)


# ---------- rule extraction ----------
_gliner = None
_gliner_failed = False
RULE_SCHEMA = {"rule": ["condition::str::When the rule applies", "threshold::str::Numeric limit or amount",
                        "action::str::What the expert does", "escalate_to::str::Person/role to ask or escalate to"]}


def _gliner_extract(text: str) -> Optional[dict]:
    global _gliner, _gliner_failed
    if _gliner_failed:
        return None
    try:
        if _gliner is None:
            from gliner2 import GLiNER2  # type: ignore
            import os
            _gliner = GLiNER2.from_pretrained(os.getenv("CLAROS_GLINER2_MODEL", "fastino/gliner2-base-v1"))
        r = _gliner.extract_json(text, RULE_SCHEMA)
        rows = r.get("rule") if isinstance(r, dict) else None
        if rows:
            row = rows[0] if isinstance(rows, list) else rows
            return {k: (v if isinstance(v, str) or v is None else str(v)) for k, v in row.items()}
    except Exception:  # noqa: BLE001
        _gliner_failed = True
        deps.log.info("gliner2 unavailable; using LLM/regex for rule extraction")
    return None


def _regex_rule(text: str) -> ExtractedRule:
    m = re.search(r"(\d[\d.,\s]*\d|\d)\s*(k|тыс|000)?", text)
    th = m.group(0).strip() if m else None
    esc = None
    m2 = re.search(r"(?:ask|escalate to|check with|frag(?:e|en)?|demande(?:r)? à|pregunt\w* a|спрос\w*|к)\s+"
                   r"([A-ZА-ЯÄÖÜ]?[\w\-]+(?:\s[\w\-]+)?)", text, re.I)
    if m2:
        esc = m2.group(1)
    return ExtractedRule(condition=None, threshold=th, action=None, escalate_to=esc)


async def extract_rule(text: str, u: Optional[Unknown] = None) -> Optional[ExtractedRule]:
    if len(text.split()) < 3:
        return None
    g = await asyncio.to_thread(_gliner_extract, text) if not _gliner_failed and _gliner_enabled() else None
    if g:
        try:
            return ExtractedRule(**{k: g.get(k) for k in ("condition", "threshold", "action", "escalate_to")})
        except Exception:  # noqa: BLE001
            pass
    try:
        txt = await asyncio.wait_for(deps.llm_chat([
            {"role": "system", "content": "Extract the expert's rule. Reply JSON {condition, threshold, action, "
             "escalate_to} (null when absent). Keep the expert's language."},
            {"role": "user", "content": json.dumps({"question": u.spoken_question if u else None,
                                                    "answer": text}, ensure_ascii=False)},
        ], model_role="fast", json_schema={"type": "object"}), 6.0)
        if txt:
            d = systemone._parse_json(txt)
            return ExtractedRule(**{k: (None if d.get(k) is None else str(d.get(k)))
                                    for k in ("condition", "threshold", "action", "escalate_to")})
    except Exception:  # noqa: BLE001
        pass
    return _regex_rule(text)


def _gliner_enabled() -> bool:
    import os
    return os.getenv("CLAROS_GLINER2", "1") != "0"


# ---------- registry ----------
ledgers: dict[str, Ledger] = {}


def get_ledger(session_id: str) -> Ledger:
    lg = ledgers.get(session_id)
    if lg is None:
        lg = ledgers[session_id] = Ledger(session_id)
    return lg
