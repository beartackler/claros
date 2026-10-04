# Claros web UI — design notes (v2)

Source of truth: `docs/PRODUCT.md` v2 / v2.1 and `docs/CONTRACTS.md` (v2.1 nudges, v2.2 evidence).
Desktop first (1440 / 1920). Light only. Big type, few words, one obvious primary action per screen.

## World
Neobrutalist "apprentice's notebook": paper-cream ground, ink 2px borders, hard offset shadows, 6px radius.
Violet (`claros`) = Claros is speaking / asking / summoned. `expert` blue = an expert's own words.
`ready` green / hatched amber = Ready / Needs a second run (one map status, never "open questions").

## Type
`globals.css` remaps the Tailwind scale: body 18px (`text-base`), `text-sm` 16, `text-xs` 13, headings
up to 96px. Captions over instructions; every paragraph is one short line or gone.

## Pressables (neobrutalism.dev default)
`.press` (globals.css): a slab sits on a hard shadow and presses DOWN by the shadow size on hover and
`:active` — never lifts. Open menus (`data-popup-open`), toggled (`aria-pressed`) and current items stay
pressed in. Sizes: `.press-sm` 2px, `.press` 4px, `.press-lg` 6px, `.press-xl` 8px. Cards with a
stretched link/button use `.press-within`. Used by every Button variant, chips, step cards, filters,
dropdown/select triggers, Resume, lightbox controls.

## Components (structural, from `src/components/ui`, Base UI `render` prop)
Button, Badge (status, steps, request stages), Card (map + request cards), Avatar (ExpertAvatar),
Input, Textarea, Select (speak language), DropdownMenu (language, role, Export), Sheet/Drawer (step
detail < 1024px, mobile nav), Tooltip, Toast, Switch, Label — all retuned to the Claros tokens
(ink borders, card bg, hard shadows, 16–18px text). The lightbox is the one custom overlay (media
viewer with shared-element zoom; keyboard + focus trap built in).

## Layout
Fluid container up to 1600px (`CONTAINER` in Shell), 40px gutters on desktop. Two panes everywhere it
helps: homes, start sequence, live views, debrief, Work Map.

## Journeys
- **Shell**: role switch in the user menu; each role sees only its own nav (Expert: Home, Work maps;
  Learner: none — Home is the session). The same menu holds the judge toggle "Show how Claros decided".
- **Start** (`StartSequence`): ONE "Start" → 1 Share your app window → 2 Allow microphone → 3 Claros
  greets you (voice). Visible per-step states: waiting / in progress / done / blocked-with-the-fix:
  picker cancelled, macOS Screen Recording blocked (restart hint), whole screen or a tab (pick again /
  continue), mic blocked (browser + macOS steps), no mic, voice didn't connect, server waking / offline.
  Privacy in two short lines above the button. No typed path — voice is the product.
- **Expert home**: giant "Show Claros how you work" slab → "Learners are waiting on" (their screen
  moment, one-tap Record it) → "Your work maps" (big thumbnail + Ready / Needs a second run).
- **Capture**: status row (recording, timer, question dots, Pop out, Strike that, Off the record,
  Done — debrief) → big orb + live captions → three lines of what Claros noticed. Phase routing to
  debrief unchanged.
- **Off the record (honest)**: off by voice or tap; back ONLY by Resume (page, PiP orb, or R). Claros
  says so when going off, mutes, frames stop, dark orb, ResumeBar; reminder after 3 min, then every 5.
  "Strike that" → `control strike_that`. Implemented in `voice/useClarosVoice.ts` (say-queue gate).
- **Debrief**: Questions (one big question + its screen moment, voice / type / skip) → Teach-back
  (step list highlighted, big decision + screenshot, That's right / Correct this) → Publish map.
- **Work Map** (≥1024px app layout): the page never scrolls; the title block collapses into a compact
  header as soon as either pane scrolls; step list (left) and detail (right) scroll independently
  (`overscroll-behavior: contain`). Status "Confirmed by Anna & Marco · debrief"; unconfirmed steps say
  "Not confirmed yet — Claros will ask in the next run". Each quote appears once; guardrails link back
  ("Anna's words ↑"). Disagreements are one note on the step. No open-questions list, no approval
  toggles, no predicates. Export holds one quiet item: "For agents (SKILL.md)".
- **Learner**: Home = Start hero + Your last session + Your requests. Live = orb, captions, current
  step; nudge card (v2.1: options 1–4, I don't know, close, experts-differ, confirm-step, diverge) and
  hard-stop card take the right pane and the PiP companion; replay = large expert-moment card. Honest
  "Claros hasn't seen this yet → Ask an expert". End = big summary (on your own / practice next).

## Voice clips (consent-first)
Capture start has one Switch "Let new hires hear my voice clips" (default off) → `hello.consent.voice_clips`; the socket
joins when Start is pressed so consent rides in `hello`. `useVoiceClips` records the mic (webm/opus) and cuts one clip
per final user utterance → `POST /api/sessions/{sid}/clips`; nothing while off the record, buffer dropped on "strike that".
Quotes play their clip (▶ in QuoteBlock) only when `audio_clip` / `intervene.quote_original.audio_clip` is present.

## Matching
Learner lookup sends `session_id` (server reads that session's live screen). Merged requests show
"Lea + 2 others" (expert) and "3 learners asked" (learner).

## Screenshots
Every screen moment is a `ZoomShot` (`Lightbox.tsx`): click → shared-element zoom (motion `layoutId`),
Esc / backdrop closes, ← → walks frames, swipe on touch, reduced-motion respected.

## Evidence ("How Claros decided", off by default; user menu or `?judge=1`)
`Evidence.tsx`: live signals bar (typing / speaking / screen / gate, from ws `signals`, local fallback),
`why` tag under each question (`say`/`ask.why`), "Looked it up instead of asking you · source" list
(`looked_up`) in capture and debrief — this one is always visible; signals and `why` are judge-only.

## Review hooks (no permissions needed)
`/learn?demo=1` scripted session · `/learn?nudge=stop&clip=/api/clips/<file>` stop card with a clip ·
`?debug=1` exposes `window.__clarosSend` for smoke tests · `/learn?nudge=predict|differ|diverge|confirm|stop` one nudge state ·
`/capture/<id>?demo=1` live capture · `?judge=1` evidence on · `?debug=1` force coverage.

## i18n
`dict.ts`: en + ru complete (ru typed against en); de/fr/es cover the shell + off-the-record copy.
