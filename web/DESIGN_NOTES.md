# Claros web UI — design notes

## World
Neobrutalist "apprentice's notebook". Paper-cream ground, ink 2px borders, hard offset shadows (no blur),
6px radius. Dark mode is the same notebook at night: ink and paper swap, fills deepen so labels stay legible.
Theme follows the system by default; the account menu pins Light/Dark (`claros.theme`, applied pre-paint in `layout.tsx`).

## Tokens (`app/globals.css`)
Claros palette is exposed as Tailwind colors: `paper paper-2 card ink ink-2 claros claros-soft claros-ink
expert expert-soft ready partial missing danger on-fill`, shadows `shadow-hard-sm / hard / hard-lg / claros`.
The neobrutalism.dev registry tokens (`--main`, `--background`, `--border`…) are mapped onto these, so
registry components drop in without restyling.

- **Violet (`claros`) = Claros is speaking / asking / summoned.** Walk-me-through, Answer by voice, intervention.
- Coverage: `ready` green, `partial` amber (hatched), `missing` coral — always icon + word.
- `expert` blue = an expert's own words and judgment calls.
- Text on bright fills uses `on-fill` (never flips in dark).

## Component layer (`components/ui`, neobrutalism.dev registry, Base UI — `render` prop, not asChild)
Added from the registry: dropdown-menu, navigation-menu, sheet, drawer, breadcrumb, popover, hover-card,
collapsible, accordion, skeleton, empty, kbd. Extended:
- **Button** hierarchy: `primary` (ink slab, violet shadow) · `claros` (summons Claros only) · `secondary`
  (card + shadow) · `outline` · `ghost` · `destructive` · `link`; sizes xs→xl + icon sizes; `loading` prop
  (spinner, aria-busy, blocks clicks). Hover lifts 1px, press sinks into the shadow, focus = 3px violet ring,
  disabled = flat 45%. Legacy `default/neutral` kept for `voice/` and `/dev`.
- **Badge** variants: ready / partial / missing / expert / claros / ink / tag (mono, for app + O*NET data) / dashed.
- Dropdowns, tooltips get hard shadows; inputs get violet focus + AA placeholder.

## Shell (`components/claros/Shell.tsx`)
Top bar: wordmark · primary nav (NavigationMenu: Home, Walk me through, Workflows; active = ink slab) ·
language dropdown · role menu (avatar + name; Expert/Learner radio + theme). Phone: menu button opens a
left Sheet with the nav. Inner pages get breadcrumbs (`crumbs` prop). Capture and debrief use **focus
mode** (`focus` prop): wordmark, session label, language, Exit — no nav.
Demo controls (force coverage) live only in a hidden panel shown with `?debug=1` (`debug.tsx`).

## Information architecture
`/inbox` redirects to `/`. Role decides Home.

**Expert home** — demand first:
1. Requests from learners (learner, what they tried, their screen moment, age, *Record it now* / *Later*;
   "Later" collapses into "Saved for later" with undo).
2. Questions waiting for you — open unknowns + expert conflicts across their maps; *Answer by voice*
   opens a debrief session for that workflow; *See on map* deep-links `?step=…&focus=conflict`.
3. Your workflows — coverage health bars (evidence / decisions / rules), contributors, open questions.
4. Secondary: Teach Claros something new.

**Learner home** — get unstuck now, then keep going:
1. Hero "Walk me through this" (violet).
2. Continue learning — per-workflow mastery strip + "Practice next" (deep-links `/learn?wf=…&step=…`).
   Server mastery wins; this browser's runs are kept in localStorage so progress shows immediately.
3. Workflows you can learn — cards with coverage + app chips; filters derived from data (apps, coverage).
4. Your requests — Asked → Recorded → Ready.

`/map` is the Workflows library; `/learn?wf=` enters the learning loop directly (skips lookup).

## Work Map (`app/map/[workflowId]`)
- App-agnostic header: name, coverage, version, app chips from `map.apps`, O*NET code + task, experts,
  coverage meters; actions Learn this (learner) / Answer by voice (expert) + Export dropdown.
- "Experts differ at step N" banners are buttons → open that step at its conflict.
- Sticky toolbar: filter tabs **All · Judgment calls · Guardrails · Conflicts · Unconfirmed** (with counts) +
  "How to read this" legend popover + mini-map toggle.
- Primary view: vertical step list in partial order (`stepGroups`: longest path over `after`); steps at the
  same depth render as a dashed **Any order** block. Each step is a card with a stretched button (hover lift,
  focus ring, "Open ›") and typed chips — Judgment call (Scale, blue), Guardrail (OctagonAlert, ink),
  Experts differ (Split, amber), Not confirmed (CircleDashed, dashed), Background (BookOpen). Chips are real
  buttons that open the same panel scrolled to (and flashing) that item.
- Detail panel: Sheet on ≥768px, Drawer below. Screen moment flipbook, decision (from → to, would change if),
  reason quotes (original + translation, ▶ if clip), guardrails, variants/conflict, cited background,
  approval switch (expert). Prev/next step.
- Mini-map: sticky thumbnail rail (desktop) / strip in the toolbar (phone), same order as the list,
  current step tracked by IntersectionObserver, click to jump.
- `highlight_step` from the voice agent scrolls to and flashes the card and follows in an open panel.

## Learn, capture, debrief
Same components; all behaviors kept (phase routing to debrief, highlight_step in the loop, say queue in
`voice/`). Debrief corrections come from where the expert stops the teach-back (no canned text).
Screen-moment fallback is a neutral app window — no app-specific copy anywhere.

## i18n
`components/claros/dict.ts`: `en` and `ru` complete (`ru` is typed `Record<DictKey,string>` so the
compiler enforces it); `de/fr/es` cover the shell and fall back to `en`.
