# Claros demo script (local, ERPNext)

**ERPNext** and **Claros** each in their **own Chrome window** (side by side is fine — good for an audience),
and you share only the ERPNext window. Working solo, maximize ERPNext and use the Claros **pop-out** on top.
Never put both tabs in one window (Chrome split view): sharing that window makes Claros watch itself.
Lines in quotes are what you say out loud. **Pause** = hands off, silent, 3 seconds — that's when Claros asks.

## Before each run

```bash
cd ~/Documents/GitHub/claros/infra/erpnext && ./reset.sh restore   # drafts back to untouched (+ table columns)
```

- ERPNext: new Chrome **window** → http://localhost:8080 → `expert@ostwind.example` / `Claros-Demo-2026!`
- Claros: second Chrome window → http://localhost:3000 (hard reload: Cmd+Shift+R)

---

## Part 1 — Expert (you are Anna, ~10 min)

### 0. Start
1. Claros window: top-right name chip → **Expert · Anna Keller**.
2. Click the purple card **Show Claros how you work**.
3. Leave **I'll speak: English**. Turn on **Let new hires hear my voice clips** if you want clips.
4. Click **Start**. Chrome's dialog: tab **Window** → click the **ERPNext** window → **Share**. Allow the microphone.
5. Claros says hi. Say: *"I'm going through this week's supplier invoices before they get posted."*
6. Click **Pop out** (top bar). Drag the small window into a corner over ERPNext. Click into ERPNext.

### 1. Air compressor — FMT-2026-0261 (€8,400)
1. Open http://localhost:8080/app/purchase-invoice/ACC-PINV-2026-00027
   (list: *Feldmark Maschinentechnik GmbH*, 01.10.2026).
2. Say: *"Okay, the Feldmark invoice. An air compressor for eight thousand four hundred."*
3. Scroll down to **Items**. Row 1 shows Expense Head **Tools and Small Equipment - OPP**, Cost Center **Administration - OPP**.
4. Say: *"Booked to small tools — that's wrong for a compressor."*
5. Click the **Expense Head** cell → Cmd+A → type `Plants` → click **Plants and Machineries - OPP**.
6. Click the **Cost Center** cell → Cmd+A → type `Production` → click **Production - OPP**.
7. **Cmd+S** (or **Save**, top right). A "Saved" toast appears.
8. **Pause.** When Claros asks why:
   *"Equipment over five thousand euros that we'll use for more than a year gets capitalised as an asset.
   Repairs and overhauls stay operating expenses, even above five thousand. Cost center is Production because it sits on the shop floor."*

### 2. December re-bill — VIS-25-1187-A (€2,850)
1. Open http://localhost:8080/app/purchase-invoice?supplier=Velt%20Industrieservice%20GmbH
   — you see the **paid** one from 04.12.2025 (€2,850) and the **draft** from 22.12.2025.
2. Say: *"Velt again. Two eight fifty — that number looks familiar from December."*
3. Click the **Draft** row dated **22.12.2025**.
4. Say: *"Same amount as the December invoice we already paid."*
5. Right sidebar: **Tags +** → type `On Hold` → Enter → Esc.
6. Tab **More Info** → scroll to the comment box at the bottom → type
   `On hold: duplicate of VIS-25-1187 (paid 04.12.2025). Asked Velt for a credit note.` → **Comment**.
7. Do **not** submit. **Pause.** When asked:
   *"Velt re-bills paid invoices with a letter added at the end, and ERPNext's duplicate check misses that.
   I never submit those — I ask them for a credit note."*

### 3. UK invoice — PFL-3622 (£4,250)
1. Open http://localhost:8080/app/purchase-invoice/ACC-PINV-2026-00029
   (list: *Pennick Facilities Ltd*, 02.10.2026).
2. Say: *"Pennick — that's for the UK company, in pounds."* (Company field: **Ostwind Precision Parts Ltd**.)
3. Black **Actions** button (top right) → **Request Approval** → **Yes** if asked. Status → *Pending Second Approval*.
4. **Pause.** When asked: *"Anything for the UK company needs a second approval from the controller. I never submit those myself."*
5. Then add: *"If I can't tell whether something is a repair or new equipment, I ask the plant manager before booking it."*

### 4. Trust controls (once each)
1. Say *"off the record."* Pop-out shows it's paused. Wait 5 s → tap **Resume**.
2. Say something throwaway (*"ugh, coffee's cold"*), then *"strike that."*

### 5. Debrief
1. Claros window → **Done — debrief** (top bar). The pop-out closes. "Building your map…" can take up to a minute.
2. **Questions**: one at a time. Click **Answer by voice** and answer in one or two sentences (or **Skip**).
3. **Teach-back**: each step → **That's right**, or **Correct this**. On the capex step, correct it on purpose:
   *"No — repairs stay operating expenses even above five thousand."* Check Claros reads back only that change.
4. Say *"Yes, that's how it works"* → **Publish map**.

### 6. Work Map
Click through the steps: each should show your words + a screenshot (click to zoom).

---

## Part 2 — New hire (you are Lea, ~5 min)

### 0. Start
1. ERPNext: bottom-left avatar (**Erika Expert**) → **Log out** → `learner@ostwind.example` / `Claros-Demo-2026!`.
2. Claros: top-right chip → **Learner · Lea Martin** → **Start**. Share the **ERPNext** window, allow the mic.
3. Say: *"I'm entering supplier invoices — first time doing this."* Click **Pop out**, park it over ERPNext.

### 1. Tool presetter — FMT-2026-0274 (€7,200) — same pattern Anna fixed
1. Open http://localhost:8080/app/purchase-invoice/ACC-PINV-2026-00030 (Feldmark, 05.10.2026).
2. Say: *"Feldmark, a tool presetter, seven thousand two hundred."* Scroll to **Items**.
3. When a card appears in the pop-out, answer **by voice**: *"Capex?"*
4. Now deliberately get it wrong: leave **Tools and Small Equipment** and move to **Save**.
   **Expect:** Claros stops you with Anna's screenshot + quote.
5. Fix it: Expense Head → **Plants and Machineries - OPP**, Cost Center → **Production - OPP** → **Cmd+S**.

### 2. Spindle overhaul — VIS-26-0731 (€6,100) — the trap
1. Open http://localhost:8080/app/purchase-invoice/ACC-PINV-2026-00031 (Velt, 06.10.2026).
2. Say: *"Velt, a spindle overhaul, six thousand one hundred."*
3. On the card, say *"I don't know."* **Expect:** Claros explains in Anna's words — repairs stay opex.
4. Leave Expense Head **Repairs and Maintenance - OPP** → **Cmd+S**. **Expect:** Claros does **not** stop you.

### 3. Other ways to answer (once each)
- **Click** an option on a card · **close** a card (×) without answering
- Ask *"why?"* · ask *"show me what Anna did"*

### 4. End
End the session in Claros → short summary: handled alone / after a hint / caught.

---

If something looks wrong, note the clock time — that's enough to find it in the logs.
