"""Scripted headless end-to-end run: REAL ERPNext screens (Playwright) + REAL models, simulated voice.

    cd server && .venv/bin/python ../infra/e2e/run_e2e.py A        # expert capture (resets ERPNext first)
    cd server && .venv/bin/python ../infra/e2e/run_e2e.py B        # end session → map build → debrief
    cd server && .venv/bin/python ../infra/e2e/run_e2e.py C        # learner (lookup, learn, intervene / no-intervene)
    cd server && .venv/bin/python ../infra/e2e/run_e2e.py RU       # Russian learner + expert intents

State (session ids, workflow id) is kept in data/fixtures/e2e/state.json; every phase writes
data/fixtures/e2e/<phase>/ {messages.jsonl, server_log.jsonl, summary.json, frames/ (REDACTED only)}.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
import erp  # noqa: E402
from claros_client import API, HOSTED, ClarosClient, now_ms  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "fixtures" / "e2e"
STATE = OUT / "state.json"
EXPERT = {"id": "expert@ostwind.example", "name": "Erika Expert", "role": "expert"}
LEARNER = {"id": "learner@ostwind.example", "name": "Lea Learner", "role": "learner"}
# second expert (stretch: "two experts, one task") — same task, one different judgment call (cost center)
EXPERT2 = {"id": "expert2@ostwind.example", "name": "Max Mertens", "role": "expert"}
X = {"expert": EXPERT, "cost_center": ("Production", "Production - OPP"), "tag": "", "answers": {}}


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(**kw: Any) -> dict:
    st = load_state() | kw
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2))
    return st


def log(*a: Any) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


async def api(method: str, path: str, **kw: Any) -> Any:
    async with httpx.AsyncClient(timeout=300) as h:
        r = await h.request(method, f"{API}{path}", **kw)
        r.raise_for_status()
        return r.json() if r.content else None


# ---------------- expert answers (simulated person) ----------------

ANSWERS = {
    "capex": "Equipment over 5,000 is always capex here, so it goes to Plants and Machineries and the "
             "Production cost center, never to small tools.",
    "dup": "This supplier double-bills every December; hold it until they confirm. Same amount, just a "
           "suffix on the old invoice number.",
    "approval": "Anything from the UK subsidiary needs a second approval from the controller before it is booked.",
}
KEYS = {
    "capex": r"capex|capital|asset|plant|machin|expense|account|5[,.]?000|equipment|tools?\b|cost cent|"
             r"production|administration|feldmark",
    "dup": r"hold|duplicat|velt|vis-|again|december|re-?bill|tag|comment|twice|block|same amount|paid",
    "approval": r"approv|subsidiar|\buk\b|gbp|pennick|second|request|ltd|controller|pending",
}


ANSWERS2 = {
    "capex": "Equipment over 5,000 is capex, so Plants and Machineries. I book it to Administration, not "
             "Production, because the fixed-asset register is kept there.",
}


def pick_answer(text: str, phase: str) -> str:
    low = text.lower()
    scores = {k: len(re.findall(p, low)) for k, p in KEYS.items()}
    best = max(scores, key=lambda k: (scores[k], k == phase))
    if scores[best] == 0:
        best = phase
    return X["answers"].get(best) or ANSWERS[best]


DEBRIEF_KB = [
    (r"4[,.]?999|below|under 5|less than 5", "No — below 5,000 it stays an expense, small tools is fine then."),
    (r"maint|repair|overhaul|service", "Maintenance or repairs stay an expense even above 5,000; only new equipment "
                                       "is capitalised."),
    (r"new supplier|unknown supplier|never seen|don't know the supplier|first time",
     "If I don't know the supplier, I stop and ask the controller before booking anything."),
    (r"duplicate|december|velt|double|hold|same amount|re-?bill|release",
     "Any supplier: if the amount matches an invoice we already paid, put it on hold and ask the supplier. I release "
     "it myself once they confirm it's not a duplicate."),
    (r"uk|subsidiar|ltd|gbp|pound|second approval|controller|pennick",
     "Every invoice of the UK company goes to Frank, the controller, for second approval."),
    (r"capex|capital|asset|plant|machin|equipment|5[,.]?000|cost cent|production|expense head|tools",
     "Equipment over 5,000 net is capex: Plants and Machineries and the Production cost center."),
]


TEACHBACK_CORRECTION = ("One detail: maintenance and repairs stay an expense even above 5,000 — only new equipment "
                        "is capex.")
_corrected: dict = {}


def truth(variant: str) -> Optional[bool]:
    """Ground truth for exam cases: should a guardrail stop the learner? None = can't tell."""
    v = variant.lower()
    if re.search(r"maint|repair|overhaul|plants and mach|fixed asset", v):
        return False  # maintenance stays opex; already capitalised is the correct coding
    if re.search(r"uk|subsidiar|gbp|ltd", v):
        return True
    if re.search(r"december|double|duplicate|same amount|already paid", v):
        return True
    nums = [float(x.replace(",", "").replace(".", "")) for x in re.findall(r"\d[\d.,]{2,}", v)]
    big = [n for n in nums if 1000 <= n < 10_000_000]
    if big and re.search(r"tool|equip|compress|machin|small|amount", v):
        return max(big) > 5000
    return None


