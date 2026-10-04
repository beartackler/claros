# Claros — product spine (v2, 2026-10-04 — supersedes the learner-pull/LMS framing below where they conflict)

## The journey, per the brief (Capture → Map → Teach). Voice is the product; screens are support.
EXPERT (one sitting, ~10–15 min):
1. Start → share the app window + mic in ONE guided gesture. Claros says hi and asks what they're about to do (voice; no form).
2. CAPTURE: expert works a real task and talks. Claros stays quiet while they type/read/talk, asks 3–5 short questions at natural pauses (≥1 guardrail). Screen: just the floating orb + live captions. Nothing to read.
3. DEBRIEF (same session, deliberate friction): Claros asks what's still unclear (incl. any disagreement with another expert's earlier run — asked HERE, not left as an "open question"), then explains the process back; expert corrects; confirms "yes, that's how it works".
4. WORK MAP: the artifact. Clickable timeline: screen moment (big, zoomable), decision, reason in the expert's words, guardrails. Expert can glance and approve; it's also what judges/managers see.
There are NO lingering "open questions" lists: anything unresolved is resolved in the debrief; if the expert leaves early, the map is marked "needs a second run" — one clear state.

LEARNER (live, on their own screen, real case):
1. Start → share window + mic in one gesture → "What are you working on?" (voice). Claros recognizes the workflow from screen + speech.
2. TEACH: learner works; Claros explains steps the way the expert did, asks them to predict the next decision (by voice), steps in BEFORE a guardrail is broken, and replays the expert's screen moment (large card) with the expert's words.
3. End: a short summary — what they handled on their own, what to practice next. That's the only "report".
No learner course library, no screenshot quizzes, no click-through LMS. (Nudge cards below are LIVE, triggered by the learner's real work — not a quiz.) If Claros has never seen the workflow, it says so honestly and offers to ask an expert (one tap) — that request is what the expert sees on their home.

## LEARNER live loop (v2.1 — interactive nudges, every action is a signal)
Claros lives in the floating companion (Document PiP, always on top — it cannot draw over other apps, so the "overlay" IS the companion window, docked beside the app).
At a decision point Claros SPEAKS a short nudge and the companion shows a NUDGE CARD: the expert's reference (large zoomable screen moment + quote) and 2–4 answer options (e.g. "Capex · Opex · Ask the controller").
The learner can respond in any way, and every response feeds the tutor policy + mastery (BKT):
- say it ("capex", "the second one", "B", in any supported language) → matched to an option;
- click/tap an option (or press 1–4);
- say/click "I don't know" → Claros explains with the expert's words + moment, lowers mastery for that decision, gives the hint ladder next time;
- close the card → counts as "skip"; don't re-ask this decision in this case; repeated dismissals → Claros talks less ("just watch" mode), says so once;
- just act in the app before answering → the action IS the answer (implicit response): right → brief "Yes — that's what Anna does", wrong → explain; guardrail → stop BEFORE submit;
- ask back ("why?", "show me what Anna did", "what's next?") → answer from the map, open the reference.
Beyond the happy path:
- learner diverges from the expert's path: allowed orders (partial order) are fine silently; an unknown path that breaks no guardrail → one light check ("Anna does X here — is this on purpose?"); answer + action recorded as a NOVEL CASE → offered to the expert as a request ("Lea did Y on a Z invoice — is that OK?");
- experts differ on the decision → both options shown as valid, attributed; no "wrong";
- low-confidence step match → Claros asks "Are you doing X?" instead of assuming;
- learner already past the decision when the prompt is ready → turn it into a quick post-hoc check, never a stale question;
- learner talking to someone else / silence → no nudges; nudges respect the same pause gate as capture;
- learner too fast toward Submit with a violation → hard stop intervention wins over everything.
End: short summary — decisions handled unaided / after a hint / caught, and "practice next".
Grading honesty (the Teach bar is a case the expert NEVER showed): "right" is decided on the learner's own record from
the expert's rules (case facts, not the expert's demo value); field rules and the decision model must agree; when the
record doesn't settle it Claros says so ("can't tell from this screen — here's how Anna decides") and lets the real
action + guardrails decide. Never tell a learner they are wrong when they might be right.
Novices (no mastery yet) get the worked example first — the expert's moment + words — and then still make THEIR
decision on the card; prediction-only once mastery grows; "just watch" when mastered.

## UI principles
- Big type, few words. Every screen has one obvious primary action.
- Wide screens: use the width (two-pane layouts), don't center a narrow column.
- Screenshots are evidence: always large enough to read, click to zoom (fluid shared-element animation).
- Buttons press DOWN into their shadow on hover (neobrutalism default), never lift.

---

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
