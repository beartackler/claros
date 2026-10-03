"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  BookmarkPlus,
  Check,
  CircleDot,
  Eye,
  EyeOff,
  HelpCircle,
  Mic,
  MicOff,
  MonitorUp,
  Square,
} from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { LANGS, LANG_NAMES, useUi, type DictKey, type UiLang } from "@/components/claros/i18n";
import { ClarosDot, ClarosSays, EmptyState, Meter, Panel, SectionTitle } from "@/components/claros/primitives";
import { sendControl, useJoinSession, useLive, useLiveStore, useMicCheck } from "@/components/claros/live";
import { ClarosCompanion } from "@/voice/ClarosCompanion";
import { endSession } from "@/lib/api";
import { EXPERT, MOCK_EVENTS, MOCK_UNKNOWNS } from "@/lib/mock";
import type { ScreenEvent, Unknown } from "@/lib/contracts";
import { cn } from "@/lib/utils";

const BUDGET = 5;

export default function CapturePage() {
  return (
    <Shell wide>
      <Capture />
    </Shell>
  );
}

function Capture() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { lang } = useUi();
  const [speakLang, setSpeakLang] = useState<UiLang>(lang);
  const [live, setLive] = useState(false);
  useJoinSession(live ? sessionId : null, "capture", EXPERT, speakLang);
  const { capture, voice } = useLive(live ? sessionId : null, "capture", speakLang, EXPERT.name);

  return live ? (
    <LiveNotebook sessionId={sessionId} capture={capture} voice={voice} />
  ) : (
    <Preflight
      speakLang={speakLang}
      setSpeakLang={setSpeakLang}
      sharing={capture.active}
      surface={capture.surface}
      onShare={() => capture.start().catch(() => {})}
      onStart={() => {
        setLive(true);
        // voice starts after the session is joined; degrade silently without a server
        setTimeout(() => voice.start().catch(() => {}), 50);
      }}
    />
  );
}

/* ---------------- pre-flight ---------------- */

