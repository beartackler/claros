"""Shared knowledge helpers: map persistence, coverage, evidence validation, predicates, i18n."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from claros.models import (
    Coverage, Guardrail, Moment, Quote, ScreenState, Step, Unknown, WorkMap,
)

from . import _deps as d

# ---------------- persistence ----------------


def load_map(workflow_id: str, version: Optional[int] = None) -> Optional[WorkMap]:
    raw = d.st_call("get_workflow", workflow_id, version)
    if not raw:
        return None
    try:
        return WorkMap.model_validate(raw)
    except Exception:  # noqa: BLE001
        d.log.warning("invalid stored map %s", workflow_id, exc_info=True)
        return None


def list_maps() -> list[WorkMap]:
    out = []
    for raw in d.st_call("list_workflows", default=[]) or []:
        try:
            out.append(WorkMap.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def map_doc_text(wm: WorkMap) -> str:
    parts = [wm.name, " ".join(wm.apps)]
    if wm.onet:
        parts += [wm.onet.occupation_title, wm.onet.task or ""]
    parts += [s.title for s in wm.steps]
    parts += [g.text for g in wm.guardrails]
    for aliases in wm.canonical_vars.values():
        parts += aliases
    return " \n".join(p for p in parts if p)


async def save_map(wm: WorkMap, session_id: Optional[str] = None, *, recompute: bool = True) -> WorkMap:
    """Store as a new workflow version, index for lookup, publish map.updated."""
    if recompute:
        wm.coverage = compute_coverage(wm)
    ver = d.st_call("put_workflow", wm)
    if isinstance(ver, int):
        wm.version = ver
    text = map_doc_text(wm)
    d.st_call("index_text", "workflows", wm.workflow_id, text)
    try:
        vec = (await d.embed([text], "retrieval.passage"))[0]
        d.st_call("index_vec", "workflows", wm.workflow_id, vec)
    except Exception:  # noqa: BLE001
        pass
    sid = session_id or "_global"
    await d.publish(sid, "map.updated", {"workflow_id": wm.workflow_id, "version": wm.version})
    await d.send(sid, {"type": "map_updated", "workflow_id": wm.workflow_id, "version": wm.version})
    return wm


# ---------------- evidence + coverage ----------------

OPEN_STATUSES = ("open", "asked", "deferred")


def moment_ok(m: Optional[Moment]) -> bool:
    return bool(m and m.keyframe_ids and m.utterance_ids)


def guardrail_ok(g: Guardrail) -> bool:
    return any(moment_ok(m) for m in g.evidence)


def validate_evidence(wm: WorkMap, known_keyframes: Optional[set[str]] = None,
                      known_utterances: Optional[set[str]] = None) -> list[str]:
    """Return human-readable errors for steps/guardrails lacking a Moment (keyframe + utterance)."""
    errs: list[str] = []

    def bad_ids(m: Moment) -> list[str]:
        out = []
        if known_keyframes is not None:
            out += [k for k in m.keyframe_ids if k not in known_keyframes]
        if known_utterances is not None:
            out += [u for u in m.utterance_ids if u not in known_utterances]
        return out

    for s in wm.steps:
        if not moment_ok(s.moment):
            errs.append(f"step {s.id} '{s.title}' has no moment with keyframe_ids AND utterance_ids")
        elif bad_ids(s.moment):
            errs.append(f"step {s.id} references unknown ids {bad_ids(s.moment)}")
    for g in wm.guardrails:
        if not guardrail_ok(g):
            errs.append(f"guardrail {g.id} '{g.text}' has no evidence moment with keyframe_ids AND utterance_ids")
        else:
            for m in g.evidence:
                if bad_ids(m):
                    errs.append(f"guardrail {g.id} references unknown ids {bad_ids(m)}")
    return errs


def compute_coverage(wm: WorkMap) -> Coverage:
    if not wm.steps:
        return Coverage(status="missing", open_unknowns=len([u for u in wm.open_unknowns
                                                             if u.status in OPEN_STATUSES]))
    n = len(wm.steps)
    swe = sum(1 for s in wm.steps if moment_ok(s.moment)) / n
    judg = [s for s in wm.steps if s.decision and s.decision.kind == "judgment"]
    jc = (sum(1 for s in judg if s.decision.reason_quote_ids) / len(judg)) if judg else 1.0
    gc = (sum(1 for g in wm.guardrails if guardrail_ok(g) and g.quote_ids and (g.predicate or g.fuzzy))
          / len(wm.guardrails)) if wm.guardrails else 1.0
    ou = len([u for u in wm.open_unknowns if u.status in OPEN_STATUSES])
    conflicts = sum(1 for s in wm.steps if s.conflict)
    ready = swe >= 1.0 and jc >= 1.0 and gc >= 1.0 and ou == 0 and conflicts == 0 and bool(wm.approved_by)
    return Coverage(status="ready" if ready else "partial", steps_with_evidence=round(swe, 3),
                    judgments_complete=round(jc, 3), guardrails_complete=round(gc, 3),
                    open_unknowns=ou, conflicts=conflicts)


def checks_pass(wm: WorkMap) -> bool:
    c = compute_coverage(wm)
    return c.steps_with_evidence >= 1.0 and c.judgments_complete >= 1.0 and c.guardrails_complete >= 1.0


def step_by_id(wm: WorkMap, sid: str) -> Optional[Step]:
    return next((s for s in wm.steps if s.id == sid), None)


def guardrail_by_id(wm: WorkMap, gid: str) -> Optional[Guardrail]:
    return next((g for g in wm.guardrails if g.id == gid), None)


def quote_by_id(wm: WorkMap, qid: str) -> Optional[Quote]:
    return next((q for q in wm.quotes if q.id == qid), None)


def quote_text(q: Quote, lang: str) -> str:
    return q.translations.get(lang) or q.text


def ordered_steps(wm: WorkMap) -> list[Step]:
    return sorted(wm.steps, key=lambda s: s.order)


def map_lang(wm: WorkMap) -> str:
    return wm.quotes[0].lang if wm.quotes else "en"


def new_unknown(type_: str, text: str, *, scope: str = "personal_judgment", moment: Optional[Moment] = None,
                entity: Optional[str] = None, priority: float = 0.5, hypothesis: Optional[str] = None) -> Unknown:
    return Unknown(id=d.new_id("u"), type=type_, scope=scope, spoken_question=text, entity=entity,
                   priority=priority, moment=moment, created_t=d.now_ms(), hypothesis=hypothesis)


# ---------------- predicates (json-logic over canonical vars) ----------------

_NUM = re.compile(r"^[^\d\-+]*([-+]?[\d\s.,'  ]+)[^\d]*$")


def parse_number(s: Any) -> Optional[float]:
    """Locale-tolerant: '5.200,00' '5,200.00' '5 200,00' '5200' '€ 5.200' → 5200.0."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = _NUM.match(str(s).strip())
    if not m:
        return None
    t = re.sub(r"[\s'  ]", "", m.group(1))
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        parts = t.split(sep)
        t = "".join(parts) if (len(parts) > 2 or len(parts[-1]) == 3) else ".".join(parts)
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(s: Any) -> Optional[tuple[int, int, int]]:
    """'2026-12-03' '03.12.2026' '12/03/2026' (US) '31/12/2026' → (y, m, d)."""
    if not isinstance(s, str):
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(r"(\d{1,2})([./-])(\d{1,2})\2(\d{2,4})", s)
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
    y = y + 2000 if y < 100 else y
    if m.group(2) == "." or a > 12:
        day, mon = a, b
    else:
        mon, day = a, b
    if not (1 <= mon <= 12):
        day, mon = mon, day
    return (y, mon, day) if 1 <= mon <= 12 else None


