"""Offline unit tests for claros.perception (no OCR models, mocked LLM)."""
from __future__ import annotations

import asyncio
import base64
import io

import pytest
from PIL import Image

from claros.models import Field_, ScreenState
from claros.perception import pii, pipeline as pl
from claros.perception.diff import Differ
from claros.perception.locale import guess_lang, normalize, parse_date, parse_number
from claros.perception.ocr import OcrLine, fix_homoglyphs, merge_rects
from claros.perception.redact import RedactResult, blur_boxes, mask_text
from claros.perception.state import StateTracker, VField, VisionState, analyze


# ---------------- locale ----------------

@pytest.mark.parametrize("s,lang,exp", [
    ("12.400,00 €", "de", {"value": 12400.0, "currency": "EUR"}),
    ("12 400,00 ₽", "ru", {"value": 12400.0, "currency": "RUB"}),
    ("12 400,00 ₽", "ru", {"value": 12400.0, "currency": "RUB"}),
    ("1,234.56", "en", 1234.56),
    ("$1,234.56", "en", {"value": 1234.56, "currency": "USD"}),
    ("1 234,56 €", "fr", {"value": 1234.56, "currency": "EUR"}),
    ("1.234,56", "es", 1234.56),
    ("1.234", "de", 1234.0),
    ("1.234", "en", 1.234),
    ("1,5", "de", 1.5),
    ("-42", "en", -42.0),
    ("(1,000.00)", "en", -1000.0),
    ("19 %", "de", {"value": 19.0, "unit": "%"}),
    ("EUR 5.000,00", "de", {"value": 5000.0, "currency": "EUR"}),
    ("Verwaltung", "de", None),
    ("ACC-PINV-0004", "en", None),
])
def test_normalize_numbers(s, lang, exp):
    assert normalize(s, lang) == exp


@pytest.mark.parametrize("s,lang,exp", [
    ("03.10.2026", "de", "2026-10-03"),
    ("03.10.2026", "ru", "2026-10-03"),
    ("03/10/2026", "fr", "2026-10-03"),
    ("03/10/2026", "es", "2026-10-03"),
    ("10/03/2026", "en", "2026-10-03"),
    ("25/03/2026", "en", "2026-03-25"),
    ("2026-10-03", "en", "2026-10-03"),
    ("3. Oktober 2026", "de", "2026-10-03"),
    ("3 octobre 2026", "fr", "2026-10-03"),
    ("3 de octubre de 2026", "es", "2026-10-03"),
    ("3 октября 2026 г.", "ru", "2026-10-03"),
    ("Oct 3, 2026", "en", "2026-10-03"),
])
def test_parse_date(s, lang, exp):
    assert parse_date(s, lang) == exp


def test_parse_number_rejects_text():
    assert parse_number("abc") is None
    assert parse_number("12a") is None


def test_guess_lang():
    assert guess_lang(["Счёт поставщика", "Черновик"]) == "ru"
    assert guess_lang(["Eingangsrechnung", "Kostenstelle", "Speichern", "Entwurf"]) == "de"
    assert guess_lang(["Facture fournisseur", "Enregistrer", "Brouillon"]) == "fr"
    assert guess_lang(["Factura de proveedor", "Guardar", "Borrador"]) == "es"
    assert guess_lang(["Purchase Invoice", "Save", "Draft"]) == "en"


# ---------------- OCR helpers ----------------

def test_homoglyph_repair():
    assert fix_homoglyphs("ivan.реtrоv@mаil.ru") == "ivan.petrov@mail.ru"
    assert fix_homoglyphs("ACC-PINV-o004") == "ACC-PINV-0004"
    assert fix_homoglyphs("12.4oo,00 €") == "12.400,00 €"
    assert fix_homoglyphs("Счёт поставщика") == "Счёт поставщика"
    assert fix_homoglyphs("Пpoизвoдcтвo") == "Производство"  # Latin o/p/c inside a Cyrillic word


def test_merge_rects():
    assert merge_rects([[0, 0, 10, 10], [5, 5, 10, 10], [100, 100, 5, 5]]) == [[0, 0, 15, 15], [100, 100, 5, 5]]


# ---------------- PII ----------------

def _labels(text):
    return {lab for _, _, lab in pii.regex_spans(text)}


