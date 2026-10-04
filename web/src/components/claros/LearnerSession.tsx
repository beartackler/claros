"use client";

/**
 * Learner = a live voice companion over their own work. No course library, no quizzes.
 * Start (share → mic → Claros asks what you're doing) → live view (orb, captions, current step,
 * large expert-moment card when Claros steps in) → a short summary.
 */
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, CheckCircle2, Hand, MonitorUp, PictureInPicture2, PlayCircle, RotateCw, Send, Split, Square } from "lucide-react";
import { useUi } from "./i18n";
import { ClarosDot, Loading } from "./primitives";
import { LiveCaptions, LiveOrb, requestMic, useJoinSession, useLive, useLiveStore, waitVoice } from "./live";
import { StartSequence, type ShareResult } from "./StartSequence";
import { MomentCard, type MomentKind } from "./MomentCard";
import { CompanionCard, pushMock, useNudges, type MockKind } from "./Nudge";
import { PipPortal, usePip } from "@/voice/pip";
import { firstName, isConfirmed, sortedSteps } from "./mapUtils";
import { useDebug } from "./debug";
import { Button, buttonVariants } from "@/components/ui/button";
import { createRequest, createSession, getWorkflow, lookupWorkflow } from "@/lib/api";
import { EXPERT, LEA } from "@/lib/mock";
import { writeLocalMastery } from "@/lib/localMastery";
import { saveLastSession, type LastSession } from "@/lib/lastSession";
import type { Step, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

type Phase = "start" | "live" | "summary";
type Card = { kind: MomentKind; stepId: string; guardrailId?: string | null; keyframes?: string[] };

/** The whole learner experience. On Home it sits under the top bar with `below` (last session, requests). */
export function LearnerSession({ below }: { below?: React.ReactNode }) {
  return (
    <Suspense fallback={<Loading rows={2} />}>
      <Learn below={below} />
    </Suspense>
  );
}

function Learn({ below }: { below?: React.ReactNode }) {
  const { t, lang } = useUi();
  const params = useSearchParams();
  const nudgeParam = params.get("nudge") as MockKind | null;
  const demo = params.get("demo") === "1" || Boolean(nudgeParam);
  const directWf = params.get("wf");
  const debug = useDebug();

  const [sessionId, setSessionId] = useState<string | null>(null);
  const sessionP = useRef<Promise<string> | null>(null);
  // a session exists only once the learner presses Start (or types)
  const ensureSession = () =>
    (sessionP.current ??= createSession({ mode: "learn", user: LEA, lang }).then((r) => {
      setSessionId(r.data.session_id);
      return r.data.session_id;
    }));
  useEffect(() => {
    useLiveStore.getState().set({ transcript: [], caption: "", highlightedStepId: null, moment: null, openMapId: null });
  }, []);
  useJoinSession(sessionId, "learn", LEA, lang);
  const { capture, voice } = useLive(sessionId, "learn", lang, LEA.name);
  const voiceRef = useRef(voice);
  voiceRef.current = voice;

  const [phase, setPhase] = useState<Phase>(demo ? "live" : "start");
  const [map, setMap] = useState<WorkMap | null>(null);
  const [lookup, setLookup] = useState<"idle" | "finding" | "found" | "missing">("idle");
  const [intent, setIntent] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [summary, setSummary] = useState<LastSession | null>(null);
  const visited = useRef(new Set<string>());
  const needed = useRef(new Set<string>());

  const identify = useCallback(
    async (text: string) => {
      setIntent(text);
      setLookup("finding");
      const force = debug.enabled && debug.force !== "auto" ? debug.force : undefined;
      const r = await lookupWorkflow({ utterance: text || "walk me through this", lang, screen_state: null }, force);
      const match = force === "missing" ? null : r.data.match;
      if (!match?.workflow_id) return setLookup("missing");
      const m = await getWorkflow(match.workflow_id);
      setMap(m.data);
      setLookup("found");
    },
    [debug.enabled, debug.force, lang],
  );

  // the learner's first words identify the workflow (screen + speech, server-side lookup)
  const transcript = useLiveStore((s) => s.transcript);
  const firstUser = transcript.find((l) => l.role === "user");
  useEffect(() => {
    if (phase !== "live" || demo || lookup !== "idle" || !firstUser) return;
    void identify(firstUser.text);
  }, [phase, demo, lookup, firstUser, identify]);

  // the agent can open a map itself
  const openMapId = useLiveStore((s) => s.openMapId);
  useEffect(() => {
    const id = openMapId ?? directWf;
    if (!id || map?.workflow_id === id) return;
    getWorkflow(id).then((m) => {
      setMap(m.data);
      setLookup("found");
    });
  }, [openMapId, directWf, map?.workflow_id]);

  // current step (Claros points at it by voice)
  const highlighted = useLiveStore((s) => s.highlightedStepId);
  const steps = useMemo(() => (map ? sortedSteps(map) : []), [map]);
  const step: Step | undefined = steps.find((s) => s.id === highlighted) ?? (lookup === "found" ? steps[0] : undefined);
  useEffect(() => {
    if (step) visited.current.add(step.id);
  }, [step]);

  // nudges + hard stops (v2.1); replays (show_moment) open the expert-moment card
  const wsOpen = useLiveStore((s) => s.wsStatus === "open");
  const nudges = useNudges({ mock: demo || !wsOpen, map });
  const serverLog = useLiveStore((s) => s.serverLog);
  const last = serverLog.at(-1);
  useEffect(() => {
    if (!map || !last || last.type !== "show_moment" || !step) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCard({ kind: "how", stepId: step.id, keyframes: last.moment?.keyframe_ids });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last, map]);
  useEffect(() => {
    if (nudges.stop && map) {
      const s = map.steps.find((x) => x.guardrail_ids.includes(nudges.stop!.guardrail_id));
      if (s) needed.current.add(s.id);
    }
  }, [nudges.stop, map]);

  /* ---------- start sequence ---------- */
  const onShare = async (): Promise<ShareResult> => {
    const picking = capture.start(); // straight from the click: the window picker needs the gesture
    void ensureSession();
    await picking;
    if (!capture.stream.current) return "cancelled";
    const surface = (capture.stream.current.getVideoTracks()[0]?.getSettings() as { displaySurface?: string })?.displaySurface;
    return surface === "monitor" ? "monitor" : "ok";
  };
  const onVoice = async () => {
    await ensureSession();
    await new Promise((r) => setTimeout(r, 60)); // let the voice hook pick up the new session id
    try {
      await voiceRef.current.start();
    } catch {
      return false;
    }
    return waitVoice();
  };

  const end = () => {
    voice.end();
    capture.stop();
    for (const a of nudges.answered) if (a.outcome === "incorrect" || a.outcome === "dont_know" || a.outcome === "implicit_incorrect") needed.current.add(a.step_id);
    const own = steps.filter((s) => visited.current.has(s.id) && !needed.current.has(s.id));
    const practice = steps.filter((s) => needed.current.has(s.id));
    const s: LastSession = { workflow_id: map?.workflow_id ?? null, name: map?.name ?? intent, own: own.map((x) => x.title), practice: practice.map((x) => x.title), at: Date.now() };
    if (map) writeLocalMastery(map.workflow_id, Object.fromEntries([...own.map((x) => [x.id, "unaided"]), ...practice.map((x) => [x.id, "caught"])]));
    saveLastSession(s);
    setSummary(s);
    setPhase("summary");
  };

  /* ---------- demo script (?demo=1): a scripted session, no permissions ---------- */
  useDemoScript(demo && !nudgeParam && phase === "live", identify, map);
  // ?nudge=predict|diverge|confirm|differ|stop — one state, ready for review / screenshots
  useEffect(() => {
    if (!nudgeParam || phase !== "live") return;
    if (!map) {
      void identify("process a supplier invoice");
      return;
    }
    const st = useLiveStore.getState();
    const sid = { predict: "s4", diverge: "s3", confirm: "s2", differ: "s5", stop: "s6" }[nudgeParam] ?? "s1";
    st.set({ highlightedStepId: sid });
    st.pushTranscript({ id: "mock-a", role: "agent", text: lang === "ru" ? "Секунду — посмотрите сюда." : "One second — look at this.", t: Date.now() });
    pushMock(nudgeParam, map, lang);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nudgeParam, phase, map]);

  if (phase === "start")
    return (
      <>
      <StartSequence
        persona="learner"
        title={t("lh.title")}
        sub={t("lh.sub")}
        privacy="start.privacy.learner"
        onShare={onShare}
        onMic={requestMic}
        onVoice={onVoice}
        onDone={() => setPhase("live")}
        onType={(text) => {
          void ensureSession();
          setPhase("live");
          void identify(text);
        }}
      />
      {below}
      </>
    );

  if (phase === "summary" && summary) return <Summary s={summary} onAgain={() => window.location.reload()} />;

  return (
    <LiveView
      sharing={capture.active}
      voiceOn={voice.status === "connected"}
      speaking={voice.isSpeaking}
      getLevel={() => Math.max(voice.getOutputLevel(), voice.getInputLevel() * 0.5)}
      map={map}
      steps={steps}
      step={step}
      lookup={lookup}
      intent={intent}
      sessionId={sessionId}
      keyframe={capture.lastKeyframe ? `live_${capture.lastKeyframe.seq}` : null}
      card={card}
      nudges={nudges}
      onReplay={() => step && (needed.current.add(step.id), setCard({ kind: "how", stepId: step.id }))}
      onCloseCard={() => setCard(null)}
      onSay={(text) => (lookup === "idle" || lookup === "missing" ? identify(text) : voice.status === "connected" ? voice.sendText(text) : undefined)}
      onEnd={end}
      visited={visited.current}
    />
  );
}

/* ---------------- live view ---------------- */

function LiveView(p: {
  sharing: boolean;
  voiceOn: boolean;
  speaking: boolean;
  getLevel: () => number;
  map: WorkMap | null;
  steps: Step[];
  step?: Step;
  lookup: "idle" | "finding" | "found" | "missing";
  intent: string;
  sessionId: string | null;
  keyframe: string | null;
  card: Card | null;
  nudges: ReturnType<typeof useNudges>;
  onReplay: () => void;
  onCloseCard: () => void;
  onSay: (text: string) => void;
  onEnd: () => void;
  visited: Set<string>;
}) {
  const { t } = useUi();
  const cardStep = p.card && p.map ? p.map.steps.find((s) => s.id === p.card!.stepId) : undefined;
  const companion = Boolean(p.map && (p.nudges.stop || p.nudges.nudge));
  const split = companion || Boolean(cardStep) || p.lookup === "missing";
  const pip = usePip();
  const expertName = p.step ? firstName((p.map?.experts.find((e) => p.step!.experts.includes(e.id)) ?? p.map?.experts[0])?.name ?? "") : "";

  return (
    <div>
      <div className="mb-8 flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center gap-2 rounded-base border-2 border-ink bg-card px-3 py-1.5 text-base font-bold">
          <span className={cn("size-3 rounded-full border-2 border-ink", p.voiceOn ? "bg-ready motion-safe:animate-pulse" : "bg-paper-2")} aria-hidden />
          {p.voiceOn ? (p.speaking ? t("live.speaking") : t("live.listening")) : t("live.voiceOff")}
        </span>
        {p.sharing ? (
          <span className="inline-flex items-center gap-2 rounded-base border-2 border-ink bg-ready px-3 py-1.5 text-base font-bold text-on-fill">
            <MonitorUp className="size-5" aria-hidden /> {t("learn.sharing")}
          </span>
        ) : null}
        {pip.supported ? (
          <Button variant="outline" onClick={() => void pip.open({ width: 440, height: 620 })} className="ml-auto">
            <PictureInPicture2 aria-hidden /> <span className="hidden sm:inline">{t("live.popout")}</span>
          </Button>
        ) : null}
        <Button variant="secondary" onClick={p.onEnd} className={cn(!pip.supported && "ml-auto")}>
          <Square aria-hidden /> {t("live.end")}
        </Button>
      </div>

      <div className={cn("grid items-start gap-10", split ? "xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] xl:gap-14" : "")}>
        <div className={cn("min-w-0", !split && "grid items-center gap-8 lg:grid-cols-[auto_minmax(0,1fr)] lg:gap-16")}>
          <div className={cn(split ? "mb-6" : "justify-self-center")}>
            <LiveOrb size={split ? 120 : 240} getLevel={p.getLevel} speaking={p.speaking} off={!p.voiceOn} />
          </div>
          <div className="min-w-0">
            <LiveCaptions idle={p.lookup === "finding" ? t("live.finding") : t("live.waiting")} />

            {p.lookup === "finding" ? (
              <p className="mt-8 inline-flex items-center gap-3 text-xl font-bold text-ink-2" role="status">
                <ClarosDot size={22} speaking /> {t("live.finding")}
              </p>
            ) : null}

            {p.step && p.map ? (
              <div className="mt-10">
                <p className="text-base font-bold text-ink-2">{t("live.now")}</p>
                <p className="mt-1 flex items-start gap-3 text-2xl font-extrabold leading-snug tracking-[-0.02em] sm:text-3xl">
                  <span className="tnum mt-0.5 grid size-11 shrink-0 place-items-center rounded-base border-2 border-ink bg-ink font-mono text-xl font-black text-paper">{p.step.order}</span>
                  <span className="min-w-0">{p.step.title}</span>
                </p>
                <ol className="mt-4 flex gap-1.5" aria-hidden>
                  {p.steps.map((s) => (
                    <li key={s.id} className={cn("h-3 flex-1 rounded-[2px] border-2 border-ink", s.id === p.step!.id ? "bg-claros" : p.visited.has(s.id) ? "bg-ink" : "bg-card")} />
                  ))}
                </ol>
                {p.step.conflict ? (
                  <p className="mt-4 flex items-start gap-2 text-lg font-semibold">
                    <Split className="mt-1 size-5 shrink-0" aria-hidden /> {t("live.conflict")}
                  </p>
                ) : !isConfirmed(p.step) ? (
                  <p className="mt-4 text-lg font-semibold">{t("live.unconfirmed")}</p>
                ) : null}
                {!cardStep ? (
                  <Button variant="outline" size="lg" className="mt-6" onClick={p.onReplay}>
                    <PlayCircle aria-hidden /> {t("live.replay", { name: expertName })}
                  </Button>
                ) : null}
              </div>
            ) : null}

            <SayBox onSay={p.onSay} />
          </div>
        </div>

        {companion && p.map ? (
          <CompanionCard map={p.map} n={p.nudges} className="order-first xl:order-none" />
        ) : cardStep && p.map && p.card ? (
          <MomentCard key={`${p.card.stepId}-${p.card.kind}`} map={p.map} step={cardStep} kind={p.card.kind} guardrailId={p.card.guardrailId} keyframes={p.card.keyframes} onClose={p.onCloseCard} className="order-first xl:order-none" />
        ) : p.lookup === "missing" ? (
          <Missing intent={p.intent} sessionId={p.sessionId} keyframe={p.keyframe} />
        ) : null}
      </div>

      {/* the floating companion (Document PiP): the only thing that can sit over the learner's app */}
      <PipPortal pipWindow={pip.pipWindow}>
        <div className="flex min-h-screen flex-col gap-3 bg-paper p-3 text-ink">
          {companion && p.map ? (
            <CompanionCard map={p.map} n={p.nudges} inPip />
          ) : (
            <div className="flex items-center gap-3 rounded-base border-2 border-ink bg-card p-3 shadow-hard">
              <LiveOrb size={56} getLevel={p.getLevel} speaking={p.speaking} off={!p.voiceOn} />
              <div className="min-w-0 flex-1">
                <LiveCaptions idle={t("live.waiting")} className="[&>p:first-child]:text-xl [&>p:first-child]:sm:text-xl" />
                {p.step ? <p className="mt-2 truncate text-base font-bold text-ink-2">{p.step.order}. {p.step.title}</p> : null}
              </div>
            </div>
          )}
        </div>
      </PipPortal>
    </div>
  );
}

function SayBox({ onSay }: { onSay: (text: string) => void }) {
  const { t } = useUi();
  const [text, setText] = useState("");
  return (
    <form
      className="mt-10 flex max-w-xl gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!text.trim()) return;
        onSay(text.trim());
        setText("");
      }}
    >
      <label htmlFor="say" className="sr-only">
        {t("live.say.ph")}
      </label>
      <input
        id="say"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={t("live.say.ph")}
        className="h-11 min-w-0 flex-1 rounded-base border-2 border-ink bg-card px-3 text-base placeholder:text-ink-2 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros"
      />
      <Button type="submit" variant="outline" size="icon" aria-label={t("live.say")} disabled={!text.trim()} className="size-11">
        <Send aria-hidden />
      </Button>
    </form>
  );
}

