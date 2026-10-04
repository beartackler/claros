"use client";

// Debrief "building your map" wait (~20–90 s). The server doesn't report stages, so the stage list
// advances on a timer (evenly over ~60 s, the last one holds until the map is ready) — no percentage.
// While waiting we show the expert's own screen moments from this capture; if that fetch fails the
// stage list stands alone.
import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { API_BASE, keyframeUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useT, type DictKey } from "./i18n";
import { ClarosDot } from "./primitives";

const STAGES: DictKey[] = ["db.build.s1", "db.build.s2", "db.build.s3", "db.build.s4"];
const STAGE_MS = 15_000;
const FRAME_MS = 5_000;
const MAX_FRAMES = 5;

type Moment = { keyframeId: string; summary: string };
type LogRow = { payload?: { keyframe_id?: string | null; summary?: string | null; kind?: string } };

/** Up to 5 distinct screen moments, spread across the session, actions preferred over navigation. */
function pickMoments(rows: LogRow[]): Moment[] {
  const seen = new Set<string>();
  const all: (Moment & { nav: boolean })[] = [];
  for (const r of rows) {
    const kf = r.payload?.keyframe_id;
    const summary = r.payload?.summary?.trim();
    if (!kf || !summary || seen.has(kf) || kf.startsWith("synthetic-")) continue;
    // only clean, real actions as captions: reverts, blanks and odd readings are screen-reading noise
    if (r.payload?.kind === "undo" || /∅|→\s*\S{0,2}\s+on |Dashboard|Getting Started/.test(summary) || summary.length > 110) continue;
    seen.add(kf);
    all.push({ keyframeId: kf, summary, nav: r.payload?.kind === "navigate" });
  }
  const actions = all.filter((m) => !m.nav);
  const pool = actions.length >= 3 ? actions : all;
  if (pool.length <= MAX_FRAMES) return pool;
  return Array.from({ length: MAX_FRAMES }, (_, i) => pool[Math.round((i * (pool.length - 1)) / (MAX_FRAMES - 1))]);
}