CONFLICT2 = ("New machines that go into the fixed-asset register I book to Administration, because the register is "
             "kept there; spare parts and running costs go to Production like Erika does.")


def debrief_reply(text: str) -> str:
    low = text.lower()
    if X["tag"] and re.search(r"erika|another expert|other expert|; you |you (booked|book|used|chose|put)", low) \
            and not re.search(r"did i get|how it works\??\s*$", low):
        return CONFLICT2
    m = re.search(r"case \d+:(.*?)i would:(.*?)(\(\d+% sure\))", low, re.S)
    if m:
        want = truth(m.group(1))
        stops = bool(re.search(r"block|stop|hold|capital|capex|approval|plants|ask", m.group(2)))
        if want is None or want == stops:
            return "Yes, correct."
        return ("No, that one must be stopped — it's a guardrail case." if want else
                "No, that's fine to book normally, no guardrail applies there.")
    if re.search(r"is (that|this) right\??\s*$|did i get (it|that) right|how it works\??\s*$|confirm", low) \
            and len(low.split()) > 40:
        return TEACHBACK_CORRECTION if not _corrected.get("done") else "Yes, that's how it works."
    if re.search(r"now:|now says|changed|anything else|is that how it works|is it right now", low):
        _corrected["done"] = True
        return "Yes, that's how it works."
    for pat, ans in DEBRIEF_KB:
        if re.search(pat, low):
            return ans
    return "Yes, that's how I do it."


# ---------------- recording ----------------