function Missing({ intent, sessionId, keyframe }: { intent: string; sessionId: string | null; keyframe: string | null }) {
  const { t } = useUi();
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const ask = async () => {
    setBusy(true);
    await createRequest({
      workflow_hint: intent || "Workflow on this screen",
      requested_by: LEA,
      moment: sessionId ? { session_id: sessionId, keyframe_ids: keyframe ? [keyframe] : [], t: Date.now(), utterance_ids: [] } : null,
    });
    setBusy(false);
    setSent(true);
  };
  return (
    <section className="claros-enter rounded-[10px] border-[3px] border-ink bg-card p-6 shadow-[8px_8px_0_0_var(--claros)] sm:p-10">
      <h2 className="text-4xl font-black leading-[1.02] tracking-[-0.04em] sm:text-5xl">{t("live.missing.title")}</h2>
      <p className="mt-4 text-xl text-ink-2">{t("live.missing.sub")}</p>
      <div className="mt-8">
        {sent ? (
          <p className="claros-enter inline-flex items-center gap-3 rounded-base border-2 border-ink bg-ready px-4 py-3 text-xl font-extrabold text-on-fill" role="status">
            <CheckCircle2 className="size-7" aria-hidden /> {t("live.missing.asked")}
          </p>
        ) : (
          <Button variant="claros" size="xl" onClick={ask} loading={busy} autoFocus>
            {!busy ? <Hand aria-hidden /> : null} {t("live.missing.ask")}
          </Button>
        )}
      </div>
      {sent ? (
        <p className="mt-4 text-base font-semibold text-ink-2">
          {firstName(EXPERT.name)} · {intent}
        </p>
      ) : null}
    </section>
  );
}