def test_pii_regex_core():
    assert "email" in _labels("Kontakt: hans.mueller@firma.de")
    assert "phone_number" in _labels("Tel. +7 916 123-45-67")
    assert "phone_number" in _labels("Telefon 030 1234 5678")
    assert "iban" in _labels("IBAN DE89 3704 0044 0532 0130 00")
    assert "iban" not in _labels("IBAN DE00 3704 0044 0532 0130 00")  # bad checksum
    assert "credit_card_number" in _labels("Card 4111 1111 1111 1111")
    assert "credit_card_number" not in _labels("Card 4111 1111 1111 1112")
    assert "tax_id" in _labels("ИНН 7707083893")
    assert "password" in _labels("password: hunter22")


def test_pii_no_false_positive_on_amounts_and_dates():
    for s in ["12.400,00 €", "12 400,00 ₽", "1,234.56", "03.10.2026", "ACC-PINV-0004", "Kostenstelle: Verwaltung",
              "Grand Total", "Место возникновения затрат"]:
        assert _labels(s) == set(), s


def test_pii_names_multilingual():
    assert "person" in _labels("Herr Hans Müller")
    assert "person" in _labels("Mme Claire Dubois")
    assert "person" in _labels("Sra. María García")
    assert "person" in _labels("Иван Петрович Сидоров")
    assert "person" in _labels("Петров И.И.")
    assert "person" in _labels("Ansprechpartner: Klaus Weber")
    assert "person" not in _labels("ООО Канцтовары Плюс")
    assert "person" not in _labels("Счёт поставщика")


def test_mask_text_and_detect_cache(monkeypatch):
    monkeypatch.setattr(pii.GLINER, "state", "unavailable")
    pii._CACHE.clear()
    spans = pii.detect(["mail: a.b@c.de", "nothing"])
    assert spans[1] == []
    assert mask_text("mail: a.b@c.de", spans[0]) == "mail: [EMAIL]"
    assert "mail: a.b@c.de" in pii._CACHE


def test_gliner_spans_mapping(monkeypatch):
    class FakeModel:
        def extract_entities(self, text, labels, **kw):
            i = text.index("Hans")
            return {"entities": {"person": [{"text": "Hans Müller", "start": i, "end": i + 11, "confidence": 0.9}]}}
    monkeypatch.setattr(pii.GLINER, "model", FakeModel())
    monkeypatch.setattr(pii.GLINER, "state", "ready")
    pii._CACHE.clear()
    out = pii.detect(["Lieferant", "Kontakt Hans Müller"])
    assert out[0] == []
    assert (8, 19, "person") in out[1]


def test_blur_changes_only_boxes():
    im = Image.new("RGB", (200, 60), "white")
    from PIL import ImageDraw
    ImageDraw.Draw(im).text((10, 10), "secret@x.de", fill="black")
    ImageDraw.Draw(im).text((120, 10), "keep", fill="black")
    out = blur_boxes(im, [[5, 5, 100, 30]])
    assert out.crop((5, 5, 105, 35)).tobytes() != im.crop((5, 5, 105, 35)).tobytes()
    assert out.crop((115, 5, 190, 35)).tobytes() == im.crop((115, 5, 190, 35)).tobytes()


# ---------------- state + diff (synthetic OCR lines) ----------------

def L(text, x, y, w=None, h=24, conf=0.98):
    return OcrLine(text, [x, y, w or len(text) * 8, h], conf, redacted=True)


def form(lang="en", cost="Administration", status="Draft", toast=None, dialog=None, title_id="ACC-PINV-0004",
         typed_extra=None):
    labels = {"en": ("Purchase Invoice", "Supplier", "Cost Center", "Grand Total"),
              "de": ("Eingangsrechnung", "Lieferant", "Kostenstelle", "Gesamtbetrag"),
              "ru": ("Счёт поставщика", "Поставщик", "Место возникновения затрат", "Итого")}[lang]
    lines = [L("Acme ERP", 20, 10), L(f"{labels[0]} {title_id}", 30, 90, w=len(labels[0] + title_id) * 15, h=32),
             L(status, 920, 96), L(labels[1], 50, 176), L("Global Office Supply Ltd", 60, 208),
             L(labels[2], 50, 286), L(cost, 60, 318), L(labels[3], 650, 286), L(typed_extra or "12.400,00 €", 660, 318)]
    if toast:
        lines.append(L(toast, 1000, 720))
    if dialog:
        lines += [L(dialog, 400, 340), L("Cancel", 690, 434), L("Yes", 810, 434)]
    return lines


DIMS = (1280, 800)


