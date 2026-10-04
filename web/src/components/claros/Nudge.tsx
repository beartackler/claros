"use client";

/**
 * Learner nudges (v2.1): at a decision point Claros speaks a nudge and the companion shows a NUDGE CARD —
 * the expert's reference (big, zoomable) + 2–4 big options + "I don't know". Voice, click, keys 1–4 or
 * closing the card all answer (`nudge_response`); `nudge_result` gives the feedback.
 * Priority: hard-stop guardrail (intervene) > nudge > nothing.
 * A mock driver (no server / ?demo=1 / ?nudge=…) produces the same messages for demos and screenshots.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, CircleHelp, Maximize2, OctagonAlert, Split, X } from "lucide-react";
import type { InterveneMsg, NudgeMsg, NudgeResultMsg, Quote, ServerMsg, Step, WorkMap } from "@/lib/contracts";
import { now } from "@/lib/clock";
import { getSocket } from "@/lib/ws";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useUi, type UiLang } from "./i18n";
import { ExpertAvatar, QuoteBlock, ScreenThumb } from "./primitives";
import { ZoomShot } from "./Lightbox";
import { expertById, firstName, guardrailsFor, quotesFor, stepHighlight } from "./mapUtils";
import { useLiveStore } from "./live";

/* ---------------- state ---------------- */

export type NudgeState = {
  nudge: NudgeMsg | null;
  picked: { choice_id: string | null; dont_know?: boolean } | null;
  result: NudgeResultMsg | null;
  stop: InterveneMsg | null;
};
const EMPTY: NudgeState = { nudge: null, picked: null, result: null, stop: null };

