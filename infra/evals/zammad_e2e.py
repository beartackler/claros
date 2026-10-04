"""Phase 2 — generalization: the full capture → map → learn loop on Zammad (tier-1 → tier-2 refund escalation).

Same harness pattern as infra/e2e/run_e2e.py (real Zammad UI via Playwright, real models, simulated voice),
NO Zammad-specific code in Claros. Runs against the private eval server (CLAROS_EVAL_API, default :8789).

    cd server && .venv/bin/python ../infra/evals/zammad_e2e.py A [--tag before]   # expert capture (fresh tickets)
    cd server && .venv/bin/python ../infra/evals/zammad_e2e.py B [--tag before]   # end → map build → debrief
    cd server && .venv/bin/python ../infra/evals/zammad_e2e.py C [--tag before]   # learner EN (held-out tickets)
    cd server && .venv/bin/python ../infra/evals/zammad_e2e.py RU [--tag before]  # learner with Russian UI + speech

Writes data/evals/zammad/<tag>/<phase>/{messages.jsonl, server_log.jsonl, summary.json, frames/} and
data/evals/zammad/<tag>/state.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402
from apps import (ZAM, ZamApi, zam_close_tour, zam_login, zam_open_overview, zam_open_ticket,  # noqa: E402
                  zam_select, zam_set_group, zam_settle, zam_type_note, zam_update)
from common import API, EVAL_DATA, log  # noqa: E402

from claros_client import ClarosClient, now_ms  # noqa: E402
from run_e2e import Driver  # noqa: E402

EXPERT = {"id": "expert@helpdesk.example", "name": "Elena Tier2", "role": "expert"}
LEARNER = {"id": "learner@helpdesk.example", "name": "Luca Tier1", "role": "learner"}

ANSWERS = {
    "limit": "Tier one can refund on its own only up to fifty euros, within fourteen days of the charge, and only if "
             "it's the customer's first refund in twelve months. Everything else goes to tier two.",
    "note": "The internal note has to carry the order id, the amount, the days since the charge and any prior "
            "refunds, so tier two never has to ask again.",
    "chargeback": "A chargeback or bank dispute goes to tier two immediately; we never answer it on the substance.",
}
KEYS = {
    "limit": r"refund|fifty|\b50\b|limit|amount|euro|\beur\b|tier ?(one|1)|approv|myself|clos|days|\b14\b|over|under|"
             r"240|nineteen|\b19\b|group|tier ?(two|2)|escalat",
    "note": r"\bnote\b|internal|order|write|comment|document|text",
    "chargeback": r"chargeback|rückbuchung|bank|dispute|legal|german|weber|immediately|straight|priority|high",
}
DEBRIEF_KB = [
    (r"chargeback|bank|dispute|legal|lawyer|rückbuch", ANSWERS["chargeback"]),
    (r"prior|previous|before|second refund|repeat|twelve|12 months|already (got|had)",
     "If they already got a refund in the last twelve months it goes to tier two, even for small amounts."),
    (r"\b1[3-6]\b|days|older|week|fourteen", "Fourteen days from the charge is the cut-off; day fifteen goes to tier two."),
    (r"\b4\d\b|\b5\d\b|fifty|limit|amount|under|below|over|euro|eur\b",
     "Up to fifty euros tier one can refund; fifty-one euros already goes to tier two."),
    (r"note|internal|order", ANSWERS["note"]),
    (r"priority|high|urgent", "Escalations to tier two always get priority high."),
]


def pick_answer(text: str, phase: str) -> str:
    low = text.lower()
    scores = {k: len(re.findall(p, low)) for k, p in KEYS.items()}
    best = max(scores, key=lambda k: (scores[k], k == phase))
    if scores[best] == 0:
        best = phase
    return ANSWERS[best]


def truth(variant: str) -> Optional[bool]:
    """Exam ground truth from the written refund policy: must tier 1 stop / escalate?"""
    v = variant.lower()
    if re.search(r"chargeback|dispute|bank|legal|lawyer|rückbuch", v):
        return True
    if re.search(r"prior refund|previous refund|second refund|already (got|had|received) a refund|refunded (before|in)", v):
        return True
    days = [int(x) for x in re.findall(r"(\d+)\s*days?", v)]
    if days and max(days) > 14:
        return True
    eur = [float(x.replace(",", ".")) for x in re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:eur|€|euro)", v)]
    eur += [float(x.replace(",", ".")) for x in re.findall(r"(?:eur|€)\s*(\d+(?:[.,]\d+)?)", v)]
    if eur:
        return max(eur) > 50
    return None


def debrief_reply(text: str) -> str:
    low = text.lower()
    m = re.search(r"case \d+:(.*?)i would:(.*?)(\(\d+% sure\))", low, re.S)
    if m:
        want = truth(m.group(1))
        stops = bool(re.search(r"escalat|tier ?(2|two)|stop|ask|block|hold|hand", m.group(2)))
        if want is None or want == stops:
            return "Yes, correct."
        return ("No, that one must go to tier two — it's outside the tier one limits." if want else
                "No, that's fine for tier one to refund directly.")
    if re.search(r"is (that|this) right\??\s*$|did i get (it|that) right|confirm", low) and len(low.split()) > 40:
        return "Yes, that's right."
    for pat, ans in DEBRIEF_KB:
        if re.search(pat, low):
            return ans
    return "Yes, that's how I do it."


# ---------------- state / dump ----------------

def out_dir(tag: str) -> Path:
    d = EVAL_DATA / "zammad" / tag
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state(tag: str) -> dict:
    p = out_dir(tag) / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(tag: str, **kw: Any) -> dict:
    st = load_state(tag) | kw
    (out_dir(tag) / "state.json").write_text(json.dumps(st, indent=2))
    return st


async def api(method: str, path: str, **kw: Any) -> Any:
    async with httpx.AsyncClient(timeout=300) as h:
        r = await h.request(method, f"{API}{path}", **kw)
        r.raise_for_status()
        return r.json() if r.content else None


async def dump(tag: str, phase: str, c: ClarosClient, extra: Optional[dict] = None,
               sessions: Optional[list[str]] = None) -> dict:
    d = out_dir(tag) / phase
    (d / "frames").mkdir(parents=True, exist_ok=True)
    with open(d / "messages.jsonl", "w") as f:
        for m in c.sent:
            f.write(json.dumps({"dir": "c2s", **m}, ensure_ascii=False) + "\n")
        for m in c.received:
            f.write(json.dumps({"dir": "s2c", **m}, ensure_ascii=False) + "\n")
    logs = []
    for sid in sessions or [c.session_id]:
        logs += await api("GET", f"/api/sessions/{sid}/log")
    with open(d / "server_log.jsonl", "w") as f:
        for e in logs:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    async with httpx.AsyncClient(timeout=30) as h:
        for fr in c.frames:
            r = await h.get(f"{API}/api/keyframes/{c.session_id}_{fr['seq']}.jpg")
            if r.status_code == 200:
                (d / "frames" / f"{fr['seq']:03d}_{fr['reason']}.jpg").write_bytes(r.content)
    summary = {"session_id": c.session_id, "frames": c.frames, "turns": [t.__dict__ for t in c.turns],
               **(extra or {})}
    (d / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return summary


async def wait_asks(answered: list, n: int, max_s: float) -> None:
    end = time.monotonic() + max_s
    while time.monotonic() < end and len(answered) < n:
        await asyncio.sleep(0.5)


async def new_browser(p: Any, user: str, locale: str = "en-us") -> tuple[Any, Any]:
    ZamApi().set_prefs(user, locale=locale, intro=True, theme="light")
    b = await p.chromium.launch()
    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = await ctx.new_page()
    await zam_login(page, user)
    return b, page


# ---------------- Phase A ----------------

CAPTURE_TICKETS = [
    ("Charged twice for annual plan", "maya.okafor@customer.example",
     "Hi, my card was charged 240 EUR twice for the annual Pro plan two days ago (order LUM-58213). "
     "Please refund the duplicate. Thanks, Maya"),
    ("Refund for add-on I never used", "daniel.brooks@customer.example",
     "Hello, I bought the Export add-on (19 EUR, order LUM-58877) six days ago by mistake and never used it. "
     "Could I get my money back? Daniel"),
    ("Rückbuchung bei meiner Bank eingeleitet", "jonas.weber@customer.example",
     "Guten Tag, da auf meine E-Mails niemand reagiert, habe ich bei meiner Bank eine Rückbuchung über 59 EUR "
     "(Bestellung LUM-57301) veranlasst. Jonas Weber"),
]


async def phase_a(tag: str) -> None:
    from playwright.async_api import async_playwright
    z = ZamApi()
    tids = [z.create_ticket(t, c, b)["id"] for t, c, b in CAPTURE_TICKETS]
    log("tickets", tids)
    c = await ClarosClient.create("capture", EXPERT, "en")
    log("capture session", c.session_id)
    save_state(tag, capture_session=c.session_id, capture_tickets=tids)
    phase = {"cur": "limit"}
    answered: list[dict] = []

    async def on_ask(m: dict) -> None:
        if m.get("unknown_id") == "phase-debrief":
            return
        await asyncio.sleep(1.2)
        ans = pick_answer(m.get("text") or "", phase["cur"])
        log(f"  ASK {m['unknown_id']}: {m.get('_spoken')!r} → {ans[:50]!r}")
        turn = await c._say(ans)
        answered.append({"ask": m, "answer": ans, "reply": turn.__dict__ if turn else None, "at": now_ms()})

    c.on_ask = on_ask
    narr: list[dict] = []

    async def narrate(text: str) -> None:
        turn = await c.say(text)
        narr.append({"text": text, "tool": turn.tool if turn else None, "reply": turn.text if turn else None})
        log(f"  SAY {text!r} → tool={turn.tool if turn else None} {(turn.text or '')[:60] if turn else ''!r}")

    t_start = time.monotonic()
    async with async_playwright() as p:
        b, page = await new_browser(p, EXPERT["id"])
        D = Driver(page, c)
        # 1. 240 EUR double charge → escalate
        log("A1 240 EUR → tier 2")
        await D.step("overview", zam_open_overview(page, "all_unassigned"), "boundary")
        await D.step("open 240 EUR", zam_open_ticket(page, tids[0]), "boundary", idle_s=2)
        await narrate("Maya was charged twice, two hundred forty euros for the annual plan.")
        await asyncio.sleep(2)
        await D.step("internal note", zam_type_note(page, "Order LUM-58213, 240 EUR, charged 2 days ago, no prior "
                                                          "refunds. Over Tier 1 limit, escalating.", on_typing=D.typing))
        await narrate("That's way over fifty, so I don't promise anything, I hand it to tier two.")
        await D.step("group → Tier 2", zam_set_group(page, "Tier 2 Support"))
        await D.step("priority → high", zam_select(page, "priority_id", "3 high"))
        await D.step("update", zam_update(page), "boundary", idle_s=0.5)
        await D.toast_frame("updated")
        await wait_asks(answered, 1, 45)
        # 2. 19 EUR add-on → tier 1 refunds and closes
        log("A2 19 EUR → tier 1 closes")
        phase["cur"] = "note"
        await D.step("open 19 EUR", zam_open_ticket(page, tids[1]), "boundary", idle_s=2)
        await narrate("Nineteen euros, six days ago, first time he asks. I can just do that one myself.")
        await D.step("internal note", zam_type_note(page, "Refund 19 EUR approved by Tier 1 (6 days since charge, "
                                                          "first refund). Order LUM-58877.", on_typing=D.typing))
        await D.step("state → closed", zam_select(page, "state_id", "closed"))
        await D.step("update", zam_update(page), "boundary", idle_s=0.5)
        await D.toast_frame("updated")
        await wait_asks(answered, 2, 90)
        # 3. chargeback → tier 2 at once
        log("A3 chargeback → tier 2")
        phase["cur"] = "chargeback"
        await D.step("open chargeback", zam_open_ticket(page, tids[2]), "boundary", idle_s=2)
        await narrate("Rückbuchung, that's a chargeback at his bank. No discussion, straight to tier two.")
        await D.step("group → Tier 2", zam_set_group(page, "Tier 2 Support"))
        await D.step("priority → high", zam_select(page, "priority_id", "3 high"))
        await D.step("update", zam_update(page), "boundary", idle_s=0.5)
        await D.toast_frame("updated")
        await wait_asks(answered, 3, 120)
        await b.close()
    await asyncio.sleep(2)
    gate = await api("GET", f"/api/sessions/{c.session_id}/gate")
    ledger = await api("GET", f"/api/sessions/{c.session_id}/ledger")
    dur = round(time.monotonic() - t_start)
    await c.close()
    final = {tid: {k: z.ticket(tid).get(k) for k in ("group", "state", "priority")} for tid in tids}
    await dump(tag, "A", c, {"narration": narr, "answered": answered, "gate": gate, "ledger": ledger,
                             "driver": D.notes, "final_tickets": final, "duration_s": dur})
    asks = c.of_type("ask")
    log(f"done: {dur}s frames={len(c.frames)} asks={len(asks)} answered={len(answered)} "
        f"events={sum(len(m.get('items', [])) for m in c.of_type('events'))}")


# ---------------- Phase B ----------------

async def phase_b(tag: str) -> None:
    st = load_state(tag)
    sid = st["capture_session"]
    t0 = time.perf_counter()
    c = ClarosClient(sid, "capture", EXPERT, "en")
    await c.connect()
    c.auto_voice = False
    built, build_s, wid = None, st.get("map_build_s"), st.get("workflow_id")
    if not wid:  # end the capture → map build (skipped when re-running only the debrief)
        await api("POST", f"/api/sessions/{sid}/end")
        for _ in range(600):
            b = [m for m in c.received if m.get("type") == "status" and "Work map built" in (m.get("text") or "")]
            if b:
                built = b[0]
                break
            await asyncio.sleep(0.5)
        build_s = round(time.perf_counter() - t0, 1)
        log("map:", built and built.get("text"), f"{build_s}s")
        await asyncio.sleep(1)
        sess = await api("GET", f"/api/sessions/{sid}")
        upd = [m for m in c.received if m.get("type") == "map_updated"]
        wid = sess.get("workflow_id") or (upd[-1]["workflow_id"] if upd else None)  # client learns it from map_updated
        save_state(tag, workflow_id=wid, map_build_s=build_s)
        if wid:
            (out_dir(tag) / "workmap_prebrief.json").write_text(
                json.dumps(await api("GET", f"/api/workflows/{wid}"), indent=2, ensure_ascii=False))
    c.mode = "debrief"
    await c.send({"type": "hello", "session_id": sid, "mode": "debrief", "user": EXPERT, "lang": "en",
                  "workflow_id": wid})
    await asyncio.sleep(1)
    turns = []
    line = "Okay, let's do it."
    for _ in range(16):
        tr = await c.llm_turn(line)
        turns.append({"say": line, "reply": tr.text, "tool": tr.tool, "ttft_ms": tr.ttft_ms, "total_ms": tr.total_ms})
        log(f"  DEBRIEF {line!r}\n     → {tr.text!r} ({tr.ttft_ms}ms)")
        if re.search(r"published|that's everything|nothing left", tr.text or "", re.I):
            break
        line = debrief_reply(tr.text or "")
        await asyncio.sleep(0.3)
    await asyncio.sleep(2)
    wm = await api("GET", f"/api/workflows/{wid}") if wid else None
    cov = await api("GET", f"/api/workflows/{wid}/coverage") if wid else None
    await c.close()
    (out_dir(tag) / "workmap.json").write_text(json.dumps(wm, indent=2, ensure_ascii=False))
    await dump(tag, "B", c, {"map_build_s": build_s, "built": built, "debrief": turns, "coverage": cov,
                             "workflow_id": wid})
    if wm:
        log(f"steps={len(wm.get('steps', []))} guardrails={len(wm.get('guardrails', []))} coverage={cov}")
        for g in wm.get("guardrails", []):
            log("  G", g["id"], json.dumps(g.get("predicate")), g.get("fuzzy"), g.get("text")[:100])


# ---------------- Phase C / RU ----------------

LEARN_CASES = {
    "en": [("Refund please – annual plan", "priya.nair@customer.example",
            "Hi, you charged me 75 EUR three days ago for the annual plan (order LUM-60112). I don't want it, "
            "please refund. Priya", True),
           ("Small refund for a template pack", "daniel.brooks@customer.example",
            "Hello, I bought a template pack for 30 EUR five days ago (order LUM-60140) and it doesn't fit my needs. "
            "This is the first time I ask for a refund. Daniel", False)],
    "ru": [("Верните деньги за годовой план", "irina.sokolova@customer.example",
            "Здравствуйте! Три дня назад с меня списали 120 EUR за годовой план (заказ LUM-60201). Прошу вернуть "
            "деньги. Ирина", True),
           ("Возврат за шаблоны", "irina.sokolova@customer.example",
            "Здравствуйте, два дня назад купила набор шаблонов за 25 EUR (заказ LUM-60230), он не подошёл. "
            "Раньше возвратов не было. Ирина", False)],
}


async def phase_c(tag: str, lang: str) -> None:
    from playwright.async_api import async_playwright
    st = load_state(tag)
    wid = st.get("workflow_id")
    z = ZamApi()
    cases = LEARN_CASES[lang]
    tids = [z.create_ticket(t, cu, body)["id"] for t, cu, body, _ in cases]
    ptag = "C" if lang == "en" else "RU"
    results: dict[str, Any] = {"cases": []}
    async with async_playwright() as p:
        b, page = await new_browser(p, LEARNER["id"], "ru" if lang == "ru" else "en-us")
        await zam_open_ticket(page, tids[0])
        probe = await ClarosClient.create("request", LEARNER, lang)
        await probe.keyframe(page, "boundary")
        ss, logs = None, []
        for _ in range(40):
            await asyncio.sleep(0.5)
            logs = await api("GET", f"/api/sessions/{probe.session_id}/log?kinds=screen.state")
            if logs and logs[-1]["payload"].get("app"):
                ss = logs[-1]["payload"]
                break
        ss = ss or (logs[-1]["payload"] if logs else None)
        await probe.close()
        utter = "walk me through handling this refund ticket" if lang == "en" else \
            "проведи меня через обработку этой заявки на возврат"
        t0 = time.perf_counter()
        lk = await api("POST", "/api/workflows/lookup", json={"screen_state": ss, "utterance": utter, "lang": lang})
        results |= {"lookup": lk, "lookup_ms": round((time.perf_counter() - t0) * 1000), "expected_workflow": wid}
        log("lookup", json.dumps(lk, ensure_ascii=False)[:300])
        mwid = ((lk or {}).get("match") or {}).get("workflow_id") or wid
        c = await ClarosClient.create("learn", LEARNER, lang, workflow_id=mwid)
        save_state(tag, **{f"learn_session_{lang}": c.session_id})
        D = Driver(page, c)
        intents = []

        async def say(text: str) -> None:
            tr = await c.say(text)
            intents.append({"say": text, "reply": tr.text, "tool": tr.tool, "ttft_ms": tr.ttft_ms})
            log(f"  LEARNER {text!r} → {tr.text!r}")

        await say(utter)
        for (title, _, body, should), tid in zip(cases, tids):
            n0 = len(c.of_type("intervene"))
            t_case = now_ms()
            await D.step("overview", zam_open_overview(page, "all_unassigned"), "boundary")
            await D.step(f"open {title}", zam_open_ticket(page, tid), "boundary", idle_s=2)
            await say("что дальше?" if lang == "ru" else "what's next?")
            note = ("Возврат одобрен, закрываю." if lang == "ru" else "Refund approved by Tier 1, closing.")
            await D.step("note: refund approved", zam_type_note(page, note, on_typing=D.typing))
            await D.step("state → closed", zam_select(page, "state_id", "закрыта" if lang == "ru" else "closed"))
            await D.step("update", zam_update(page), "boundary", idle_s=0.5)
            await D.toast_frame("updated")
            await asyncio.sleep(10)
            iv = c.of_type("intervene")[n0:]
            first = (iv[0]["_at"] - t_case) / 1000 if iv else None
            results["cases"].append({"title": title, "should_intervene": should, "interventions": iv,
                                     "first_intervene_s": first})
            log(f"  case {title!r}: should={should} got={len(iv)} {[m.get('text') for m in iv]}")
        if lang == "ru":
            await say("почему нужно передавать во второй уровень?")
        else:
            await say("why can't I just refund it myself?")
        results["intents"] = intents
        await b.close()
    await c.close()
    await api("POST", f"/api/sessions/{c.session_id}/end")
    await dump(tag, ptag, c, results)
    ZamApi().set_prefs(LEARNER["id"], locale="en-us")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase")
    ap.add_argument("--tag", default="before")
    a = ap.parse_args()
    ph = a.phase.upper()
    if ph == "A":
        await phase_a(a.tag)
    elif ph == "B":
        await phase_b(a.tag)
    elif ph == "C":
        await phase_c(a.tag, "en")
    elif ph == "RU":
        await phase_c(a.tag, "ru")


if __name__ == "__main__":
    asyncio.run(main())
