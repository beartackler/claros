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

## Run 10 — dialog/tutor fixes (B, C, RU re-run on a private :8788 server, scratch copy of the DB)

| Check | Result |
|---|---|
| B: map build | 19.6 s, 8 steps, 3 guardrails; **duplicate rule is now a predicate**: `{"and":[{"var":"prior.same_supplier_amount"},{"==":[{"var":"invoice.posting_month"},12]}]}` (was fuzzy); capex + UK predicates as before; debrief → teach-back → 3 exam cases → published, READY |
| C: lookup | the recorded map, score 0.85 (the seeded `wf_ap_invoice` fixture had won at 0.71 after the B rebuild renamed the map — lookup now adds the share of the map's recorded on-screen labels visible on the learner's screen) |
| C: 7,200 equipment on opex | **1** `intervene` (g_capex, own quote "Equipment over 5,000 is always capex…", `quote_original`, `rule`); no repeat |
| C: 6,100 maintenance | 0 interventions (double-billing no longer judged by the LLM on screens without a matching earlier record) |
| RU | every spoken line Russian: "Шаг 1: Откройте счёт поставщика…", "Шаг 2: Переклассифицируйте позиции…"; 1 intervention (was 2: capex + false double-billing); "почему это капитальные затраты?" → the capex quote (was the double-billing quote); 6,100 → 0 |

Fixes (all generic, no ERPNext code):
1. **Learner language** (`knowledge/tutor.py`): map text (step titles, decisions, conflicts, guardrail text, quotes
   without a stored translation) is GLM-translated once per (workflow, map version, lang) — kv `i18n` + a string-level
   cache so a new version only translates new strings; prewarmed on lookup match / learn hello / map update, awaited
   (≤20 s) before speaking. `intervene` now carries `rule` + `quote` (learner lang) and `quote_original` (expert's
   words). `dialog.context_text`/`lookup` use the cached translations. Rule choice: the quote is the guardrail's own
   best-matching quote (stray "Okay, let's do it." quotes skipped); `why_this` picks among rules that intervened on
   this record / pending / current step by the question (GLM, fallback word overlap) instead of "last intervention".
2. **Dedupe**: `fired[(guardrail, entity)] = {sig}` with an OCR-stable signature of the predicate's values; re-fires
   only when the violating values change (cleared when the predicate is definitively false); save/submit/approve of
   the same violating state → one `escalated` "Before you submit this: Erika would not let it through…", never a repeat.
3. **No fragment values in asks** (`brain/ledger.py`): `speakable_value` drops ellipsized words, trailing `- OPF`
   style ≤3-char fragments and <4-char non-numeric values, prefers a full value from the entity model (event history,
   current screen fields/grid cells); fallback "Why this Expense Head here?". "Plants and Machineries - OPF" →
   "Why Plants and Machineries for Expense Head here?". Spoken values are cut at word boundaries (no "…").
4. **Session memory** (`knowledge/common.py`): every opened record and visible table row is remembered per learn
   session; `prior.same_supplier|same_amount|same_supplier_amount|same_supplier_amount_in_month|count` are derived
   from var-name roles (party/amount/date) and never match the record itself (shared ids). The builder/debrief accept
   them in predicates; fuzzy checks get the earlier records + prior.* as context. `*_month` vars read off a date label
   now yield the month.

Tests: +7 (`test_knowledge.py`: language + original quote, wrong-rule case, dedupe/escalation, prior vars incl. list
rows, fuzzy context, month var; `test_brain.py`: OPF/truncation). `make test` 210 passed; `pnpm build` green.

Still open: the positive double-billing case is covered by unit tests only (C/RU never open a matching duplicate);
no save event was perceived on the 7,200 form in C/RU, so live escalation is untested; quote translations made at
build time translate account names ("Машины и оборудование") while tutor i18n keeps on-screen names.