export function useNudges({ mock, map }: { mock: boolean; map: WorkMap | null }) {
  const log = useLiveStore((s) => s.serverLog);
  const [st, setSt] = useState<NudgeState>(EMPTY);
  const lastSeen = useRef<ServerMsg | null>(null);
  const counts = useRef({ answered: [] as { step_id: string; outcome: NudgeResultMsg["outcome"] }[] });

  useEffect(() => {
    const fresh: ServerMsg[] = [];
    for (let i = log.length - 1; i >= 0 && log[i] !== lastSeen.current; i--) fresh.unshift(log[i]);
    lastSeen.current = log.at(-1) ?? null;
    for (const m of fresh) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (m.type === "nudge") setSt((s) => ({ ...s, nudge: m, picked: null, result: null }));
      else if (m.type === "nudge_result")
        setSt((s) => {
          if (s.nudge?.id !== m.id) return s;
          if (s.nudge.step_id) counts.current.answered.push({ step_id: s.nudge.step_id, outcome: m.outcome });
          return { ...s, result: m };
        });
      else if (m.type === "intervene") setSt((s) => ({ ...s, stop: m, nudge: null, picked: null, result: null })); // hard stop wins
    }
  }, [log]);

  // correct → brief confirmation, then the card folds away by itself
  useEffect(() => {
    const o = st.result?.outcome;
    if (!st.result || st.result.show_reference || !(o === "correct" || o === "implicit_correct" || o === "skipped")) return;
    const h = setTimeout(() => setSt((s) => (s.result === st.result ? { ...s, nudge: null, picked: null, result: null } : s)), o === "skipped" ? 0 : 2400);
    return () => clearTimeout(h);
  }, [st.result]);

  const respond = useCallback(
    (via: "click" | "key" | "close", choice_id: string | null, dont_know = false) => {
      const n = st.nudge;
      if (!n || st.picked) return;
      getSocket()?.send({ type: "nudge_response", id: n.id, via, choice_id, dont_know, t: now() });
      if (via === "close") {
        setSt((s) => ({ ...s, nudge: null, picked: null, result: null }));
        if (n.step_id) counts.current.answered.push({ step_id: n.step_id, outcome: "skipped" });
        return;
      }
      setSt((s) => ({ ...s, picked: { choice_id, dont_know } }));
      if (mock) setTimeout(() => useLiveStore.getState().pushServer(mockResult(n, choice_id, dont_know)), 650);
    },
    [st.nudge, st.picked, mock],
  );
  const dismiss = useCallback(() => setSt((s) => ({ ...s, nudge: null, picked: null, result: null })), []);
  const clearStop = useCallback(() => setSt((s) => ({ ...s, stop: null })), []);

  // keys: 1–4 pick, 0 / D = I don't know, Esc closes
  useEffect(() => {
    const n = st.nudge;
    if (!n || st.picked || st.stop) return;
    const onKey = (e: KeyboardEvent) => {
      if (document.getElementById("claros-lightbox")) return;
      if ((e.target as HTMLElement)?.closest?.("input,textarea,select")) return;
      const k = Number(e.key);
      if (k >= 1 && k <= n.options.length) respond("key", n.options[k - 1].id);
      else if (n.allow_dont_know && (e.key === "0" || e.key.toLowerCase() === "d")) respond("key", null, true);
      else if (e.key === "Escape") respond("close", null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [st.nudge, st.picked, st.stop, respond]);

  // eslint-disable-next-line react-hooks/refs
  return { ...st, respond, dismiss, clearStop, answered: counts.current.answered };
}

/* ---------------- the card ---------------- */

/** Whichever card wins right now: hard stop > nudge (with its result). Renders nothing when idle. */
export function CompanionCard({
  map,
  n,
  inPip,
  onStopDone,
  className,
}: {
  map: WorkMap;
  n: ReturnType<typeof useNudges>;
  inPip?: boolean;
  onStopDone?: () => void;
  className?: string;
}) {
  if (n.stop) return <StopCard map={map} msg={n.stop} onDone={() => (n.clearStop(), onStopDone?.())} inPip={inPip} className={className} />;
  if (n.nudge) return <NudgeCard key={n.nudge.id} map={map} n={n} inPip={inPip} className={className} />;
  return null;
}

function refFrames(map: WorkMap, step: Step | undefined, ids: string[] | undefined, caption: string) {
  const list = [...new Set((ids?.length ? ids : step?.moment?.keyframe_ids ?? []).filter(Boolean))];
  return (list.length ? list : [`synthetic-${step?.id ?? "x"}`]).map((id, i) => ({
    id,
    keyframeId: id.startsWith("synthetic-") ? null : id,
    seed: (step?.order ?? 1) + i,
    title: step?.title ?? "",
    highlight: step ? stepHighlight(step) : null,
    caption,
  }));
}

/** Reference shot: lightbox on the page; inside the PiP window it zooms in place (the lightbox lives in the main tab). */
function RefShot({ group, frames, inPip }: { group: string; frames: ReturnType<typeof refFrames>; inPip?: boolean }) {
  const { t } = useUi();
  const [big, setBig] = useState(false);
  if (!inPip) return <ZoomShot group={group} frames={frames} priority />;
  const f = frames[0];
  return (
    <button
      type="button"
      onClick={() => setBig((b) => !b)}
      aria-label={t("shot.zoom")}
      aria-expanded={big}
      className={cn("relative block w-full overflow-hidden rounded-base border-2 border-ink text-left", big ? "fixed inset-2 z-50 w-auto bg-ink p-1" : "cursor-zoom-in")}
    >
      <ScreenThumb keyframeId={f.keyframeId} title={f.title} highlight={f.highlight} seed={f.seed} rounded={false} className={cn("border-0", big && "h-full !aspect-auto [&_img]:object-contain")} eager />
      <span aria-hidden className="absolute right-2 bottom-2 grid size-9 place-items-center rounded-base border-2 border-ink bg-card text-ink">
        {big ? <X className="size-5" /> : <Maximize2 className="size-5" />}
      </span>
    </button>
  );
}

function refQuote(map: WorkMap, step: Step | undefined, n: NudgeMsg, lang: UiLang): Quote | null {
  const q = n.reference?.quote;
  if (q) {
    const speaker = map.experts.find((e) => e.name === q.speaker || firstName(e.name) === q.speaker);
    return {
      id: `nq-${n.id}`,
      speaker: q.speaker,
      speaker_id: speaker?.id ?? q.speaker,
      lang: q.lang as Quote["lang"],
      text: q.text,
      translations: (q.translation ? { [lang]: q.translation } : {}) as Quote["translations"],
      t: 0,
      session_id: "",
      source: "live",
      audio_clip: q.audio_clip ?? null,
    };
  }
  return step ? quotesFor(map, step.decision?.reason_quote_ids)[0] ?? null : null;
}

function NudgeCard({ map, n, inPip, className }: { map: WorkMap; n: ReturnType<typeof useNudges>; inPip?: boolean; className?: string }) {
  const { t, lang } = useUi();
  const msg = n.nudge!;
  const step = map.steps.find((s) => s.id === msg.step_id);
  const quote = refQuote(map, step, msg, lang);
  const who = quote ? expertById(map, quote.speaker_id) : expertById(map, step?.experts[0] ?? "");
  const name = firstName(who.name);
  const differ = Boolean(step?.conflict) || msg.options.some((o) => o.expert_id);
  const res = n.result;
  const good = res && (res.outcome === "correct" || res.outcome === "implicit_correct");
  const noted = res?.outcome === "noted"; // this record's facts don't settle it: the expert's reasoning, no verdict
  const showRef = msg.kind !== "confirm_step" && (!res || res.show_reference || !good);
  const frames = refFrames(map, step, msg.reference?.keyframe_ids, `${name} · ${step?.title ?? ""}`);
  const pickedId = n.picked?.choice_id;
  const pickedExpert = msg.options.find((o) => o.id === pickedId)?.expert_id;
  const fbName = pickedExpert ? firstName(expertById(map, pickedExpert).name) : name;
  const optionExpert = (o: NudgeMsg["options"][number]) => (o.expert_id ? expertById(map, o.expert_id) : null);

  return (
    <section aria-live="polite" aria-label={msg.question} className={cn("@container claros-enter rounded-[10px] border-[3px] border-ink bg-card shadow-[8px_8px_0_0_var(--claros)]", className)}>
      <header className="flex items-start gap-3 border-b-[3px] border-ink bg-claros p-4 text-claros-ink @lg:p-5">
        <h2 className="min-w-0 flex-1 text-2xl font-black leading-tight tracking-[-0.03em] @lg:text-3xl @3xl:text-4xl">{msg.question}</h2>
        {!res ? (
          <button
            type="button"
            onClick={() => n.respond("close", null)}
            aria-label={t("common.close")}
            className="press press-sm grid size-11 shrink-0 place-items-center rounded-base border-2 border-ink bg-card text-ink shadow-hard-sm"
          >
            <X className="size-6" aria-hidden />
          </button>
        ) : null}
      </header>

      <div className="space-y-4 p-4 @lg:space-y-5 @lg:p-5">
        {differ && !res ? (
          <p className="flex items-center gap-2 rounded-base border-2 border-ink bg-partial/40 px-3 py-2 text-lg font-bold">
            <Split className="size-5 shrink-0" aria-hidden /> {t("nudge.differ")}
          </p>
        ) : null}

        {res ? (
          <p
            role="status"
            className={cn(
              "claros-enter flex items-start gap-3 rounded-base border-2 border-ink p-4 text-2xl font-black leading-tight tracking-[-0.02em]",
              good ? "bg-ready text-on-fill" : noted ? "bg-card" : "bg-expert-soft",
            )}
          >
            {good ? <Check className="mt-0.5 size-7 shrink-0" aria-hidden /> : <CircleHelp className="mt-0.5 size-7 shrink-0" aria-hidden />}
            {res.feedback_spoken || (good ? t("nudge.correct", { name: fbName }) : noted ? t("nudge.noted", { name }) : t("nudge.explain", { name }))}
          </p>
        ) : null}

        <div className={cn("grid gap-4", showRef && "@3xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] @3xl:items-start")}>
          {showRef ? (
            <div className="min-w-0 space-y-3">
              <RefShot group={`nudge-${msg.id}`} frames={frames} inPip={inPip} />
              {quote ? <QuoteBlock quote={quote} compact={inPip} /> : null}
            </div>
          ) : null}

          {!res || !good ? (
            <div className="order-first min-w-0 @3xl:order-none">
              <ol className="grid gap-2.5">
                {msg.options.map((o, i) => {
                  const ex = optionExpert(o);
                  const picked = pickedId === o.id;
                  return (
                    <li key={o.id}>
                      <button
                        type="button"
                        onClick={() => n.respond("click", o.id)}
                        disabled={Boolean(n.picked)}
                        aria-pressed={picked}
                        className={cn(
                          "press flex min-h-16 w-full items-center gap-4 rounded-base border-2 border-ink px-4 py-3 text-left text-xl font-extrabold leading-snug shadow-hard @lg:text-2xl",
                          picked ? "bg-claros text-claros-ink" : "bg-card",
                          n.picked && !picked && "opacity-50",
                        )}
                      >
                        <span className={cn("tnum grid size-9 shrink-0 place-items-center rounded-[4px] border-2 border-ink font-mono text-lg font-black", picked ? "bg-card text-ink" : "bg-paper-2")}>{i + 1}</span>
                        <span className="min-w-0 flex-1 first-letter:uppercase">{o.label}</span>
                        {ex ? (
                          <span className="flex shrink-0 items-center gap-1.5 text-base font-bold">
                            <ExpertAvatar user={ex} size={28} index={Math.max(0, map.experts.findIndex((e) => e.id === ex.id))} />
                            <span className="hidden @md:inline">{firstName(ex.name)}</span>
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ol>
              {msg.allow_dont_know && !res ? (
                <Button variant="ghost" size="lg" className="mt-3 w-full text-ink-2" onClick={() => n.respond("click", null, true)} disabled={Boolean(n.picked)} aria-pressed={Boolean(n.picked?.dont_know)}>
                  <CircleHelp aria-hidden /> {t("nudge.dontKnow")}
                </Button>
              ) : null}
              {res && !good ? (
                <Button variant="primary" size="lg" className="mt-3 w-full" onClick={n.dismiss} autoFocus>
                  <Check aria-hidden /> {t("live.moment.close")}
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

/** Hard stop (guardrail) — highest priority, unmistakable: ink + coral, one sentence, the expert's moment. */
function StopCard({ map, msg, onDone, inPip, className }: { map: WorkMap; msg: InterveneMsg; onDone: () => void; inPip?: boolean; className?: string }) {
  const { t, lang } = useUi();
  const step = map.steps.find((s) => s.guardrail_ids.includes(msg.guardrail_id));
  const guard = step ? guardrailsFor(map, step).find((g) => g.id === msg.guardrail_id) ?? guardrailsFor(map, step)[0] : undefined;
  // the expert's original words as the server sent them (with their voice clip when consented)
  const qo = msg.quote_original;
  const mapQuote = guard ? (qo ? map.quotes.find((x) => x.id === qo.id) : undefined) ?? quotesFor(map, guard.quote_ids)[0] : undefined;
  const quote: Quote | undefined = qo
    ? {
        ...(mapQuote ?? { id: qo.id, speaker_id: map.experts.find((e) => e.name === qo.speaker)?.id ?? qo.speaker, translations: {} as Quote["translations"], t: 0, session_id: "", source: "live" as const }),
        speaker: qo.speaker,
        lang: qo.lang as Quote["lang"],
        text: qo.text,
        audio_clip: qo.audio_clip ?? mapQuote?.audio_clip ?? null,
      }
    : mapQuote;
  const who = quote ? expertById(map, quote.speaker_id) : expertById(map, guard?.experts[0] ?? "");
  const frames = refFrames(map, step, msg.moment?.keyframe_ids?.length ? msg.moment.keyframe_ids : guard?.evidence.flatMap((e) => e.keyframe_ids).slice(0, 1), `${firstName(who.name)} · ${step?.title ?? ""}`);
  const rule = (lang === "en" ? null : msg.rule) || guard?.text || msg.text;
  return (
    <section role="alert" className={cn("@container claros-enter rounded-[10px] border-[3px] border-ink bg-ink text-paper shadow-[8px_8px_0_0_var(--missing)]", className)}>
      <header className="flex items-center gap-4 p-4 @lg:p-6">
        <span className="grid size-16 shrink-0 place-items-center rounded-full border-[3px] border-paper bg-missing text-on-fill">
          <OctagonAlert className="size-9" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-4xl font-black leading-none tracking-[-0.04em] @lg:text-5xl">{t("nudge.stop")}</h2>
          <p className="mt-1 text-lg font-bold opacity-85">{t("live.moment.stop", { name: firstName(who.name) })}</p>
        </div>
      </header>
      <div className="space-y-4 border-t-[3px] border-paper/80 p-4 @lg:p-6">
        <p className="text-2xl font-extrabold leading-snug tracking-[-0.02em] @lg:text-3xl">{rule}</p>
        <div className="grid gap-4 @3xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] @3xl:items-start">
          <RefShot group={`stop-${msg.guardrail_id}`} frames={frames} inPip={inPip} />
          {quote ? <QuoteBlock quote={quote} compact={inPip} /> : null}
        </div>
        <Button variant="secondary" size="xl" className="w-full" onClick={onDone} autoFocus>
          <Check aria-hidden /> {t("live.moment.close")}
        </Button>
      </div>
    </section>
  );
}

/* ---------------- mock driver (contract-shaped) ----------------
 * For UI review without a server (?demo=1 / ?nudge=…). Everything comes from the loaded map — no demo values.
 * The mock has no learner record to judge, so it never claims an answer is right: choices come back "noted"
 * (except experts-differ, where every attributed way is valid). Real grading happens server-side per record. */

export type MockKind = "predict" | "diverge" | "confirm" | "differ" | "stop";
type TFn = ReturnType<typeof useUi>["t"];
const ALL_VALID = new Set<string>();

function mockQuote(map: WorkMap, ids: string[] | undefined, lang: UiLang) {
  const q = quotesFor(map, ids)[0];
  return q ? { text: q.text, speaker: firstName(q.speaker), lang: q.lang, translation: q.lang !== lang ? q.translations?.[lang] ?? null : null } : null;
}

export function mockNudge(kind: MockKind, map: WorkMap, lang: UiLang, t: TFn, clip?: string | null): ServerMsg | null {
  const id = `mock-${kind}-${Date.now().toString(36)}`;
  const steps = [...map.steps].sort((a, b) => a.order - b.order);
  const judged = steps.find((s) => s.decision?.kind === "judgment" && (s.decision.to_value || s.decision.from_value));
  const ref = (s: Step) => ({ keyframe_ids: s.moment?.keyframe_ids ?? [], quote: mockQuote(map, s.decision?.reason_quote_ids, lang) });
  if (kind === "stop") {
    const g = map.guardrails.find((x) => x.evidence.length) ?? map.guardrails[0];
    if (!g) return null;
    const q = map.quotes.find((x) => g.quote_ids.includes(x.id));
    return {
      type: "intervene",
      guardrail_id: g.id,
      text: g.text,
      moment: g.evidence[0] ?? { session_id: "", keyframe_ids: [], t: 0, utterance_ids: [] },
      quote_original: q ? { id: q.id, speaker: q.speaker, lang: q.lang, text: q.text, audio_clip: clip ?? q.audio_clip ?? null } : null,
    };
  }
  if (kind === "confirm") {
    const s = judged ?? steps[0];
    if (!s) return null;
    return {
      type: "nudge", id, step_id: s.id, kind: "confirm_step", question: t("nudge.confirm", { step: s.title }),
      options: [{ id: "yes", label: t("nudge.yes") }, { id: "no", label: t("nudge.no") }], allow_dont_know: false, reference: null,
    };
  }
  if (kind === "diverge") {
    const s = steps.find((x) => x.after.length);
    const missed = s ? map.steps.find((x) => x.id === s.after[0]) : undefined;
    if (!missed) return null;
    return {
      type: "nudge", id, step_id: missed.id, kind: "diverge",
      question: t("nudge.diverge", { name: firstName(expertById(map, missed.experts[0] ?? "").name), step: missed.title }),
      options: [{ id: "purpose", label: t("nudge.onPurpose") }, { id: "fix", label: t("nudge.doIt") }], allow_dont_know: false, reference: ref(missed),
    };
  }
  if (kind === "differ") {
    const s = steps.find((x) => x.conflict && x.variants.length);
    if (!s) return null;
    const varIds = new Set(s.variants.map((v) => v.expert_id));
    const base = s.experts.find((e) => !varIds.has(e));
    const ways = [...(base && s.decision ? [{ expert_id: base, text: s.decision.to_value || s.decision.description }] : []), ...s.variants.map((v) => ({ expert_id: v.expert_id, text: v.description }))];
    ALL_VALID.add(id);
    return {
      type: "nudge", id, step_id: s.id, kind: "predict", question: t("nudge.q", { step: s.title }),
      options: ways.slice(0, 4).map((w, i) => ({ id: `o${i + 1}`, label: w.text, expert_id: w.expert_id })), allow_dont_know: true, reference: ref(s),
    };
  }
  if (!judged?.decision) return null;
  const d = judged.decision;
  const ask = guardrailsFor(map, judged).find((g) => g.action === "stop_and_ask");
  const values = [d.to_value, d.from_value].filter((v, i, a): v is string => Boolean(v) && a.indexOf(v) === i);
  const options = [...values.map((v, i) => ({ id: `o${i + 1}`, label: v })), ...(ask ? [{ id: "ask", label: t("nudge.ask", { who: ask.owner || t("nudge.lead") }) }] : [])];
  if (options.length < 2) return null;
  return { type: "nudge", id, step_id: judged.id, kind: "predict", question: t("nudge.q", { step: judged.title }), options, allow_dont_know: true, reference: ref(judged) };
}

export function mockResult(n: NudgeMsg, choice: string | null, dontKnow: boolean): NudgeResultMsg {
  if (dontKnow) return { type: "nudge_result", id: n.id, outcome: "dont_know", feedback_spoken: "", show_reference: true };
  if (ALL_VALID.has(n.id) || n.kind === "diverge" || n.kind === "confirm_step")
    return { type: "nudge_result", id: n.id, outcome: "correct", feedback_spoken: "", show_reference: false };
  return { type: "nudge_result", id: n.id, outcome: "noted", feedback_spoken: "", show_reference: true };
}

/** Push one mock message through the same store path the server uses. */
export function pushMock(kind: MockKind, map: WorkMap, lang: UiLang, t: TFn) {
  const m = mockNudge(kind, map, lang, t);
  if (m) useLiveStore.getState().pushServer(m);
}