function Preflight(p: {
  speakLang: UiLang;
  setSpeakLang: (l: UiLang) => void;
  sharing: boolean;
  surface: string;
  onShare: () => void;
  onStart: () => void;
}) {
  const { t } = useUi();
  const mic = useMicCheck();
  const [consent, setConsent] = useState(false);
  const list = (k: DictKey) => t(k).split("|");
  return (
    <div className="mx-auto max-w-4xl">
      <ClarosSays>
        <h1 className="text-4xl font-black leading-[0.95] tracking-[-0.04em]">{t("capture.preflight.title")}</h1>
        <p className="mt-3 max-w-[56ch] text-[var(--ink-2)]">{t("capture.preflight.sub")}</p>
      </ClarosSays>

      <div className="mt-8 grid gap-4 md:grid-cols-2">
        <Panel className="p-4">
          <p className="flex items-center gap-2 font-extrabold">
            <Mic className="size-5" aria-hidden /> {t("capture.preflight.mic")}
          </p>
          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              onClick={mic.check}
              className={cn("h-10 rounded-[4px] border-2 border-[var(--ink)] px-3 text-sm font-bold", mic.state === "ok" ? "bg-[var(--ready)]" : "bg-white hover:bg-[var(--paper-2)]")}
            >
              {mic.state === "ok" ? (
                <span className="inline-flex items-center gap-1.5">
                  <Check className="size-4" aria-hidden /> {t("capture.preflight.mic.ok")}
                </span>
              ) : (
                t("capture.preflight.mic.test")
              )}
            </button>
            <div className="h-3 flex-1 rounded-[3px] border-2 border-[var(--ink)] bg-white" aria-hidden>
              <div className="h-full bg-[var(--claros)] transition-[width] duration-75" style={{ width: `${Math.round(mic.level * 100)}%` }} />
            </div>
          </div>
          {mic.state === "denied" ? <p className="mt-2 text-sm font-semibold text-[#9b1c1c]">{t("capture.preflight.mic.denied")}</p> : null}
        </Panel>

        <Panel className="p-4">
          <p className="flex items-center gap-2 font-extrabold">
            <MonitorUp className="size-5" aria-hidden /> {t("capture.preflight.window")}
          </p>
          <p className="mt-1 text-sm text-[var(--ink-2)]">{t("capture.preflight.window.sub")}</p>
          <button
            type="button"
            onClick={p.onShare}
            className={cn("mt-3 h-10 rounded-[4px] border-2 border-[var(--ink)] px-3 text-sm font-bold", p.sharing ? "bg-[var(--ready)]" : "bg-white hover:bg-[var(--paper-2)]")}
          >
            {p.sharing ? `✓ ${p.surface}` : t("learn.invoke.share")}
          </button>
          {p.sharing && p.surface === "monitor" ? (
            <p className="mt-2 text-sm font-bold">↑ {t("capture.preflight.window")}</p>
          ) : null}
        </Panel>

        <Panel className="p-4">
          <p className="flex items-center gap-2 font-extrabold">
            <Eye className="size-5" aria-hidden /> {t("capture.preflight.captured")}
          </p>
          <ul className="mt-2 space-y-1.5 text-sm">
            {list("capture.preflight.captured.list").map((x) => (
              <li key={x} className="flex gap-2">
                <Check className="mt-0.5 size-4 shrink-0" aria-hidden /> {x}
              </li>
            ))}
          </ul>
        </Panel>
        <Panel tone="ink" className="p-4">
          <p className="flex items-center gap-2 font-extrabold">
            <EyeOff className="size-5" aria-hidden /> {t("capture.preflight.never")}
          </p>
          <ul className="mt-2 space-y-1.5 text-sm">
            {list("capture.preflight.never.list").map((x) => (
              <li key={x} className="flex gap-2">
                <Ban className="mt-0.5 size-4 shrink-0" aria-hidden /> {x}
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mt-6 flex flex-col gap-4 rounded-[6px] border-2 border-[var(--ink)] bg-white p-4 shadow-[var(--hard)] sm:flex-row sm:items-center">
        <label className="flex items-center gap-2 text-sm font-bold">
          {t("capture.preflight.lang")}
          <select value={p.speakLang} onChange={(e) => p.setSpeakLang(e.target.value as UiLang)} className="h-9 rounded-[4px] border-2 border-[var(--ink)] bg-white px-2">
            {LANGS.map((l) => (
              <option key={l} value={l}>
                {LANG_NAMES[l]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-1 items-start gap-2 text-sm font-semibold">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5 size-5 accent-[var(--claros)]" />
          {t("capture.preflight.consent")}
        </label>
        <button
          type="button"
          disabled={!consent}
          onClick={p.onStart}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--claros)] px-6 font-black text-white shadow-[var(--hard)] transition-[transform,box-shadow] enabled:hover:translate-x-1 enabled:hover:translate-y-1 enabled:hover:shadow-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          <CircleDot className="size-5" aria-hidden /> {t("capture.preflight.start")}
        </button>
      </div>
    </div>
  );
}

/* ---------------- live notebook ---------------- */

type Live = ReturnType<typeof useLive>;

function useDemoDrip(enabled: boolean) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const h = setInterval(() => setN((x) => (x >= MOCK_EVENTS.length ? x : x + 1)), 2600);
    return () => clearInterval(h);
  }, [enabled]);
  return n;
}

function LiveNotebook({ sessionId, capture, voice }: { sessionId: string; capture: Live["capture"]; voice: Live["voice"] }) {
  const { t } = useUi();
  const router = useRouter();
  const wsStatus = useLiveStore((s) => s.wsStatus);
  const liveEvents = useLiveStore((s) => s.events);
  const ledger = useLiveStore((s) => s.ledger);
  const caption = useLiveStore((s) => s.caption);
  const offRecord = useLiveStore((s) => s.offRecord);
  const agentMode = useLiveStore((s) => s.agentMode);
  const phase = useLiveStore((s) => s.phase);
  const demo = wsStatus !== "open" && liveEvents.length === 0;

  // Server moves the session to debrief (voice "I'm done", end_task, or POST end) → follow it.
  useEffect(() => {
    if (phase === "debrief") router.push(`/debrief/${encodeURIComponent(sessionId)}`);
  }, [phase, router, sessionId]);
  const drip = useDemoDrip(demo && !offRecord);

  // Collect unknowns seen via ledger.top (live) or mock ledger (demo).
  const [seen, setSeen] = useState<Record<string, Unknown>>({});
  useEffect(() => {
    const top = ledger?.top;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (top) setSeen((s) => ({ ...s, [top.id]: top }));
  }, [ledger?.top]);

  const events: ScreenEvent[] = demo ? MOCK_EVENTS.slice(0, drip) : liveEvents;
  const unknowns: Unknown[] = demo
    ? MOCK_UNKNOWNS.filter((u) => u.about_event_ids.some((id) => events.some((e) => e.id === id)))
    : Object.values(seen);
  const asked = unknowns.filter((u) => u.status === "asked" || u.status === "answered");
  const saved = demo ? unknowns.filter((u) => u.status === "deferred").length : ledger?.saved_for_later ?? 0;
  const [inspect, setInspect] = useState<string | null>(null);
  const inspected = unknowns.find((u) => u.id === inspect) ?? null;

  const [elapsed, setElapsed] = useState(0);
  const start = useRef<number | null>(null);
  useEffect(() => {
    start.current ??= Date.now();
    const h = setInterval(() => setElapsed(Date.now() - (start.current ?? Date.now())), 1000);
    return () => clearInterval(h);
  }, []);
  const mmss = `${String(Math.floor(elapsed / 60000)).padStart(2, "0")}:${String(Math.floor(elapsed / 1000) % 60).padStart(2, "0")}`;

  const toggleOff = () => {
    const on = !offRecord;
    if (voice.status === "connected") voice.setOffRecord(on);
    else {
      useLiveStore.getState().set({ offRecord: on });
      sendControl(on ? "off_record_on" : "off_record_off");
    }
  };

  const finish = async () => {
    // keep the voice session alive: the same conversation continues into the debrief
    capture.stop();
    await endSession(sessionId);
    router.push(`/debrief/${sessionId}`);
  };

  const feedRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    feedRef.current?.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="relative">
      {/* status strip */}
      <div className={cn("mb-5 flex flex-wrap items-center gap-3 rounded-[6px] border-2 border-[var(--ink)] p-3 shadow-[var(--hard)]", offRecord ? "bg-[var(--ink)] text-[var(--paper)]" : "bg-white")}>
        <span className={cn("inline-flex items-center gap-2 font-extrabold", offRecord && "opacity-80")}>
          <span className={cn("size-3 rounded-full border-2", offRecord ? "border-[var(--paper)]" : "border-[var(--ink)] bg-[#e5332a] motion-safe:animate-pulse")} aria-hidden />
          {offRecord ? t("capture.offrecord.on") : t("capture.live")}
        </span>
        <span className="tnum font-mono text-sm">{mmss}</span>
        <span className="font-mono text-xs opacity-70">{sessionId}</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => (voice.status === "connected" ? voice.end() : voice.start().catch(() => {}))}
            aria-pressed={voice.status === "connected"}
            className={cn("inline-flex h-10 items-center gap-2 rounded-[4px] border-2 px-3 text-sm font-bold", offRecord ? "border-[var(--paper)]" : "border-[var(--ink)]", voice.status === "connected" ? "bg-[var(--claros)] text-white" : "")}
          >
            {voice.status === "connected" ? <Mic className="size-4" aria-hidden /> : <MicOff className="size-4" aria-hidden />}
            {t("capture.voice")}
          </button>
          <button
            type="button"
            onClick={finish}
            className={cn("inline-flex h-10 items-center gap-2 rounded-[4px] border-2 px-3 text-sm font-bold", offRecord ? "border-[var(--paper)] bg-[var(--paper)] text-[var(--ink)]" : "border-[var(--ink)] bg-[var(--ink)] text-[var(--paper)]")}
          >
            <Square className="size-4" aria-hidden /> {t("capture.end")}
          </button>
        </div>
      </div>

      {caption && !offRecord ? (
        <div className="claros-enter mb-5 rounded-[6px] border-2 border-[var(--ink)] bg-[var(--claros)] p-3 text-white shadow-[var(--hard)]" aria-live="polite">
          <ClarosSays speaking={agentMode === "speaking"}>
            <p className="pt-0.5 font-bold">{caption}</p>
          </ClarosSays>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        <section aria-labelledby="nb">
          <SectionTitle aside={demo ? <span className="font-mono text-[11px] font-semibold">{t("data.demo")}</span> : null}>
            <span id="nb">{t("capture.notebook")}</span>
          </SectionTitle>
          <Panel className={cn("p-0", offRecord && "opacity-40")}>
            <p className="border-b-2 border-[var(--ink)] px-4 py-2 text-xs font-bold uppercase tracking-wide">{t("capture.events")}</p>
            {events.length === 0 ? (
              <div className="p-4">
                <EmptyState>{t("capture.empty.events")}</EmptyState>
              </div>
            ) : (
              <ol ref={feedRef} className="scroll-thin max-h-[52vh] divide-y-2 divide-dashed divide-[var(--ink)]/30 overflow-y-auto" aria-live="polite">
                {events.map((e) => {
                  const qs = unknowns.filter((u) => u.about_event_ids.includes(e.id));
                  return (
                    <li key={e.id} className="claros-enter px-4 py-3">
                      <div className="flex items-start gap-3">
                        <span className="tnum mt-0.5 w-12 shrink-0 font-mono text-xs text-[var(--ink-2)]">{fmtT(e.t)}</span>
                        <span className="mt-0.5 shrink-0 rounded-[3px] border-2 border-[var(--ink)] bg-[var(--paper-2)] px-1.5 font-mono text-[10px] font-bold uppercase">{e.kind}</span>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-semibold leading-snug">{e.summary}</p>
                          {e.old || e.new ? (
                            <p className="mt-1 font-mono text-xs">
                              <span className="line-through decoration-2">{e.old}</span> → <span className="bg-[var(--claros-soft)] px-1 font-bold">{e.new}</span>
                            </p>
                          ) : null}
                          {qs.length ? (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {qs.map((u) => (
                                <UnknownPill key={u.id} u={u} active={inspect === u.id} onClick={() => setInspect(u.id)} />
                              ))}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </Panel>
        </section>

        <aside className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Panel className="p-3">
              <p className="text-xs font-bold uppercase tracking-wide">{t("capture.asked")}</p>
              <p className="tnum mt-1 text-4xl font-black">{asked.length}</p>
            </Panel>
            <Panel tone="paper" className="p-3">
              <p className="flex items-center gap-1 text-xs font-bold uppercase tracking-wide">
                <BookmarkPlus className="size-3.5" aria-hidden /> {t("capture.saved")}
              </p>
              <p key={saved} className="ledger-tick tnum mt-1 text-4xl font-black">{saved}</p>
            </Panel>
          </div>
          <p className="-mt-2 text-xs text-[var(--ink-2)]">{t("capture.saved.sub")}</p>

          <Panel className="p-3">
            <Meter value={asked.length / BUDGET} tone="claros" label={`${t("capture.budget")} · ${t("capture.budget.sub", { used: asked.length, max: BUDGET })}`} />
          </Panel>

          <Panel className="border-[var(--claros)] p-4 shadow-[var(--hard-claros)]">
            <p className="flex items-center gap-2 font-extrabold">
              <HelpCircle className="size-5 text-[var(--claros)]" aria-hidden /> {t("capture.why")}
            </p>
            {inspected ? <WhyInspector u={inspected} /> : (
              <>
                <p className="mt-2 text-sm text-[var(--ink-2)]">{t("capture.why.none")}</p>
                {unknowns.length ? (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {unknowns.map((u) => (
                      <UnknownPill key={u.id} u={u} active={false} onClick={() => setInspect(u.id)} />
                    ))}
                  </div>
                ) : null}
              </>
            )}
          </Panel>


          <button
            type="button"
            onClick={toggleOff}
            aria-pressed={offRecord}
            className={cn(
              "flex w-full items-center justify-center gap-3 rounded-[8px] border-[3px] border-[var(--ink)] py-6 text-2xl font-black tracking-[-0.02em] shadow-[var(--hard-lg)] transition-[transform,box-shadow] hover:translate-x-1.5 hover:translate-y-1.5 hover:shadow-none",
              offRecord ? "bg-[var(--paper)] text-[var(--ink)]" : "bg-[var(--ink)] text-[var(--paper)]",
            )}
          >
            {offRecord ? <Eye className="size-7" aria-hidden /> : <EyeOff className="size-7" aria-hidden />}
            {offRecord ? t("capture.offrecord.resume") : t("capture.offrecord")}
          </button>
        </aside>
      </div>
      <ClarosCompanion voice={voice} />
    </div>
  );
}

function fmtT(ms: number) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const STATUS_STYLE: Record<string, string> = {
  asked: "bg-[var(--claros)] text-white",
  answered: "bg-[var(--claros)] text-white",
  deferred: "bg-[var(--paper-2)]",
  resolved: "bg-white border-dashed",
  open: "bg-white",
  dropped: "bg-white opacity-60",
};

function UnknownPill({ u, active, onClick }: { u: Unknown; active: boolean; onClick: () => void }) {
  const { t } = useUi();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn("inline-flex max-w-full items-center gap-1.5 rounded-[3px] border-2 border-[var(--ink)] px-2 py-0.5 text-xs font-bold", STATUS_STYLE[u.status], active && "outline-3 outline-offset-1 outline-[var(--claros)]")}
    >
      {u.status === "asked" ? <ClarosDot size={10} /> : null}
      <span className="truncate">{u.spoken_question ?? u.entity ?? u.type}</span>
      <span className="font-mono text-[10px] font-semibold opacity-80">· {t(("scope." + u.scope) as DictKey)}</span>
    </button>
  );
}

function WhyInspector({ u }: { u: Unknown }) {
  const { t } = useUi();
  const gate = useMemo(() => {
    if (u.status === "resolved") return t("capture.why.gate.resolved", { source: u.resolution_source?.split(":")[0] ?? "llm" });
    if (u.status === "deferred") return t("capture.why.gate.deferred");
    return t("capture.why.gate.pause", { s: 2.4 });
  }, [u, t]);
  const costsExpert = u.scope === "company" || u.scope === "personal_judgment";
  return (
    <dl className="mt-3 space-y-2.5 text-sm">
      {u.spoken_question ? <p className="font-bold">“{u.spoken_question}”</p> : null}
      <div className="flex justify-between gap-3">
        <dt className="font-semibold text-[var(--ink-2)]">{t("capture.why.scope")}</dt>
        <dd className={cn("rounded-[3px] border-2 border-[var(--ink)] px-1.5 text-xs font-bold", costsExpert ? "bg-[var(--expert)]" : "bg-[var(--paper-2)]")}>{t(("scope." + u.scope) as DictKey)}</dd>
      </div>
      {u.hypothesis ? (
        <div>
          <dt className="font-semibold text-[var(--ink-2)]">{t("capture.why.hypothesis")}</dt>
          <dd className="mt-0.5">
            {u.hypothesis} <span className="tnum font-mono text-xs">({Math.round((u.hypothesis_confidence ?? 0) * 100)}%)</span>
          </dd>
        </div>
      ) : null}
      <div>
        <dt className="mb-1 font-semibold text-[var(--ink-2)]">{t("capture.why.priority")}</dt>
        <dd>
          <Meter value={u.priority} label={u.type} tone="claros" />
        </dd>
      </div>
      <div>
        <dt className="font-semibold text-[var(--ink-2)]">{t("capture.why.gate")}</dt>
        <dd className="mt-0.5 font-mono text-xs">{gate}</dd>
      </div>
      {u.resolution ? <p className="rounded-[3px] border-2 border-dashed border-[var(--ink)] p-2 text-xs">{u.resolution}</p> : null}
    </dl>
  );
}