def norm_label(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode() or (s or "")
    return re.sub(r"[^\w]+", " ", s.lower(), flags=re.UNICODE).strip()


def _norm_value(f: Any) -> Any:
    if isinstance(f.normalized, dict):  # perception money/percent: {"value": 7200.0, "currency": "EUR"}
        if isinstance(f.normalized.get("value"), (int, float)):
            return f.normalized["value"]
    elif f.normalized is not None:
        return f.normalized
    n = parse_number(f.value)
    if parse_date(f.value):
        return f.value.strip()
    if n is not None and f.value and re.search(r"\d", f.value) and not re.search(r"[A-Za-z]{2,}\d|\d[A-Za-z]{2,}", f.value):
        return n
    return (f.value or "").strip()


def canonical_vars_from_state(state: ScreenState, wm: WorkMap) -> dict[str, Any]:
    """Map learner screen fields → canonical vars via field.canonical or alias labels (any language)."""
    # one on-screen label can back several canonical vars ("Amount (EUR)" → line.amount AND invoice.amount)
    alias: dict[str, list[str]] = {}
    exact: dict[str, list[str]] = {}
    for canon, labels in wm.canonical_vars.items():
        for lab in labels:
            exact.setdefault(norm_label(lab), []).append(canon)
        for k in (norm_label(canon), norm_label(canon.split(".")[-1])):
            alias.setdefault(k, []).append(canon)
    out: dict[str, Any] = {}
    fields = sorted(state.fields, key=lambda f: f.bbox is None)  # visible values win over scrolled-off ones
    for f in fields:
        nl = norm_label(re.sub(r"\s*\[\d+\]$", "", f.label or ""))
        canons = [f.canonical] if f.canonical and f.canonical != "ui.mark" else (exact.get(nl) or alias.get(nl) or [])
        for canon in canons:
            if canon in out:
                continue
            out[canon] = _norm_value(f)
            if canon.endswith(("_month", "_year")):  # a month/year var read straight off a date label
                dt = parse_date(f.normalized if isinstance(f.normalized, str) else f.value)
                if dt:
                    out[canon] = dt[1] if canon.endswith("_month") else dt[0]
            if canon.endswith("_date"):
                dt = parse_date(f.normalized if isinstance(f.normalized, str) else f.value)
                if dt:
                    out[canon[:-5] + "_month"] = dt[1]
                    out[canon[:-5] + "_year"] = dt[0]
    for k, v in (("doc.status", state.status), ("doc.entity_type", state.entity_type), ("doc.view", state.view),
                 ("doc.app", state.app)):
        if v is not None:
            out.setdefault(k, v)
    return out


# ---------------- session memory: records seen earlier (cross-entity context) ----------------
# Generic, app-agnostic: roles are inferred from canonical var NAMES (…supplier/vendor/customer/party → party,
# …amount/total → amount, …_date → month). Lets a rule like "same amount as one already paid" become a predicate.
PRIOR_VARS: dict[str, str] = {
    "prior.count": "number of OTHER records seen earlier in this session (list rows or opened records)",
    "prior.same_supplier": "true if an earlier-seen record has the same supplier/party",
    "prior.same_amount": "true if an earlier-seen record has the same amount",
    "prior.same_supplier_amount": "true if an earlier-seen record has the same supplier/party AND the same amount",
    "prior.same_supplier_amount_in_month": "true if an earlier-seen record has the same supplier/party, the same "
                                           "amount AND a date in the same calendar month",
}
_PARTY = ("supplier", "vendor", "customer", "party", "payee", "counterparty", "client", "requester")
_NOT_PARTY = ("_no", "_id", "_number", "_date", "_month", "_year", "_ref", "_group", "_type")
_AMOUNT = ("amount", "total", "grand_total", "net_total")


def _tail(k: str) -> str:
    return k.split(".")[-1].lower()


def _party_values(v: dict[str, Any]) -> set[str]:
    out = set()
    for k, x in v.items():
        t = _tail(k)
        if isinstance(x, str) and x.strip() and any(p in t for p in _PARTY) and not t.endswith(_NOT_PARTY):
            out.add(norm_label(re.sub(r"\s*(\.\.\.|…)$", "", x)))
    return {x for x in out if x}


def _amount_values(v: dict[str, Any]) -> set[float]:
    out = set()
    for k, x in v.items():
        t = _tail(k)
        if any(t == a or t.endswith("_" + a) or t.endswith(a) for a in _AMOUNT) and not t.endswith(_NOT_PARTY):
            n = x if isinstance(x, (int, float)) and not isinstance(x, bool) else parse_number(x)
            if n:
                out.add(round(float(n), 2))
    return out


def _months(v: dict[str, Any]) -> set[tuple[int, int]]:
    out = set()
    for k, x in v.items():
        if k.endswith("_date"):
            dt = parse_date(x if isinstance(x, str) else None)
            if dt:
                out.add((dt[0], dt[1]))
    return out


_IDLIKE = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[\w./-]{5,}$")


def _ids(v: dict[str, Any], ent: Optional[str] = None) -> set[str]:
    out = {str(x).strip() for x in v.values() if isinstance(x, str) and _IDLIKE.match(x.strip()) and
           re.search(r"[-/]", x)}
    if ent:
        out.add(ent)
    return out


def _party_eq(a: set[str], b: set[str]) -> bool:
    for x in a:
        for y in b:
            if x == y or (min(len(x), len(y)) >= 8 and (x.startswith(y) or y.startswith(x))):
                return True
    return False


def entity_snapshot(vars_: dict[str, Any]) -> dict[str, Any]:
    """What to remember about a record: its canonical values (not the doc.* screen metadata)."""
    return {k: v for k, v in vars_.items() if not k.startswith(("doc.", "prior.")) and v not in (None, "")}


def rows_as_entities(state: ScreenState, wm: WorkMap) -> list[dict[str, Any]]:
    """Table rows visible on screen (list views, grids) → canonical-var snapshots, one per row."""
    exact: dict[str, list[str]] = {}
    for canon, labels in wm.canonical_vars.items():
        for lab in labels:
            exact.setdefault(norm_label(lab), []).append(canon)
    out = []
    for tb in state.tables or []:
        cols = [exact.get(norm_label(re.sub(r"\s*\*$", "", str(c))), []) for c in (tb.get("columns") or [])]
        if not any(cols):
            continue
        for row in tb.get("rows") or []:
            if not isinstance(row, list):
                continue
            snap: dict[str, Any] = {}
            for canons, cell in zip(cols, row):
                for c in canons:
                    if cell not in (None, "") and c not in snap:
                        n = parse_number(cell) if re.search(r"\d", str(cell)) and not parse_date(str(cell)) else None
                        snap[c] = n if n is not None and not re.search(r"[A-Za-z]{2,}", str(cell)) else str(cell)
            if len(snap) >= 2:
                out.append(snap)
    return out


def prior_vars(cur: dict[str, Any], seen: dict[str, dict[str, Any]], cur_id: Optional[str] = None) -> dict[str, Any]:
    """prior.* vars for the current record from records seen earlier in the session (never itself)."""
    party, amts, months, ids = _party_values(cur), _amount_values(cur), _months(cur), _ids(cur, cur_id)
    out = {k: False for k in PRIOR_VARS}
    out["prior.count"] = 0
    matches: list[str] = []
    for key, snap in seen.items():
        if key == cur_id or (ids & _ids(snap, key)):
            continue  # the same record (e.g. its own list row)
        out["prior.count"] += 1
        sp = _party_eq(party, _party_values(snap))
        sa = bool(amts & _amount_values(snap))
        sm = bool(months & _months(snap))
        out["prior.same_supplier"] |= sp
        out["prior.same_amount"] |= sa
        if sp and sa:
            out["prior.same_supplier_amount"] = True
            matches.append(key)
            if sm:
                out["prior.same_supplier_amount_in_month"] = True
    out["prior.match_ids"] = matches
    return out


def seen_summary(seen: dict[str, dict[str, Any]], cur_id: Optional[str] = None, n: int = 8) -> str:
    lines = []
    for key, snap in list(seen.items())[-n:]:
        if key == cur_id:
            continue
        party = next(iter(sorted(_party_values(snap))), None)
        amt = next(iter(sorted(_amount_values(snap))), None)
        dt = next((str(v) for k, v in snap.items() if k.endswith("_date")), None)
        lines.append(" · ".join(str(x) for x in (key, party, amt, dt) if x is not None))
    return "\n".join(lines)


def predicate_vars(p: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(p, dict):
        for k, v in p.items():
            if k == "var":
                name = v[0] if isinstance(v, list) and v else v
                if isinstance(name, str) and name:
                    out.add(name)
            else:
                out |= predicate_vars(v)
    elif isinstance(p, list):
        for x in p:
            out |= predicate_vars(x)
    return out


def _nest(flat: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in flat.items():
        cur = out
        parts = k.split(".")
        ok = True
        for p in parts[:-1]:
            nxt = cur.setdefault(p, {})
            if not isinstance(nxt, dict):
                ok = False
                break
            cur = nxt
        if ok:
            cur.setdefault(parts[-1], v)
    return out


def _jl(p: Any, data: dict[str, Any], flat: dict[str, Any]) -> Any:
    """Small json-logic evaluator (subset) used when json_logic lib is unavailable."""
    if not isinstance(p, dict) or len(p) != 1:
        return [_jl(x, data, flat) for x in p] if isinstance(p, list) else p
    op, args = next(iter(p.items()))
    if op == "var":
        name = args[0] if isinstance(args, list) else args
        default = args[1] if isinstance(args, list) and len(args) > 1 else None
        return flat.get(name, default)
    if not isinstance(args, list):
        args = [args]
    if op == "and":
        r: Any = True
        for a in args:
            r = _jl(a, data, flat)
            if not r:
                return r
        return r
    if op == "or":
        r = False
        for a in args:
            r = _jl(a, data, flat)
            if r:
                return r
        return r
    if op == "if":
        for i in range(0, len(args) - 1, 2):
            if _jl(args[i], data, flat):
                return _jl(args[i + 1], data, flat)
        return _jl(args[-1], data, flat) if len(args) % 2 else None
    v = [_jl(a, data, flat) for a in args]
    if op == "!":
        return not v[0]
    if op == "!!":
        return bool(v[0])
    if op in ("==", "==="):
        return _eq(v[0], v[1])
    if op in ("!=", "!=="):
        return not _eq(v[0], v[1])
    if op in (">", ">=", "<", "<="):
        nums = [parse_number(x) if not isinstance(x, (int, float)) else float(x) for x in v]
        if any(n is None for n in nums):
            return False
        if len(nums) == 3 and op in ("<", "<="):
            return (nums[0] < nums[1] < nums[2]) if op == "<" else (nums[0] <= nums[1] <= nums[2])
        a, b = nums[0], nums[1]
        return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]
    if op == "in":
        a, b = v[0], v[1]
        if isinstance(b, list):
            return any(_eq(a, x) for x in b)
        return isinstance(b, str) and isinstance(a, str) and a.lower() in b.lower()
    if op == "missing":
        return [x for x in v if flat.get(x) in (None, "")]
    raise ValueError(f"unsupported json-logic op {op}")


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return norm_label(a) == norm_label(b)
    na = parse_number(a) if not isinstance(a, bool) else None
    nb = parse_number(b) if not isinstance(b, bool) else None
    if na is not None and nb is not None:
        return na == nb
    return a == b


def eval_predicate(pred: dict[str, Any], vars_: dict[str, Any]) -> Optional[bool]:
    """True = predicate holds (guardrail triggered). None = not evaluable (missing vars)."""
    needed = predicate_vars(pred)
    if any(vars_.get(n) in (None, "") for n in needed):
        return None
    try:
        return bool(_jl(pred, _nest(vars_), vars_))
    except Exception:  # noqa: BLE001
        return None


# ---------------- i18n ----------------

T: dict[str, dict[str, str]] = {
    "intervene": {
        "en": "{expert} would stop here. Why do you think?",
        "de": "{expert} würde hier anhalten. Was meinst du, warum?",
        "fr": "{expert} s'arrêterait ici. À ton avis, pourquoi ?",
        "es": "{expert} se detendría aquí. ¿Por qué crees?",
        "ru": "{expert} здесь бы остановился. Как думаешь, почему?",
    },
    "intervene_submit": {
        "en": "Before you submit this: {expert} would not let it through like this. Take another look.",
        "de": "Bevor du das abschickst: So würde {expert} es nicht durchlassen. Schau noch einmal hin.",
        "fr": "Avant de valider : {expert} ne laisserait pas passer ça comme ça. Regarde encore.",
        "es": "Antes de enviarlo: {expert} no lo dejaría pasar así. Revísalo otra vez.",
        "ru": "Прежде чем сохранять: {expert} бы это так не пропустил(а). Посмотри ещё раз.",
    },
    "said": {"en": "{expert} said: “{q}”", "de": "{expert} sagte: „{q}“", "fr": "{expert} a dit : « {q} »",
             "es": "{expert} dijo: «{q}»", "ru": "{expert} сказал(а): «{q}»"},
    "not_in_map": {
        "en": "Nobody has shown me that yet, so I won't guess. Want me to ask an expert?",
        "de": "Das hat mir noch niemand gezeigt, also rate ich nicht. Soll ich eine Expertin oder einen Experten fragen?",
        "fr": "Personne ne me l'a encore montré, donc je ne devine pas. Je demande à un expert ?",
        "es": "Nadie me lo ha mostrado todavía, así que no voy a adivinar. ¿Pregunto a un experto?",
        "ru": "Мне это ещё никто не показывал, гадать не буду. Спросить эксперта?",
    },
    "predict": {
        "en": "Before you go on: what would you do here, and why?",
        "de": "Bevor du weitermachst: Was würdest du hier tun, und warum?",
        "fr": "Avant de continuer : que ferais-tu ici, et pourquoi ?",
        "es": "Antes de seguir: ¿qué harías aquí y por qué?",
        "ru": "Прежде чем продолжить: что бы ты здесь сделал(а) и почему?",
    },
    "hint_offer": {
        "en": "This is a judgment call. Want a hint?", "de": "Hier ist Urteilsvermögen gefragt. Möchtest du einen Tipp?",
        "fr": "C'est une question de jugement. Tu veux un indice ?", "es": "Aquí hay que decidir. ¿Quieres una pista?",
        "ru": "Здесь нужно решение. Подсказать?",
    },
    "step": {"en": "Step {n}: {t}", "de": "Schritt {n}: {t}", "fr": "Étape {n} : {t}", "es": "Paso {n}: {t}",
             "ru": "Шаг {n}: {t}"},
    "unconfirmed": {"en": "(not confirmed yet)", "de": "(noch nicht bestätigt)", "fr": "(pas encore confirmé)",
                    "es": "(aún sin confirmar)", "ru": "(ещё не подтверждено)"},
    "overview": {"en": "This workflow has {n} steps.", "de": "Dieser Ablauf hat {n} Schritte.",
                 "fr": "Ce processus a {n} étapes.", "es": "Este proceso tiene {n} pasos.",
                 "ru": "В этом процессе {n} шагов."},
    "conflict": {"en": "Experts differ here: {c} Ask your lead.", "de": "Hier sind sich die Experten uneinig: {c} Frag deine Leitung.",
                 "fr": "Les experts divergent ici : {c} Demande à ton responsable.",
                 "es": "Los expertos no coinciden aquí: {c} Pregunta a tu responsable.",
                 "ru": "Здесь эксперты расходятся: {c} Спроси руководителя."},
    "all_clear": {"en": "Nothing in the map flags this. Looks fine so far.",
                  "de": "Nichts in der Karte spricht dagegen. Sieht bisher gut aus.",
                  "fr": "Rien dans la carte ne le signale. Ça a l'air bon.",
                  "es": "Nada en el mapa lo marca. Por ahora bien.",
                  "ru": "По карте здесь всё в порядке."},
    "done": {"en": "That was the last step.", "de": "Das war der letzte Schritt.", "fr": "C'était la dernière étape.",
             "es": "Ese fue el último paso.", "ru": "Это был последний шаг."},
    "ok_watch": {"en": "Okay, I'll just watch.", "de": "Okay, ich schaue nur zu.", "fr": "D'accord, je regarde seulement.",
                 "es": "Vale, solo observo.", "ru": "Хорошо, просто наблюдаю."},
    "ok_stop": {"en": "Okay, stopping here.", "de": "Okay, ich höre hier auf.", "fr": "D'accord, j'arrête.",
                "es": "Vale, paro aquí.", "ru": "Хорошо, останавливаюсь."},
    "asked": {"en": "I've asked an expert. You'll be notified when they've shown me.",
              "de": "Ich habe einen Experten gefragt. Du wirst benachrichtigt.",
              "fr": "J'ai demandé à un expert. Tu seras notifié.",
              "es": "He preguntado a un experto. Te avisaré.",
              "ru": "Я спросил эксперта. Сообщу, когда он покажет."},
    "right": {"en": "Right.", "de": "Genau.", "fr": "Exact.", "es": "Exacto.", "ru": "Верно."},
    "not_quite": {"en": "Not quite.", "de": "Nicht ganz.", "fr": "Pas tout à fait.", "es": "No del todo.",
                  "ru": "Не совсем."},
    "expert": {"en": "the expert", "de": "die Expertin", "fr": "l'expert", "es": "el experto", "ru": "эксперт"},
}


def tr(key: str, lang: str, **kw: Any) -> str:
    tpl = T.get(key, {}).get(lang) or T.get(key, {}).get("en", key)
    return tpl.format(**kw)


# ---------------- compiled guardrails ----------------

ENFORCE = {"block_and_explain": "block", "warn": "warn", "stop_and_ask": "ask", "hold": "hold"}
STATE_CHANGING = ("save", "submit", "approve", "hold", "escalate", "reject")


def compile_guardrails(wm: WorkMap) -> list[dict[str, Any]]:
    """Publish-time compile: {id, trigger, scope, condition, exceptions, evidence, enforce, quote_id}.
    Runtime check is deterministic json-logic on state-changing actions (no LLM in the hot path)."""
    out = []
    for g in wm.guardrails:
        scope = [s.id for s in wm.steps if g.id in s.guardrail_ids]
        exc = [s.decision.counterfactual for s in wm.steps
               if s.id in scope and s.decision and s.decision.counterfactual]
        out.append({"id": g.id, "text": g.text,
                    "trigger": {"on": list(STATE_CHANGING), "also": "every screen state (soft)"},
                    "scope": scope, "condition": g.predicate if g.predicate else {"fuzzy": g.text},
                    "deterministic": bool(g.predicate), "exceptions": exc,
                    "evidence": [m.model_dump(mode="json") for m in g.evidence], "enforce": ENFORCE.get(g.action, "block"),
                    "owner": g.owner, "quote_id": g.quote_ids[0] if g.quote_ids else None, "experts": g.experts})
    return out
