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
from claros_client import API, ClarosClient, now_ms  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "fixtures" / "e2e"
STATE = OUT / "state.json"
EXPERT = {"id": "expert@ostwind.example", "name": "Erika Expert", "role": "expert"}
LEARNER = {"id": "learner@ostwind.example", "name": "Lea Learner", "role": "learner"}


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


def pick_answer(text: str, phase: str) -> str:
    low = text.lower()
    scores = {k: len(re.findall(p, low)) for k, p in KEYS.items()}
    best = max(scores, key=lambda k: (scores[k], k == phase))
    if scores[best] == 0:
        best = phase
    return ANSWERS[best]


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
    c = await ClarosClient.create("capture", EXPERT, "en")
    log("capture session", c.session_id)
    save_state(capture_session=c.session_id)
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
        await D.step("cost center → Production",
                     erp.set_grid_link(page, "cost_center", "Production", pick="Production - OPP",
                                       on_typing=D.typing), idle_s=1.5)
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
    summ = await dump("A", c, {"narration": narr, "answered": answered, "gate": gate, "ledger": ledger,
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
    sid = st["capture_session"]
    t0 = time.perf_counter()
    c = ClarosClient(sid, "capture", EXPERT, "en")
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
    save_state(workflow_id=wid, map_build_s=build_s)
    # debrief: same session switches to debrief mode (as the client does on `phase`)
    c.mode = "debrief"
    await c.send({"type": "hello", "session_id": sid, "mode": "debrief", "user": EXPERT, "lang": "en",
                   "workflow_id": wid})
    await asyncio.sleep(1)
    turns = []
    script = [
        "Okay, let's do it.",
        "Equipment means machines and tools we keep for years; anything over 5,000 euros net is capitalised.",
        "If it is maintenance or a repair, it stays an expense, even above 5,000.",
        "The controller, Frank, approves UK invoices.",
        "Yes, that's right.",
        "Yes, correct.",
        "Yes.",
        "That's right.",
    ]
    for line in script:
        tr = await c.llm_turn(line)
        turns.append({"say": line, "reply": tr.text, "tool": tr.tool, "ttft_ms": tr.ttft_ms, "total_ms": tr.total_ms})
        log(f"  DEBRIEF {line!r}\n     → {tr.text!r} tool={tr.tool} ({tr.ttft_ms}ms)")
        await asyncio.sleep(0.5)
    await asyncio.sleep(2)
    wm = await api("GET", f"/api/workflows/{wid}") if wid else None
    cov = await api("GET", f"/api/workflows/{wid}/coverage") if wid else None
    await c.close()
    d = OUT / "B"
    d.mkdir(parents=True, exist_ok=True)
    (d / "workmap.json").write_text(json.dumps(wm, indent=2, ensure_ascii=False))
    await dump("B", c, {"map_build_s": build_s, "built": built, "debrief": turns, "coverage": cov,
                        "workflow_id": wid})
    if wm:
        log(f"steps={len(wm.get('steps', []))} guardrails={len(wm.get('guardrails', []))} "
            f"open={len(wm.get('open_unknowns', []))} coverage={cov}")
        for g in wm.get("guardrails", []):
            log("  G", g["id"], g.get("predicate"), g.get("fuzzy"), g.get("text")[:90])
        log("  canonical_vars", wm.get("canonical_vars"))


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
        await b.close()
    await c.close()
    await api("POST", f"/api/sessions/{c.session_id}/end")
    await dump(tag, c, results)


async def main() -> None:
    ph = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()
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
