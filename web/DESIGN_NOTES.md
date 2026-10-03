# Claros web UI — design notes (owner: web-ui)

## World
Neobrutalist "apprentice's notebook". Paper-cream ground, ink-black 2px borders, hard 4px offset
shadows (no blur — the world chose it), square-ish 6px radius. Everything is flat and honest: the
product's promise is "never invent rules", so the UI never decorates over uncertainty.

## Color roles (tokens in `components/claros/claros.css`)
- `--ink` black, `--paper` cream, `--card` white.
- **`--claros` (electric violet) = Claros is speaking / asking / intervening. Only that.** Anything
  violet is Claros's voice: the orb, the intervention card, the "why Claros asked" inspector, the
  CTA that summons it. Nothing else gets violet.
- Coverage: `--ready` green, `--partial` amber, `--missing` coral. Always paired with an icon +
  word (never color alone).
- `--expert` mustard = an expert's own words (quotes, attribution avatars).
- Off-the-record = full ink inversion (black ground, cream text) — impossible to miss.

## Surfaces
| Route | Mode | Primary job | Hero element |
|---|---|---|---|
| `/` | Operate | pick role, start fast | Learner: one huge violet "Walk me through this"; Expert: demand inbox first |
| `/learn` | Operate | get unstuck on *this* screen | state machine invoke→share→lookup→ready/partial/missing |
| `/capture/[id]` | Operate | work + talk, be interrupted rarely | live notebook; budget meter; Off-the-record slab |
| `/debrief/[id]` | Operate | close gaps fast | moment keyframe center, ledger counter draining to 0 |
| `/map/[id]` | Read | trust + approve | filmstrip + 3 lanes (steps / judgment / guardrails) |
| `/inbox` | Operate | accept demand | request list w/ learner screen moment |

## Friction encoded in UI
- Invoke: one button / one key (`Space` on /learn idle).
- Capture: questions budget meter visible (≤3–5 / 10 min); "saved for debrief" counter shows Claros
  is *holding back*.
- Debrief: ledger number is the biggest type on screen; progress drains to 0.
- Learner prediction: answer before reveal (desirable difficulty); "show me" is secondary.
- Guardrail: intervention card blocks the step until acknowledged.
- Unconfirmed in PARTIAL: hatched stripe + "Not confirmed yet" label, never a guessed rule.
- Conflicts: "Experts differ here" banner with both reasons, side by side, "ask your lead".

## Screen moments
Keyframes load from `/api/keyframes/{id}.jpg`; if missing (offline/mock) a synthetic `ScreenThumb`
SVG draws an ERP-like form with the decision field highlighted, so the story still reads.

## i18n
`lib/i18n.ts`: flat dictionary per lang (en, ru complete; de/fr/es partial → fall back to en).
`useT()` hook; language picker in the top bar persists to localStorage.

## Data
`lib/api.ts` fetches `NEXT_PUBLIC_CLAROS_API` (default http://localhost:8787) with 2.5s timeout and
falls back to `lib/mock.ts` (AP invoice workflow, two experts Sabine (de) + Dmitri (ru)). Pages show
a small "demo data" chip when on mocks.

## A11y / motion
Visible 3px violet focus ring offset 2px; `prefers-reduced-motion` kills drains/flips; all
interactive targets ≥40px; phone width: lanes stack, filmstrip scrolls horizontally.