def test_analyze_heuristics():
    h = analyze(form("de", status="Entwurf"), DIMS)
    assert h.entity_id == "ACC-PINV-0004"
    assert h.entity_type == "Eingangsrechnung"
    assert h.status == "Entwurf"
    pairs = {p.label: p.value for p in h.pairs}
    assert pairs["Kostenstelle"] == "Administration"
    assert pairs["Gesamtbetrag"] == "12.400,00 €"
    assert h.ui_lang == "de"
    h2 = analyze(form(dialog="Submit this document permanently?"), DIMS)
    assert h2.dialogs == ["Submit this document permanently?"]


def run_seq(frames, activities=()):
    tr, df = StateTracker(), Differ("s")
    for a in activities:
        df.note_activity(a)
    evs, prev = [], None
    for i, (t, lines) in enumerate(frames):
        st = tr.update(i, t, lines, DIMS)
        evs += df.diff(prev, st)
        prev = st
    return evs, tr


def test_events_open_edit_save_submit_dialog():
    frames = [(0, form()), (2000, form(cost="Production")), (3000, form(cost="Production", toast="Saved")),
              (5000, form(cost="Production", dialog="Submit this document permanently?")),
              (7000, form(cost="Production", status="Submitted"))]
    evs, _ = run_seq(frames, [{"t": 1500, "kind": "typing", "tiles_changed": 3}])
    kinds = [e.kind for e in evs]
    assert kinds == ["open", "edit", "save", "dialog", "submit"]
    edit = evs[1]
    assert (edit.field, edit.old, edit.new, edit.source) == ("Cost Center", "Administration", "Production", "typed")
    assert edit.entity_id == "ACC-PINV-0004"


@pytest.mark.parametrize("toast", ["Saved", "Gespeichert", "Enregistré", "Guardado", "Сохранено"])
def test_save_toast_multilingual(toast):
    evs, _ = run_seq([(0, form()), (1000, form(toast=toast))])
    assert [e.kind for e in evs] == ["open", "save"]


@pytest.mark.parametrize("lang,st0,st1", [("de", "Entwurf", "Gebucht"), ("ru", "Черновик", "Проведён"),
                                          ("en", "Draft", "Submitted")])
def test_status_submit_multilingual(lang, st0, st1):
    evs, _ = run_seq([(0, form(lang, status=st0)), (1000, form(lang, status=st1))])
    assert evs[-1].kind == "submit"


def test_undo_within_20s_and_not_after():
    evs, _ = run_seq([(0, form()), (1000, form(cost="Production")), (5000, form(cost="Administration"))])
    assert [e.kind for e in evs] == ["open", "edit", "undo"]
    evs, _ = run_seq([(0, form()), (1000, form(cost="Production")), (40000, form(cost="Administration"))])
    assert [e.kind for e in evs] == ["open", "edit", "edit"]


def test_value_sources():
    # no activity at all -> system
    evs, _ = run_seq([(0, form()), (2000, form(cost="Production"))])
    assert evs[-1].source == "system"
    # user activity but no typing -> pasted
    evs, _ = run_seq([(0, form()), (2000, form(cost="Production"))], [{"t": 1800, "kind": "navigating"}])
    assert evs[-1].source == "pasted"
    # typing with tiles over the cost-center field, total changes too -> typed + system(autofill)
    evs, _ = run_seq([(0, form()), (2000, form(cost="Production", typed_extra="13.000,00 €"))],
                     [{"t": 1700, "kind": "typing", "tiles_changed": [[40, 300, 300, 60]]}])
    src = {e.field: e.source for e in evs if e.kind == "edit"}
    assert src == {"Cost Center": "typed", "Grand Total": "system"}


def test_accumulated_fields_survive_scroll():
    tr = StateTracker()
    tr.update(0, 0, form(), DIMS)
    scrolled = [ln for ln in form() if ln.text not in ("Grand Total", "12.400,00 €")]
    st = tr.update(1, 1000, scrolled, DIMS)
    gt = [f for f in st.fields if f.label == "Grand Total"][0]
    assert gt.value == "12.400,00 €" and gt.bbox is None
    assert gt.normalized == {"value": 12400.0, "currency": "EUR"}


def test_router():
    tr = StateTracker()
    assert tr.route(form(), DIMS)[0] is True          # first frame
    tr.update(0, 0, form(), DIMS)
    tr.apply_vision(0, VisionState(entity_id="ACC-PINV-0004", entity_type="Purchase Invoice"), form())
    s, why = tr.route(form(cost="Production"), DIMS)
    assert (s, why) == (False, "cheap")
    assert tr.route(form(title_id="ACC-PINV-0005"), DIMS)[0] is True
    assert tr.route(form(dialog="Delete this?"), DIMS)[1] == "dialog"
    assert tr.route([OcrLine(x.text, x.bbox, 0.3) for x in form()], DIMS)[1] == "low_conf"


