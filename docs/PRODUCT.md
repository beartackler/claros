# Claros — product spine

## The crux (from the brief)
Expert judgment ("why", limits, exceptions, when to stop and ask) is never written down.
Recorders capture *what*; Claros captures *why* by watching real screen work, asking only
what it cannot learn elsewhere, and proves understanding twice: the expert confirms a
teach-back, and a new person handles an unseen case correctly.
Bar: "If a new person could not do the task from what it learned, it is not an apprentice."

## Where Claros lives (workflow fit)
Claros is a **dormant, always-available companion** (floating Picture-in-Picture orb today,
browser extension tomorrow). It never captures until someone invokes it.

Learner-pull is the primary loop — expert time is spent only where a learner needs it:

1. Learner (on any web app): "Claros, walk me through this."
2. Claros identifies the workflow (screen state + spoken intent + O*NET prior) and looks it up.
3. Coverage check on the matched Work Map:
   - **Ready** (approved, no open unknowns) → learning loop.
   - **Partial** → teach the confirmed parts, clearly mark unconfirmed ones, never invent rules.
   - **Missing** → *fake door, honestly*: "Nobody has shown me this yet. Want me to ask an expert?"
     → creates a capture request in the expert's inbox (with the learner's screen moment).
4. Expert opens request → capture session (works + talks; Claros asks at pauses) → debrief
   (deliberate friction: closes gaps, teach-back, expert confirms) → map published.
5. Learner is notified → learning loop on their own case.
6. Novel cases the learner hits become new unknowns for the expert (living memory).

Push entry also exists (expert clicks "Teach Claros" / manager builds onboarding path), but the
demo story is pull.

## Ask less: context before questions
Before an unknown reaches the expert, Claros classifies its **knowledge scope**:
- `universal` (accounting/legal/domain basics: what is capex) → answer with LLM knowledge.
- `app` (what an ERP field/button does) → app docs via web search + reader tools.
- `occupation` (generic structure of the task) → O*NET task/DWA prior.
- `company` (thresholds, policies, who approves) → **ask the expert** (or company docs if imported).
- `personal_judgment` (tacit heuristics, exceptions, "smells") → **ask the expert**.
Only `company` and `personal_judgment` cost expert attention. Prefer hypothesis-confirm
questions ("Looks like equipment over 5,000 always goes to capex here — is that your rule?")
over open ones when a confident hypothesis exists; open "why" otherwise. Generic context is still
stored in the map, cited ("from ERPNext docs", "O*NET 43-3031.00"), so the tutor can explain it.

## Many experts, one flow
Maps from several experts for one workflow are aligned (state signatures + embeddings) into one
canonical flow with variants:
- order differences → partial order, no conflict;
- guardrails → union (conservative), each attributed;
- coverage → union of cases;
- contradictory decisions → **conflict** → both experts asked why; until resolved the learner sees
  "Experts differ here: A does X because…, B does Y because… — ask your lead" (never silently pick).
Learner sees one coherent path, decision points with consensus strength and attribution.

## Friction map (deliberate)
| Moment | Friction | Why |
|---|---|---|
| Invoking Claros (learner or expert) | ~zero: voice, orb button | habit formation; always available |
| Expert capture | very low: just work + talk; ≤3–5 questions/10 min, hypothesis-confirm | expert time is scarce |
| Debrief | **intentional** but short (≤6 questions/5 min), progress visible (ledger → 0) | quality comes from here |
| Learner prediction prompts | **desirable difficulty** | learning sticks via retrieval/prediction |
| Guardrail before submit | hard stop | the whole point |
| Expert request from learner | one tap to accept / schedule | demand-driven capture |

## Intents (voice, any language)
Learner: walk_through · what_next · why_this · check_my_work · hint · just_watch · stop ·
ask_expert · answer_prediction · off_topic.
Expert: narration · answer · correction · confirm · not_now · off_record · strike_that ·
question_to_claros · end_session.
Classified in our custom-LLM brain on every user turn (fast decision model, multilingual) and
routed to deterministic handlers; free-form only when needed.

## Languages
EN, DE, FR, ES, RU at minimum (stretch demo: Russian). Expert and learner may differ: quotes are
stored in the original language with translation; tutor speaks the learner's language and can play
the expert's original clip with translated captions. No voice clones.

## Horizontal
Any web/desktop app via window share + vision; workflow identity via O*NET task match + map library
match; no app-specific code paths. ERPNext (accounts payable) is just our main test bed.

## What we are NOT optimizing for
Every stretch goal. Two-expert merge is in because it is core to "living memory" and the learner
experience; agent-export is a thin JSON/SKILL.md export, not a showcase.