/* ---------------- summary ---------------- */

function Summary({ s, onAgain }: { s: LastSession; onAgain: () => void }) {
  const { t } = useUi();
  return (
    <div>
      <h1 className="text-5xl font-black leading-[0.98] tracking-[-0.045em] sm:text-7xl">{t("sum.title")}</h1>
      {s.name ? <p className="mt-4 text-2xl font-semibold text-ink-2">{s.name}</p> : null}
      <div className="mt-12 grid gap-8 lg:grid-cols-2 lg:gap-12">
        <SummaryList title={t("sum.own")} items={s.own} tone="ready" />
        <SummaryList title={t("sum.practice")} items={s.practice} tone="claros" />
      </div>
      <div className="mt-12 flex flex-wrap gap-4">
        <Button variant="claros" size="xl" onClick={onAgain}>
          <RotateCw aria-hidden /> {t("sum.again")}
        </Button>
        <Link href="/" className={buttonVariants({ variant: "secondary", size: "xl" })}>
          {t("sum.home")} <ArrowRight aria-hidden />
        </Link>
      </div>
    </div>
  );
}

function SummaryList({ title, items, tone }: { title: string; items: string[]; tone: "ready" | "claros" }) {
  return (
    <section className={cn("rounded-[10px] border-[3px] border-ink p-6 shadow-hard-lg sm:p-8", tone === "ready" ? "bg-ready/40" : "bg-claros-soft")}>
      <h2 className="flex items-baseline justify-between gap-4 text-3xl font-black tracking-[-0.03em]">
        {title}
        <span className="tnum text-6xl leading-none">{items.length}</span>
      </h2>
      {items.length ? (
        <ul className="mt-6 space-y-3">
          {items.map((x) => (
            <li key={x} className="flex items-start gap-3 text-xl font-bold leading-snug">
              {tone === "ready" ? <Check className="mt-1 size-6 shrink-0" aria-hidden /> : <ArrowRight className="mt-1 size-6 shrink-0" aria-hidden />}
              {x}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/* ---------------- demo script ---------------- */

function useDemoScript(on: boolean, identify: (text: string) => Promise<void>, map: WorkMap | null) {
  const { lang } = useUi();
  const mapRef = useRef(map);
  mapRef.current = map;
  useEffect(() => {
    if (!on) return;
    const st = useLiveStore.getState();
    const ru = lang === "ru";
    const say = (role: "agent" | "user", text: string) => st.pushTranscript({ id: `demo-${Math.random().toString(36).slice(2)}`, role, text, t: Date.now() });
    const nudge = (k: MockKind) => mapRef.current && pushMock(k, mapRef.current, lang);
    const steps: [number, () => void][] = [
      [300, () => say("agent", ru ? "Привет, Лея. Над чем работаете?" : "Hi Lea. What are you working on?")],
      [2200, () => say("user", ru ? "Провожу счёт поставщика." : "Booking this supplier invoice.")],
      [2600, () => void identify("process a supplier invoice")],
      [4400, () => (st.set({ highlightedStepId: "s1" }), say("agent", ru ? "Это я знаю. Откройте следующий черновик счёта." : "I know this one. Open the next draft invoice."))],
      [8000, () => (st.set({ highlightedStepId: "s2" }), nudge("confirm"))],
      [14000, () => (st.set({ highlightedStepId: "s4" }), say("agent", ru ? "Строка 2 — станок за 5 200. Какой счёт?" : "Line 2 is a 5,200 machine. Which account?"), nudge("predict"))],
    ];
    const timers = steps.map(([ms, fn]) => setTimeout(fn, ms));
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [on]);
}