def test_vision_template_and_out_of_order():
    tr = StateTracker()
    lines0 = form() + [L("Due Date", 650, 176), L("31.10.2026", 660, 208)]
    tr.update(0, 0, lines0, DIMS)
    vs = VisionState(app="Acme ERP", view="Purchase Invoice form", entity_type="Purchase Invoice",
                     entity_id="ACC-PINV-0004", status="Draft", ui_lang="en",
                     fields=[VField(label="Cost Center", value="Administration", bbox=[56, 314, 520, 40], kind="select")])
    tr.update(1, 1000, form(cost="Production") + [L("Due Date", 650, 176), L("31.10.2026", 660, 208)], DIMS)
    st = tr.apply_vision(0, vs, lines0)
    assert st is not None and st.app == "Acme ERP" and st.seq == 1
    cc = [f for f in st.fields if f.label == "Cost Center"][0]
    assert cc.value == "Production"  # newer OCR value wins over the older vision frame
    assert tr.kinds["id:ACC-PINV-0004"]["cost center"] == "select"
    assert tr.apply_vision(0, vs, lines0) is None  # stale/duplicate seq dropped


# ---------------- pipeline (mocked OCR + LLM) ----------------

class Bus:
    def __init__(self):
        self.msgs = []

    def __call__(self, sid, topic, payload):
        self.msgs.append((topic, payload))

    def of(self, topic, typ=None):
        return [p for t, p in self.msgs if t == topic and (typ is None or p.get("type") == typ)]


def _jpeg():
    b = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(b, "JPEG")
    return base64.b64encode(b.getvalue()).decode()


