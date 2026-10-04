# README demo assets

| File | What it shows |
| --- | --- |
| `workmap.gif` / `workmap.mp4` | Work Map: steps 2 → 3 → 5 ("Experts differ"), scroll the step detail, open a screenshot in the lightbox, close it |
| `learner.gif` / `learner.mp4` | Learner live session: predict card → answer → feedback with the expert's reference → guardrail **Stop** card |
| `expert.gif` / `expert.mp4` | Expert home → "Show Claros how you work" → start sequence (share → mic → Claros says hi) → live capture with captions + "How Claros decided" tags → debrief |
| `record.py` | Script that records and encodes all of the above |

GIFs: 1200 px wide, 15 fps, two-pass palette (each must stay under 6 MB). MP4s: 1440×900 H.264.

## Regenerate

You need the web dev server on `:3000`, the API on `:8787` (seeded: `POST /api/knowledge/seed` if the Work Map is empty) and `ffmpeg` on your PATH.

```bash
uv run --project server python docs/assets/record.py            # all three
uv run --project server python docs/assets/record.py learner    # or just one: workmap | learner | expert
```

How it works: Playwright records a 1440×900 video of each scenario, and ffmpeg trims the page-load lead-in and encodes the MP4 and GIF. The script doesn't change any app code. Instead it uses the app's own demo flags (`?nudge=predict|stop`, `?demo=1`, `?judge=1`) plus some code it injects into the browser:

- CSS that hides the Next.js dev indicator, and a drawn cursor (headless Chromium doesn't show one)
- a fake screen share and microphone, so no permission prompts appear
- a request for the voice token that never finishes, so no real voice session starts
- a session socket that never opens, so the capture view falls back to its built-in demo feed (which runs faster than the app's normal 2.6 s pace)
- the session-end call is answered locally, so the server doesn't build a blank map from an empty session
- unnamed scratch maps are filtered out of the map list (the same rule as the app's `isJunkWorkflow`), so "latest map" is always a real one
