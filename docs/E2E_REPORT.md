# Claros headless E2E — real ERPNext + real models (2026-10-03)

Harness: `infra/e2e/` (`run_e2e.py A|B|C|RU`, Playwright 1440×900 @2x → 1600×1000 JPEG q0.75 keyframes with
16×9 changed tiles, `activity` typing/navigating/idle, simulated ElevenLabs: `⟦ask:ID|text⟧`/`⟦intervene:…⟧` and user
turns POSTed to `/llm/v1/chat/completions` with `elevenlabs_extra_body` + system tools). Runs against a private
non-reloading server (`CLAROS_E2E_API=http://localhost:8788`) because `make server --reload` was restarted by other
agents mid-session (wipes in-memory ledger/gate). Replayable fixture: `data/fixtures/e2e/{A,B,C,RU}/`
(`messages.jsonl` both directions, `server_log.jsonl`, `summary.json`, `frames/` = **redacted** frames from
`/api/keyframes`, `B/workmap.json`). Offline perception replay: `python -m claros.perception.replay data/fixtures/e2e/A/frames --vision`.

## Final run (run 9) — results

| Check | Result |
|---|---|
| A: narration → `skip_turn` | 5/5 |
| A: asks | 3 in 134 s, all answered, ~12.6 s ask→answer; 2 guardrail-type (`stop_and_ask`) + 1 `why`: "Why Plants and Machineries - OPF for Expense Head here?", "What made you stop on On Hold? Who decides then?", "What made you stop on Pending Second Approval? Who decides then?" |
| A: events | 13 clean events (open/edit Expense Head/edit Cost Center/save/hold via tag/escalate Pending Second Approval); offline replay of the same frames shows zero noise edits |
| A: ScreenState | app/view/entity_type/entity_id (ACC-PINV-…)/status correct on every form frame after vision; lists = "Purchase Invoice list", no entity |
| B: map build | 31 s (was 65–74 s), 8 steps, all with keyframe + utterance evidence, 3 guardrails |
| B: capex predicate | `{"and":[{">":[{"var":"line.amount"},5000]},{"in":["Tools and Small Equipment",{"var":"line.expense_account"}]}]}` over screen-observable canonical vars; UK = `{"in":["Ltd",{"var":"invoice.company"}]}`; duplicate rule fuzzy |
| B: debrief | 2 questions → teach-back (sentence-complete) → confirmed → 3 exam cases → published, coverage READY |
| C: lookup | match = the new map, score 0.87, READY (0.5 s) |
| C: 7,200 equipment on opex | `intervene` (g_capex_5000, trigger=predicate, quote "Equipment over 5,000 is always capex…", expert moment kf) — before submit |
| C: 6,100 maintenance kept opex | **no** intervention |
| RU | intents ok (walk_through/what_next/off_record → "Не записываю." + control), interventions in Russian, 6,100 no intervention |

Latencies (run 9): vision per keyframe p50 2.3 s / p90 2.7 s / max 2.9 s, 0 failures (37 calls; was 8 s timeout ×100%
failures, then 11–13 s). Brain: pre-written ask/intervene/tutor replies 3–10 ms TTFT; narration skip_turn 1.3 s p50
(intent classification); map build 31 s; lookup 0.5 s.

## Bugs found & fixed

Perception
- **PII redacted record numbers**: phone regex matched `2026-00027` inside `ACC-PINV-2026-00027` → entity_id lost (`pii.py`).
- **Vision never worked on real screens**: 8 s timeout vs 11–13 s (GLM default reasoning ~900 tokens) and strict
  `json_schema` made GLM return only required keys (`fields: []`). `llm.py`: per-role `reasoning_effort: low`,
  `json_object` mode + schema in the system prompt for Isoquant, vision timeout 25 s, smart 120 s.
- VisionState tolerant: rows as dicts, bool/number values, `[x1,y1,x2,y2]` boxes converted, `[REDACTED]` ids dropped.
- Vision templates were never applied (looked up by heuristic key "view:assign"); now matched by entity id, by the
  frame vision read, or by label overlap.
- Heuristics on dense ERPNext: sidebar words as titles, first list row id as entity, status badge glued to the
  breadcrumb ("…GmbHNot Saved"), "Pending Second Approval" unknown, "Saved Filters"/"Not Saved" read as save toasts.
- Grid cells: values tracked per column (column = gap between neighbouring headers, argmax overlap); label/label
  mis-pairs, checkbox pairs and non-template pairs dropped; no edit events on list views, before the screen's vision
  template exists, or for fields scrolling in/out; diff re-baselined on the frame vision read.
- OCR noise / truncation / currency-format differences are no longer edits; number format learned from the screen
  (`8.400,00` ⇒ decimal comma even with an English UI: "1,000" qty = 1).
- Tag/chip "On Hold" → `hold` event (any status word set on a record), `Pending …` → `escalate`.

Brain / knowledge
- Ask gap 90 s→45 s, unknown expiry 45→60 s (mid-task asks died waiting), hypothesis cluster cos 0.85→0.8, spoken
  values cleaned (`×`, `...`) and refreshed from the current screen at ask time.
- Builder: predicates only over vars that were on-screen labels (else fuzzy), tautological var==var dropped,
  numeric string thresholds coerced, **inverted predicates re-oriented** against captured before/after screens (LLM
  wrote `in "Plants and Machineries"` = the fix, not the mistake), alias collisions (`Amount (EUR)` → line.amount and
  invoice.amount) fixed, same expert re-recording reuses their workflow id, quote translation on smart role.
- Debrief: no bare "Why?", teach-back cut at a sentence + confirmation question, restated rules merged into the
  existing guardrail.
- Tutor: fuzzy guardrails require every condition visibly met (a December double-billing rule fired on the Oct
  equipment invoice).
- `llm_endpoint.py` (edited before the freeze): answer/correction with nothing asked → narration (was "Got it,
  corrected." to "that's wrong for a compressor"); learner "не записывай" → off-record.

## Open issues (not fixed)
- Dialog layer (owned elsewhere): RU tutor speaks English step titles inside Russian frames ("Шаг 2: Reclassify…");
  RU "почему это капитальные затраты?" answered with the double-billing quote; intervention fires again on save
  (re-arm on state change) — 2 identical `intervene` per record.
- First ask still says "- OPF": OCR truncation of the cell; vision corrects later but not before the ask.
- Ledger asked `why` on the expense-head edit, not the confirm-style capex hypothesis (5 hypotheses rarely cluster).
- Exam cases sometimes omit the amount slot; probes/exam derive only from deterministic predicates.
- Duplicate rule stays fuzzy (needs a paid-invoice lookup that the screen can't show); hold via comment text is
  invisible to perception (More Info tab has no fields).
- Grid tracking is single-row only; multi-row grids get vision values on their own frame only.
- Fallback LLM providers have no keys; any Isoquant outage = no vision/map build.
- ERPNext v16 hides Accounting Dimensions for drafts: harness sets a per-user grid column preference
  (Expense Head, Cost Center) and models "hold" as tag + comment (no draft hold field).