@pytest.fixture
def scripted(monkeypatch, tmp_path):
    """Replace redact_frame with a script of OCR line lists; redirect keyframe storage to tmp."""
    script: list = []

    def fake_redact(jpeg, tiles=None, prev=None):
        lines = script.pop(0)
        return RedactResult(lines=lines, jpeg=b"REDACTED", dims=DIMS)

    monkeypatch.setattr(pl, "redact_frame", fake_redact)
    import claros.perception.redact as rd
    monkeypatch.setattr(rd, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(pl, "_session", lambda sid: None)
    return script


async def test_pipeline_publishes_events_and_context(scripted):
    bus = Bus()
    calls = []

    async def vision(msgs, jpeg):
        calls.append(jpeg)
        assert jpeg == b"REDACTED"
        assert "OCR lines" in msgs[-1]["content"]
        return VisionState(app="Acme ERP", entity_type="Purchase Invoice", entity_id="ACC-PINV-0004", status="Draft")

    p = pl.SessionPipeline("s1", bus, vision=vision, persist=False)
    scripted += [form(), form(cost="Production")]
    p.on_activity({"t": 900, "kind": "typing", "tiles_changed": 2})
    await p.on_keyframe({"t": 0, "seq": 0, "reason": "boundary", "jpeg_b64": _jpeg()})
    await p.on_keyframe({"t": 1000, "seq": 1, "reason": "settle", "jpeg_b64": _jpeg()})
    await p.drain()
    evs = [e for batch in bus.of("screen.events") for e in batch]
    assert [e.kind for e in evs] == ["open", "edit"]
    out = bus.of("ws.out", "events")
    assert out and out[0]["items"][0]["kind"] == "open"
    ctx = bus.of("ws.out", "context_update")
    assert ctx and ctx[0]["text"].startswith("Screen: Purchase Invoice ACC-PINV-0004 Draft")
    assert len(calls) == 1  # second frame was a cheap update
    states = bus.of("screen.state")
    assert isinstance(states[-1], ScreenState) and states[-1].app == "Acme ERP"
    p.close()


async def test_context_update_throttled(scripted):
    bus = Bus()

    async def novision(m, j):
        return None

    p = pl.SessionPipeline("s1", bus, vision=novision, persist=False)
    scripted += [form(), form(cost="A1"), form(cost="B2"), form(cost="C3")]
    for i in range(4):
        await p.on_keyframe({"t": i * 100, "seq": i, "jpeg_b64": _jpeg()})
    assert len(bus.of("ws.out", "context_update")) == 1
    assert p.ctx_handle is not None  # a deferred flush is scheduled
    p.ctx_last_sent -= 10
    p._flush_context()
    texts = [m["text"] for m in bus.of("ws.out", "context_update")]
    assert len(texts) == 2 and "C3" in texts[-1]
    p.close()


async def test_off_record_drops_frames(scripted, monkeypatch):
    bus = Bus()

    class S:
        off_record = True

    monkeypatch.setattr(pl, "_session", lambda sid: S())
    p = pl.SessionPipeline("s1", bus, persist=True)
    scripted += [form()]
    assert await p.on_keyframe({"t": 0, "seq": 0, "jpeg_b64": _jpeg()}) is None
    assert bus.msgs == [] and scripted  # redaction never ran
    p.close()


async def test_persist_writes_redacted_only(scripted, tmp_path, monkeypatch):
    bus = Bus()
    stored = {}
    import claros.perception.redact as rd

    class FakeStore:
        def put_keyframe(self, id, sid, path, t, meta):
            stored[id] = (path, meta)

        def log(self, *a, **k):
            pass

    import claros.store
    monkeypatch.setattr(claros.store, "store", FakeStore())

    async def novision(m, j):
        return None

    p = pl.SessionPipeline("sess", bus, vision=novision, persist=True)
    scripted += [form()]
    st = await p.on_keyframe({"t": 0, "seq": 7, "jpeg_b64": _jpeg()})
    path = tmp_path / "data" / "keyframes" / "sess" / "7.jpg"
    assert path.read_bytes() == b"REDACTED"
    assert st.keyframe_id == "sess_7" and stored["sess_7"][1]["redacted"] is True
    assert rd.keyframe_path("sess", 7) == path
    p.close()


async def test_vision_cap_and_latest_wins(scripted):
    bus = Bus()
    seen = []
    gate = asyncio.Event()

    async def vision(msgs, jpeg):
        seen.append(len(seen))
        await gate.wait()
        return None

    p = pl.SessionPipeline("s1", bus, vision=vision, persist=False, max_vision_per_min=2)
    # every frame changes title -> structural; first is "must", later "title" are must too, so use settle
    # frames with big line churn (droppable) to exercise latest-wins
    churn = [form()] + [[L(f"row {i} {j}", 10, 400 + 30 * j) for j in range(10)] + form() for i in range(5)]
    scripted += churn
    for i in range(6):
        await p.on_keyframe({"t": i * 100, "seq": i, "reason": "settle", "jpeg_b64": _jpeg()})
    await asyncio.sleep(0.05)
    # first call in flight (seq 0); of the 5 droppable churn frames only the newest is still pending
    assert len(seen) == 1
    assert [j.seq for j in p.pending] == [5]
    gate.set()
    await asyncio.sleep(0.05)
    assert len(seen) == 2
    # cap reached: a further must-keep frame waits instead of calling
    scripted += [form(title_id="X-PINV-9999")]
    await p.on_keyframe({"t": 900, "seq": 6, "reason": "boundary", "jpeg_b64": _jpeg()})
    await asyncio.sleep(0.05)
    assert len(seen) == 2 and [j.seq for j in p.pending] == [6]
    p.close()


async def test_out_of_order_keyframe_not_applied(scripted):
    bus = Bus()

    async def novision(m, j):
        return None

    p = pl.SessionPipeline("s1", bus, vision=novision, persist=False)
    scripted += [form(), form(cost="Late")]
    await p.on_keyframe({"t": 1000, "seq": 5, "jpeg_b64": _jpeg()})
    assert await p.on_keyframe({"t": 500, "seq": 4, "jpeg_b64": _jpeg()}) is None
    assert p.published.seq == 5
    p.close()


async def test_register_wires_bus(monkeypatch):
    import claros.perception as perception

    class FakeBus:
        def __init__(self):
            self.subs = {}

        def subscribe(self, topic, h):
            self.subs[topic] = h

    monkeypatch.setattr(pii.GLINER, "load_async", lambda: None)
    monkeypatch.setattr(perception, "_warm", lambda: None)
    b = FakeBus()
    perception.register(b)
    assert {"ws.in.keyframe", "ws.in.activity", "session.ended"} <= set(b.subs)


def test_field_model_roundtrip():
    f = Field_(label="Kostenstelle", value="Verwaltung", bbox=[1, 2, 3, 4])
    assert ScreenState(seq=1, t=0, fields=[f]).model_dump()["fields"][0]["label"] == "Kostenstelle"
