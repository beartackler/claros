"""Integration: real RapidOCR on synthetic en/de/ru form screenshots (skipped if rapidocr v3 missing).
LLM is mocked out (heuristics only); GLiNER2 disabled for determinism (regex PII layer)."""
from __future__ import annotations

import asyncio
import base64

import pytest

rapidocr = pytest.importorskip("rapidocr")

from claros.perception import pii, pipeline as pl  # noqa: E402
from claros.perception.redact import redact_frame  # noqa: E402
from claros.perception.synth import VALUES, sequence, to_jpeg  # noqa: E402


@pytest.fixture(autouse=True)
def _no_gliner(monkeypatch):
    monkeypatch.setattr(pii.GLINER, "state", "unavailable")
    monkeypatch.setattr(pl, "_session", lambda sid: None)


@pytest.mark.parametrize("lang,cc_old,cc_new,status_new", [
    ("en", "Administration", "Production", "Submitted"),
    ("de", "Verwaltung", "Produktion", "Gebucht"),
    ("ru", "Администрация", "Производство", "Проведён"),
])
def test_pipeline_on_synthetic_frames(lang, cc_old, cc_new, status_new):
    events, states = [], []

    def publish(sid, topic, payload):
        if topic == "screen.events":
            events.extend(payload)
        elif topic == "screen.state":
            states.append(payload)

    async def novision(m, j):
        return None

    async def go():
        p = pl.SessionPipeline("it", publish, vision=novision, persist=False)
        p.on_activity({"t": 1200, "kind": "typing", "tiles_changed": []})
        for i, (_, im) in enumerate(sequence(lang)):
            await p.on_keyframe({"t": i * 1500, "seq": i, "jpeg_b64": base64.b64encode(to_jpeg(im)).decode()})
        p.close()
        return p

    p = asyncio.run(go())
    kinds = [e.kind for e in events]
    assert kinds == ["open", "edit", "save", "dialog", "submit"], [e.summary for e in events]
    edit = events[1]
    assert (edit.old, edit.new, edit.source) == (cc_old, cc_new, "typed")
    assert events[0].entity_id == "ACC-PINV-0004"
    assert events[-1].new == status_new
    assert states[0].ui_lang == lang
    assert max(t["ocr_ms"] for t in p.timings) < 5000


@pytest.mark.parametrize("lang", ["en", "de", "ru"])
def test_redaction_removes_pii_from_image(lang):
    _, im = sequence(lang)[0]
    res = redact_frame(to_jpeg(im))
    email = VALUES[lang][5]
    assert any(ln.text == "[EMAIL]" for ln in res.lines)
    assert all(email not in ln.text for ln in res.lines)
    assert "person" in res.pii_labels and "email" in res.pii_labels
    # OCR the redacted output: the e-mail must be unreadable
    again = redact_frame(res.jpeg)
    raw_texts = " ".join(ln.text for ln in again.lines)
    assert email.split("@")[0] not in raw_texts


def test_tile_ocr_only_reads_changed_region():
    seq = sequence("de")
    first = redact_frame(to_jpeg(seq[0][1]))
    second = redact_frame(to_jpeg(seq[1][1]), changed_tiles=[[56, 310, 256, 50]], prev_lines=first.lines)
    assert second.regions is not None
    texts = [ln.text for ln in second.lines]
    assert "Produktion" in texts and "Verwaltung" not in texts
    assert "Lieferant" in texts  # carried over from the previous frame