async def dump(phase: str, c: ClarosClient, extra: Optional[dict] = None, sessions: Optional[list[str]] = None) -> dict:
    d = OUT / phase
    (d / "frames").mkdir(parents=True, exist_ok=True)
    with open(d / "messages.jsonl", "w") as f:
        for m in c.sent:
            f.write(json.dumps({"dir": "c2s", **m}, ensure_ascii=False) + "\n")
        for m in c.received:
            f.write(json.dumps({"dir": "s2c", **m}, ensure_ascii=False) + "\n")
    sids = sessions or [c.session_id]
    logs = []
    for sid in sids:
        logs += await api("GET", f"/api/sessions/{sid}/log")
    with open(d / "server_log.jsonl", "w") as f:
        for e in logs:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    # redacted frames only (served by the server after redaction)
    async with httpx.AsyncClient(timeout=30) as h:
        for fr in c.frames:
            kid = f"{c.session_id}_{fr['seq']}"
            r = await h.get(f"{API}/api/keyframes/{kid}.jpg")
            if r.status_code == 200:
                (d / "frames" / f"{fr['seq']:03d}_{fr['reason']}.jpg").write_bytes(r.content)
                fr["redacted_file"] = f"frames/{fr['seq']:03d}_{fr['reason']}.jpg"
    summary = {"session_id": c.session_id, "frames": c.frames,
               "turns": [t.__dict__ for t in c.turns], **(extra or {})}
    (d / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return summary


# ---------------- UI step helper ----------------

class Driver:
    def __init__(self, page: Any, c: ClarosClient) -> None:
        self.page, self.c = page, c
        self.notes: list[dict] = []

    async def typing(self) -> None:
        await self.c.activity("typing", tiles=6)

    async def step(self, label: str, coro: Any, reason: str = "settle", idle_s: float = 1.2) -> dict:
        await self.c.activity("navigating", tiles=40)
        t0 = time.perf_counter()
        await coro
        ui_ms = round((time.perf_counter() - t0) * 1000)
        await self.c.activity("idle", tiles=0)
        info = await self.c.keyframe(self.page, reason)
        info |= {"label": label, "ui_ms": ui_ms}
        self.notes.append(info)
        log(f"  kf seq={info.get('seq')} {reason:8s} {label}")
        await asyncio.sleep(idle_s)
        return info

    async def toast_frame(self, label: str) -> None:
        info = await self.c.keyframe(self.page, "toast")
        info |= {"label": label}
        self.notes.append(info)
        log(f"  kf seq={info.get('seq')} toast    {label}")


async def wait_asks(c: ClarosClient, n: int, max_s: float, answered: list) -> None:
    end = time.monotonic() + max_s
    while time.monotonic() < end and len(answered) < n:
        await asyncio.sleep(0.5)


# ---------------- Phase A: expert capture ----------------

async def phase_a() -> None:
    log("reset ERPNext")
    subprocess.run([str(ROOT / "infra/erpnext/reset.sh"), "restore"], check=True, capture_output=True)
    c = await ClarosClient.create("capture", X["expert"], "en")
    log("capture session", c.session_id, X["expert"]["name"])
    save_state(**{f"capture_session{X['tag']}": c.session_id})
    phase = {"cur": "capex"}
    answered: list[dict] = []

    async def on_ask(m: dict) -> None:
        if m.get("unknown_id") == "phase-debrief":
            return
        await asyncio.sleep(1.2)  # expert thinks
        ans = pick_answer(m.get("text") or "", phase["cur"])
        log(f"  ASK {m['unknown_id']}: {m.get('_spoken')!r} → answer {ans[:50]!r}")
        turn = await c._say(ans)
        answered.append({"ask": m, "answer": ans, "reply": turn.__dict__ if turn else None, "at": now_ms()})

    c.on_ask = on_ask
    narr: list[dict] = []

    async def narrate(text: str) -> None:
        turn = await c.say(text)
        narr.append({"text": text, "tool": turn.tool if turn else None, "reply": turn.text if turn else None})
        log(f"  SAY {text!r} → tool={turn.tool if turn else None} text={turn.text[:60] if turn else ''!r}")

    t_start = time.monotonic()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        await ctx.add_init_script("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement("
                                  "'style');s.textContent='.onboarding-widget-box,.user-onboarding{display:none!important}';"
                                  "document.head.appendChild(s)})")
        page = await ctx.new_page()
        await erp.login(page, "expert@ostwind.example")
        await erp.configure_grid(page)
        D = Driver(page, c)

        # ---- 1. equipment invoice ----
        log("A1 equipment invoice")
        await D.step("purchase invoice list", erp.open_list(page), "boundary")
        await D.step("open FMT-2026-0261", erp.open_doc(page, "ACC-PINV-2026-00027"), "boundary", idle_s=2)
        await narrate("Okay, the Feldmark invoice. An air compressor for eight thousand four hundred.")
        await asyncio.sleep(3)
        await D.step("expense head → Plants and Machineries",
                     erp.set_grid_link(page, "expense_account", "Plants and Mach",
                                       pick="Plants and Machineries - OPP", on_typing=D.typing), idle_s=2)
        await narrate("Booked to small tools, that's wrong for a compressor.")
        cc, cc_pick = X["cost_center"]
        await D.step(f"cost center → {cc}",
                     erp.set_grid_link(page, "cost_center", cc, pick=cc_pick, on_typing=D.typing), idle_s=1.5)
        await D.step("save", erp.save(page), "boundary", idle_s=0.3)
        await D.toast_frame("saved toast")
        await wait_asks(c, 1, 45, answered)

        # ---- 2. December re-bill ----
        log("A2 December re-bill")
        phase["cur"] = "dup"
        await D.step("list filtered by Velt",
                     erp.open_list(page, query="?supplier=Velt%20Industrieservice%20GmbH"), "boundary", idle_s=2)
        await narrate("Velt again. Two eight fifty, that number looks familiar from December.")
        await D.step("open VIS-25-1187-A", erp.open_doc(page, "ACC-PINV-2026-00028"), "boundary", idle_s=2)
        await narrate("Same amount as the December invoice that we already paid.")
        await D.step("tag On Hold", erp.add_tag(page, "On Hold", on_typing=D.typing), idle_s=1.5)
        await D.step("hold comment",
                     erp.add_comment(page, "On hold: duplicate of VIS-25-1187 (paid 04.12.2025). Waiting for Velt "
                                           "to confirm.", on_typing=D.typing), idle_s=1.5)
        await D.step("details tab", erp.details_tab(page), idle_s=0.5)
        await D.step("save", erp.save(page), "boundary", idle_s=0.3)
        await D.toast_frame("saved toast")
        await wait_asks(c, 2, 100, answered)

        # ---- 3. subsidiary invoice ----
        log("A3 subsidiary invoice")
        phase["cur"] = "approval"
        await D.step("list", erp.open_list(page), "boundary")
        await D.step("open PFL-3622", erp.open_doc(page, "ACC-PINV-2026-00029"), "boundary", idle_s=2)
        await narrate("Pennick, that's for the UK company, in pounds.")
        await D.step("Actions → Request Approval", erp.workflow_action(page, "Request Approval"), "boundary", idle_s=1)
        await wait_asks(c, 3, 120, answered)
        state = await erp.doc_state(page)
        await b.close()

    await asyncio.sleep(2)
    gate = await api("GET", f"/api/sessions/{c.session_id}/gate")
    ledger = await api("GET", f"/api/sessions/{c.session_id}/ledger")
    dur = round(time.monotonic() - t_start)
    await c.close()
    summ = await dump("A" + X["tag"], c, {"narration": narr, "answered": answered, "gate": gate, "ledger": ledger, "spoken": c.spoken,
                               "driver": D.notes, "final_doc": state, "duration_s": dur})
    asks = c.of_type("ask")
    log(f"done: {dur}s, frames={len(c.frames)}, asks={len(asks)}, answered={len(answered)}, "
        f"events={sum(len(m.get('items', [])) for m in c.of_type('events'))}")
    for m in asks:
        u = next((x for x in ledger["unknowns"] if x["id"] == m["unknown_id"]), {})
        log(f"   ask {u.get('type')}/{u.get('scope')}: {m.get('text')!r}")


# ---------------- Phase B: end → map → debrief ----------------

async def phase_b() -> None:
    st = load_state()
    sid = st[f"capture_session{X['tag']}"]
    t0 = time.perf_counter()
    c = ClarosClient(sid, "capture", X["expert"], "en")
    await c.connect()
    c.auto_voice = False
    await api("POST", f"/api/sessions/{sid}/end")
    log("ended; waiting for map build")
    built = None
    for _ in range(600):
        st_msgs = [m for m in c.received if m.get("type") == "status" and "Work map built" in (m.get("text") or "")]
        if st_msgs:
            built = st_msgs[0]
            break
        await asyncio.sleep(0.5)
    build_s = round(time.perf_counter() - t0, 1)
    log("map:", built and built.get("text"), f"{build_s}s")
    sess = await api("GET", f"/api/sessions/{sid}")
    wid = sess.get("workflow_id")
    if not wid:
        wfs = await api("GET", "/api/workflows")
        mine = [w for w in wfs if sid in json.dumps(await api("GET", f"/api/workflows/{w['workflow_id']}"))]
        wid = mine[0]["workflow_id"] if mine else None
    log("workflow", wid)
    save_state(**{f"workflow_id{X['tag']}": wid, f"map_build_s{X['tag']}": build_s})
    # debrief: same session switches to debrief mode (as the client does on `phase`)
    c.mode = "debrief"
    if HOSTED:
        c.auto_voice = True
        turns = await hosted_debrief(c, sid, wid)
    else:
        turns = await custom_debrief(c, sid, wid)
    await asyncio.sleep(2)
    wm = await api("GET", f"/api/workflows/{wid}") if wid else None
    cov = await api("GET", f"/api/workflows/{wid}/coverage") if wid else None
    await c.close()
    d = OUT / ("B" + X["tag"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "workmap.json").write_text(json.dumps(wm, indent=2, ensure_ascii=False))
    await dump("B" + X["tag"], c, {"map_build_s": build_s, "built": built, "debrief": turns, "coverage": cov,
                        "workflow_id": wid, "spoken": c.spoken})
    if wm:
        log(f"steps={len(wm.get('steps', []))} guardrails={len(wm.get('guardrails', []))} "
            f"open={len(wm.get('open_unknowns', []))} coverage={cov}")
        for g in wm.get("guardrails", []):
            log("  G", g["id"], g.get("predicate"), g.get("fuzzy"), g.get("text")[:90])
        log("  canonical_vars", wm.get("canonical_vars"))


async def hosted_debrief(c: ClarosClient, sid: str, wid: Optional[str]) -> list[dict]:
    """Hosted dialog: hello mode=debrief → the server pushes `say`; the expert answers with final transcripts."""
    n0 = len(c.spoken)
    await c.send({"type": "hello", "session_id": sid, "mode": "debrief", "user": X["expert"], "lang": "en",
                  "workflow_id": wid})
    turns: list[dict] = []
    _corrected.clear()
    for _ in range(16):
        # wait for the server's next utterance(s) to be spoken, then ~3 s of quiet (teach-back = several segments)
        end = time.monotonic() + 90
        while time.monotonic() < end and len(c.spoken) == n0:
            await asyncio.sleep(0.3)
        last = len(c.spoken)
        quiet_since = time.monotonic()
        # quiet = nothing new spoken AND the agent is not mid-utterance (a teach-back is several `say` segments)
        while time.monotonic() - quiet_since < 3.0 and time.monotonic() < end:
            await asyncio.sleep(0.3)
            if len(c.spoken) != last or c.agent_speaking:
                last, quiet_since = len(c.spoken), time.monotonic()
        batch = c.spoken[n0:]
        n0 = len(c.spoken)
        if not batch:
            log("  DEBRIEF: no reply from the server")
            break
        text = " ".join(b["text"] for b in batch)
        log(f"  CLAROS ({','.join(sorted({b['kind'] for b in batch}))}): {text!r}")
        if re.search(r"published|that's everything|nothing left", text, re.I):
            turns.append({"claros": text, "kinds": [b["kind"] for b in batch],
                          "steps": [b.get("step_id") for b in batch if b.get("step_id")]})
            break
        reply = debrief_reply(text)
        turns.append({"claros": text, "kinds": [b["kind"] for b in batch],
                      "steps": [b.get("step_id") for b in batch if b.get("step_id")], "expert": reply})
        log(f"  EXPERT: {reply!r}")
        await asyncio.sleep(0.8)
        await c.say(reply)
    return turns


async def custom_debrief(c: ClarosClient, sid: str, wid: Optional[str]) -> list[dict]:
    await c.send({"type": "hello", "session_id": sid, "mode": "debrief", "user": EXPERT, "lang": "en",
                   "workflow_id": wid})
    await asyncio.sleep(1)
    turns = []
    line = "Okay, let's do it."
    for _ in range(16):
        tr = await c.llm_turn(line)
        turns.append({"say": line, "reply": tr.text, "tool": tr.tool, "ttft_ms": tr.ttft_ms, "total_ms": tr.total_ms})
        log(f"  DEBRIEF {line!r}\n     → {tr.text!r} tool={tr.tool} ({tr.ttft_ms}ms)")
        if re.search(r"published|that's everything|nothing left", tr.text or "", re.I):
            break
        line = debrief_reply(tr.text or "")
        await asyncio.sleep(0.3)
    return turns


# ---------------- Phase C: learner ----------------

async def phase_c(lang: str = "en") -> None:
    st = load_state()
    wid = st.get("workflow_id")
    tag = "C" if lang == "en" else "RU"
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        await ctx.add_init_script("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement("
                                  "'style');s.textContent='.onboarding-widget-box,.user-onboarding{display:none!important}';"
                                  "document.head.appendChild(s)})")
        page = await ctx.new_page()
        await erp.login(page, "learner@ostwind.example")
        await erp.configure_grid(page)
        await erp.open_list(page)
        await erp.open_doc(page, "ACC-PINV-2026-00030")
        # lookup with a screen state from a quick probe session (what the orb does before starting)
        probe = await ClarosClient.create("request", LEARNER, lang)
        await probe.keyframe(page, "boundary")
        ss = None
        for _ in range(40):
            await asyncio.sleep(0.5)
            logs = await api("GET", f"/api/sessions/{probe.session_id}/log?kinds=screen.state")
            if logs and logs[-1]["payload"].get("app"):
                ss = logs[-1]["payload"]
                break
        ss = ss or (logs[-1]["payload"] if logs else None)
        await probe.close()
        utter = "walk me through processing this supplier invoice" if lang == "en" else \
            "проведи меня через обработку этого счёта поставщика"
        t0 = time.perf_counter()
        lk = await api("POST", "/api/workflows/lookup", json={"screen_state": ss, "utterance": utter, "lang": lang})
        lookup_ms = round((time.perf_counter() - t0) * 1000)
        log("lookup", json.dumps(lk)[:400], f"{lookup_ms}ms")
        match = (lk or {}).get("match") or {}
        mwid = match.get("workflow_id") or wid
        c = await ClarosClient.create("learn", LEARNER, lang, workflow_id=mwid)
        log("learn session", c.session_id, "workflow", mwid)
        save_state(**{f"learn_session_{lang}": c.session_id})
        D = Driver(page, c)
        results: dict[str, Any] = {"lookup": lk, "lookup_ms": lookup_ms, "workflow_id": mwid}
        intents = []

        async def ask_learner(text: str) -> None:
            tr = await c.say(text)
            log(f"  LEARNER {text!r} → {tr.text!r} tool={tr.tool} ({tr.ttft_ms}ms)")
            intents.append({"say": text, "reply": tr.text, "tool": tr.tool, "ttft_ms": tr.ttft_ms})

        await ask_learner(utter)
        # held-out 7,200 equipment draft, coded as OPEX (learner keeps/sets the opex account)
        await D.step("open FMT-2026-0274 (7,200)", erp.open_doc(page, "ACC-PINV-2026-00030"), "boundary", idle_s=2)
        await D.step("expense head (opex) Tools and Small Equipment",
                     erp.set_grid_link(page, "expense_account", "Tools and Small", pick="Tools and Small Equipment - OPP",
                                       on_typing=D.typing), idle_s=1.5)
        await D.step("cost center (opex) Administration",
                     erp.set_grid_link(page, "cost_center", "Administration", pick="Administration - OPP",
                                       on_typing=D.typing), idle_s=1.5)
        await ask_learner("что дальше?" if lang == "ru" else "what's next?")
        await D.step("save", erp.save(page), "boundary", idle_s=0.3)
        await D.toast_frame("saved toast")
        await asyncio.sleep(6)
        iv_7200 = [m for m in c.of_type("intervene")]
        log(f"interventions after 7,200: {len(iv_7200)}", [m.get("text") for m in iv_7200])
        results["intervene_7200"] = iv_7200
        if lang == "ru":
            await ask_learner("не записывай")
            results["off_record_ack"] = c.of_type("control")[-1:] if c.of_type("control") else None
            await c.send({"type": "control", "t": now_ms(), "action": "off_record_off"})  # orb button: back on
            await ask_learner("почему это капитальные затраты?")
        # 6,100 maintenance invoice, keep opex → expect NO intervention
        n0 = len(c.of_type("intervene"))
        await D.step("list", erp.open_list(page), "boundary")
        await D.step("open VIS-26-0731 (6,100 maintenance)", erp.open_doc(page, "ACC-PINV-2026-00031"), "boundary",
                     idle_s=2)
        await D.step("keep opex: re-select Repairs and Maintenance",
                     erp.set_grid_link(page, "expense_account", "Repairs and Main", pick="Repairs and Maintenance - OPP",
                                       on_typing=D.typing), idle_s=1.5)
        await D.step("save", erp.save(page), "boundary", idle_s=0.3)
        await D.toast_frame("saved toast")
        await asyncio.sleep(8)
        iv_6100 = c.of_type("intervene")[n0:]
        log(f"interventions on 6,100: {len(iv_6100)}", [m.get("text") for m in iv_6100])
        results["intervene_6100"] = iv_6100
        results["intents"] = intents
        results["spoken"] = c.spoken
        await b.close()
    await c.close()
    await api("POST", f"/api/sessions/{c.session_id}/end")
    await dump(tag, c, results)


async def main() -> None:
    ph = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
    if ph in ("A2", "B2"):
        X.update(expert=EXPERT2, cost_center=("Administration", "Administration - OPP"), tag="2", answers=ANSWERS2)
        ph = ph[0]
    if ph == "A":
        await phase_a()
    elif ph == "B":
        await phase_b()
    elif ph == "C":
        await phase_c("en")
    elif ph == "RU":
        await phase_c("ru")


if __name__ == "__main__":
    asyncio.run(main())
