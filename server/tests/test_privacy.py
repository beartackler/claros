"""Off the record + strike that: tombstones, redaction, and the map builder / debrief ignore struck intervals."""
from __future__ import annotations

import time

import pytest

from claros import privacy
from claros.brain import lang as L
from claros.knowledge import _deps as d
from claros.knowledge import builder
import claros.store as store_mod
from claros.store import Store

store: Store = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def fresh_store(tmp_path, monkeypatch):
    global store
    store = Store(str(tmp_path / "p.db"))
    monkeypatch.setattr(store_mod, "store", store)
    monkeypatch.setattr(d, "STORE", store)
    yield


def _age(row_id: int, seconds: float) -> None:
    store.execute("UPDATE log SET ts=? WHERE id=?", (time.time() - seconds, row_id))


def _utt(sid: str, text: str, age_s: float, eid: str) -> int:
    rid = store.log(sid, "ws.in.utterance", {"t_start": 1.0, "role": "user", "text": text, "event_id": eid})
    _age(rid, age_s)
    return rid


def _ev(sid: str, eid: str, age_s: float, kf: str) -> int:
    rid = store.log(sid, "screen.events", {"items": [{"id": eid, "seq": 1, "t": 1.0, "kind": "edit", "field": "Amount",
                                                      "old": "1", "new": "2", "keyframe_id": kf}]})
    _age(rid, age_s)
    return rid


def test_strike_tombstones_last_30s_and_builder_ignores_it():
    sid = "s_priv"
    _utt(sid, "Equipment over 5,000 is capex.", 120, "u_old")
    _ev(sid, "ev_old", 110, "kf_old")
    _utt(sid, "My colleague Jan earns 4,200 a month.", 10, "u_new")
    _ev(sid, "ev_new", 8, "kf_new")
    store.put_keyframe("kf_new", sid, "/nonexistent.jpg", 1.0, {})
    out = privacy.strike(sid)
    assert out["rows"] == 2 and "ev_new" in out["events"] and "u_new" in out["utterances"]
    assert store.get_keyframe("kf_new") is None
    r = builder.replay(sid)
    texts = [u["text"] for u in r.utterances]
    assert texts == ["Equipment over 5,000 is capex."]
    assert [e.id for e in r.events] == ["ev_old"]
    assert "4,200" not in str(list(store.iter_log(sid)))


def test_strike_control_row_does_not_pop_an_older_utterance():
    sid = "s_priv2"
    _utt(sid, "Hold it until the supplier confirms.", 90, "u1")
    _utt(sid, "Wrong thing I said.", 5, "u2")
    privacy.strike(sid)
    store.log(sid, "ws.in.control", {"action": "strike_that"})
    r = builder.replay(sid)
    assert [u["text"] for u in r.utterances] == ["Hold it until the supplier confirms."]


def test_off_record_redacts_request_and_preceding_sentence_only():
    sid = "s_priv3"
    _utt(sid, "This supplier double-bills every December.", 60, "u1")
    _utt(sid, "My salary is 5,100.", 3, "u2")
    _utt(sid, "Let's go off the record.", 0.5, "u3")
    assert privacy.redact_before_off_record(sid) == 2
    store.log(sid, "ws.in.control", {"action": "off_record_on"})
    store.log(sid, "ws.in.utterance", {"t_start": 2.0, "role": "user", "text": "[off the record]", "event_id": "u4"})
    store.log(sid, "ws.in.control", {"action": "off_record_off"})
    _utt(sid, "Back to the invoices.", 0, "u5")
    r = builder.replay(sid)
    assert [u["text"] for u in r.utterances] == ["This supplier double-bills every December.", "Back to the invoices."]
    assert "5,100" not in str(list(store.iter_log(sid)))


def test_strike_drops_live_ledger_items_from_the_window():
    from claros.brain.ledger import get_ledger
    from claros.models import ScreenEvent, Unknown
    sid = "s_priv4"
    lg = get_ledger(sid)
    now = time.time() * 1000
    lg.events = [ScreenEvent(id="ev_new", seq=1, t=1.0, kind="edit")]
    lg.unknowns["u_new"] = Unknown(id="u_new", type="why", created_t=now - 5_000, about_event_ids=["ev_new"])
    lg.unknowns["u_old"] = Unknown(id="u_old", type="why", created_t=now - 120_000, status="answered",
                                   resolution="because", resolution_source="expert", answer_utterance_ids=["u_x"])
    _ev(sid, "ev_new", 5, "kf_x")
    _utt(sid, "because", 4, "u_x")
    privacy.strike(sid)
    assert lg.unknowns["u_new"].status == "dropped" and not lg.events
    assert lg.unknowns["u_old"].status == "open"  # its answer was struck → the question comes back


def test_off_record_phrases_say_resume_in_every_language():
    for lg in ("en", "de", "fr", "es", "ru"):
        assert L.phrase("off_record", lg) != L.phrase("off_record", "en") or lg == "en"
    assert "Tap Resume" in L.phrase("off_record", "en")
    assert L.phrase("on_record", "en") == "Back on the record."


async def test_debrief_strike_removes_recent_answer_and_reopens_question(monkeypatch):
    from claros.knowledge import debrief
    from claros.models import Guardrail, Quote, Step, Unknown, WorkMap

    class Bus:
        def publish(self, *a, **k):
            pass
    d.BUS = Bus()
    now = time.time() * 1000
    q_old = Quote(id="q_old", speaker="E", speaker_id="e", lang="en", text="Over 5,000 is capex.", t=now - 300_000,
                  session_id="s_db", source="live")
    q_new = Quote(id="q_new", speaker="E", speaker_id="e", lang="en", text="Only for Jan's team.", t=now - 3_000,
                  session_id="s_db", source="debrief")
    wm = WorkMap(id="wm", workflow_id="wf_db", name="AP", steps=[Step(id="s1", order=1, title="Code", guardrail_ids=["g1", "g2"])],
                 quotes=[q_old, q_new],
                 guardrails=[Guardrail(id="g1", text="Over 5,000 is capex", quote_ids=["q_old", "q_new"]),
                             Guardrail(id="g2", text="Only Jan's team", quote_ids=["q_new"])],
                 open_unknowns=[Unknown(id="u1", type="limit", status="answered", answer_utterance_ids=["q_new"],
                                        resolution="Only for Jan's team.")])
    store.put_workflow(wm)
    debrief.start("s_db", "wf_db")
    assert await debrief.strike_recent("s_db") == 1
    after = debrief.load_map("wf_db")
    assert [g.id for g in after.guardrails] == ["g1"] and after.guardrails[0].quote_ids == ["q_old"]
    assert after.steps[0].guardrail_ids == ["g1"] and after.open_unknowns[0].status == "open"
    assert "Jan" not in str([q.text for q in after.quotes])