function useSessionMoments(sessionId: string): Moment[] {
  const [moments, setMoments] = useState<Moment[]>([]);
  useEffect(() => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);
    fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/log?kinds=screen.event`, { signal: ctrl.signal })
      .then((r) => (r.ok ? (r.json() as Promise<LogRow[]>) : []))
      .then((rows) => setMoments(Array.isArray(rows) ? pickMoments(rows) : []))
      .catch(() => {}) // silent: the stage list stands alone
      .finally(() => clearTimeout(timer));
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [sessionId]);
  return moments;
}

function useElapsed(): number {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setMs(Date.now() - t0), 1000);
    return () => clearInterval(id);
  }, []);
  return ms;
}

export function MapBuilding({ sessionId }: { sessionId: string }) {
  const t = useT();
  const elapsed = useElapsed();
  const moments = useSessionMoments(sessionId);
  const [broken, setBroken] = useState<Set<string>>(() => new Set());
  const shown = moments.filter((m) => !broken.has(m.keyframeId));
  const current = Math.min(Math.floor(elapsed / STAGE_MS), STAGES.length - 1);
  const frame = shown.length ? Math.floor(elapsed / FRAME_MS) % shown.length : 0;
  const prevFrame = shown.length > 1 ? (frame - 1 + shown.length) % shown.length : -1;
  const secs = Math.floor(elapsed / 1000);
  const clock = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;

  return (
    <div role="status" aria-live="polite" className={cn("grid items-start gap-12 pt-4", shown.length ? "grid-cols-[minmax(0,5fr)_minmax(0,6fr)]" : "max-w-3xl")}>
      <style>{CSS}</style>
      <div className="min-w-0">
        <div className="flex items-center gap-4">
          <ClarosDot size={44} speaking />
          <span className="tnum text-lg font-bold text-ink-2" aria-hidden>
            {clock}
          </span>
        </div>
        <h1 className="mt-8 text-6xl font-extrabold tracking-tight text-ink">{t("db.build.title")}</h1>
        <p className="mt-4 text-xl text-ink-2">{t("db.build.sub")}</p>

        <ol className="mt-10 space-y-3">
          {STAGES.map((key, i) => {
            const state = i < current ? "done" : i === current ? "now" : "next";
            return (
              <li
                key={key}
                aria-current={state === "now" ? "step" : undefined}
                className={cn(
                  "flex items-center gap-4 rounded-base border-2 px-5 py-4 transition-colors duration-500",
                  state === "now" && "mb-build-now border-ink bg-card shadow-claros",
                  state === "done" && "border-ink bg-card",
                  state === "next" && "border-ink/25 bg-transparent text-ink-2/70",
                )}
              >
                <span
                  className={cn(
                    "grid size-9 shrink-0 place-items-center rounded-[4px] border-2",
                    state === "done" && "border-ink bg-ready text-on-fill",
                    state === "now" && "border-ink bg-claros text-claros-ink",
                    state === "next" && "border-ink/25",
                  )}
                  aria-hidden
                >
                  {state === "done" ? (
                    <Check className="size-5" strokeWidth={3} />
                  ) : state === "now" ? (
                    <span className="flex gap-[3px]">
                      {[0, 1, 2].map((d) => (
                        <span key={d} className="mb-build-dot size-[5px] rounded-full bg-claros-ink" style={{ animationDelay: `${d * 160}ms` }} />
                      ))}
                    </span>
                  ) : (
                    <span className="tnum text-sm font-bold">{i + 1}</span>
                  )}
                </span>
                <span className={cn("text-xl", state === "now" ? "font-extrabold text-ink" : "font-bold")}>
                  {t(key)}
                  {state === "done" ? <span className="sr-only"> ✓</span> : null}
                </span>
              </li>
            );
          })}
        </ol>
      </div>

      {shown.length ? (
        <figure className="min-w-0">
          <figcaption className="mb-3 flex items-center justify-between gap-4 text-sm font-bold text-ink-2">
            <span>{t("db.build.from")}</span>
            <span className="flex gap-1.5" aria-hidden>
              {shown.map((m, i) => (
                <span key={m.keyframeId} className={cn("h-2 w-6 rounded-full border-2 border-ink transition-colors duration-500", i === frame ? "bg-claros" : "bg-card")} />
              ))}
            </span>
          </figcaption>
          <div className="relative aspect-[16/10] overflow-hidden rounded-base border-2 border-ink bg-[#f7f7f5] shadow-hard-lg">
            {shown.map((m, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={m.keyframeId}
                src={keyframeUrl(m.keyframeId)}
                alt=""
                draggable={false}
                onError={() => setBroken((b) => new Set(b).add(m.keyframeId))}
                className={cn(
                  "absolute inset-0 h-full w-full object-cover object-top",
                  // new frame fades in over the old one (which stays solid underneath): no muddy double-exposure
                  i === frame ? "mb-build-pan z-10 opacity-100 transition-opacity duration-700 ease-out" : i === prevFrame ? "z-0 opacity-100" : "opacity-0",
                )}
              />
            ))}
            <span className="mb-build-scan pointer-events-none absolute inset-x-0 top-0 h-16" aria-hidden />
          </div>
          <p key={shown[frame]?.keyframeId} className="ledger-tick mt-5 flex items-start gap-3 text-xl font-bold text-ink">
            <span className="mt-2 inline-block size-3 shrink-0 rounded-[2px] border-2 border-ink bg-claros" aria-hidden />
            <span className="line-clamp-2 min-w-0">{shown[frame]?.summary}</span>
          </p>
        </figure>
      ) : null}
    </div>
  );
}

const CSS = `
@keyframes mb-dot { 0%, 80%, 100% { transform: translateY(0); opacity: .45 } 40% { transform: translateY(-4px); opacity: 1 } }
.mb-build-dot { animation: mb-dot 1.1s ease-in-out infinite; }
@keyframes mb-scan { 0% { transform: translateY(-100%) } 100% { transform: translateY(900%) } }
.mb-build-scan {
  background: linear-gradient(to bottom, transparent, color-mix(in srgb, var(--claros) 16%, transparent) 70%, color-mix(in srgb, var(--claros) 55%, transparent) 98%, transparent);
  border-bottom: 2px solid var(--claros);
  animation: mb-scan 3.2s cubic-bezier(.45,0,.55,1) infinite;
}
@keyframes mb-pan { from { transform: scale(1.06) } to { transform: scale(1) } }
.mb-build-pan { animation: mb-pan 6s ease-out both; }
@keyframes mb-now { from { transform: translate(4px, 4px); box-shadow: none } to { transform: none } }
.mb-build-now { animation: mb-now 360ms cubic-bezier(.16,1,.3,1) both; }
@media (prefers-reduced-motion: reduce) {
  .mb-build-dot, .mb-build-pan, .mb-build-now { animation: none !important; }
  .mb-build-scan { display: none; }
}
`;
