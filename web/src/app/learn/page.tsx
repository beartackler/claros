"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { AlertOctagon, ArrowRight, Check, CheckCircle2, Hand, Map as MapIcon, Mic, MonitorUp, RotateCw, ShieldAlert, X } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import {
  AppChip,
  AvatarStack,
  ClarosDot,
  ClarosSays,
  CoverageChip,
  Flipbook,
  Loading,
  OnetTag,
  Panel,
  QuoteBlock,
  ScreenThumb,
  SourceNote,
} from "@/components/claros/primitives";
import { expertById, firstName, guardrailsFor, isConfirmed, quotesFor, shuffle, sortedSteps, stepHighlight } from "@/components/claros/mapUtils";
import { useJoinSession, useLive, useLiveStore } from "@/components/claros/live";
import { ConflictBanner } from "@/components/claros/ConflictBanner";
import { LEVEL_BG } from "@/components/claros/cards";
import { useDebug } from "@/components/claros/debug";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createRequest, createSession, getWorkflow, lookupWorkflow, type Source } from "@/lib/api";
import { EXPERT, LEA, MOCK_DISTRACTORS } from "@/lib/mock";
import { writeLocalMastery } from "@/lib/localMastery";
import type { Coverage, LookupResponse, MasteryNode, Step, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

type Phase = "invoke" | "lookup" | "result" | "loop" | "mastery";

export default function LearnPage() {
  return (
    <Shell crumbs={[{ key: "crumb.learn" }]}>
      <Suspense fallback={<Loading rows={2} />}>
        <Learn />
      </Suspense>
    </Shell>
  );
}

function Learn() {
  const { t, lang } = useUi();
  const params = useSearchParams();
  const directWf = params.get("wf");
  const startStep = params.get("step");
  const debug = useDebug();
  const [phase, setPhase] = useState<Phase>(directWf ? "lookup" : "invoke");
  const [intent, setIntent] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [lookup, setLookup] = useState<LookupResponse | null>(null);
  const [source, setSource] = useState<Source>();
  const [map, setMap] = useState<WorkMap | null>(null);
  const [mastery, setMastery] = useState<Record<string, MasteryNode["level"]>>({});
  const [loopStart, setLoopStart] = useState<string | null>(startStep);

  useJoinSession(sessionId, "learn", LEA, lang);
  const { capture, voice } = useLive(sessionId, "learn", lang, LEA.name);

  const status: Coverage["status"] = lookup?.match?.coverage.status ?? "missing";

  const ensureSession = async () => {
    if (sessionId) return sessionId;
    const s = await createSession({ mode: "learn", user: LEA, lang });
    setSessionId(s.data.session_id);
    return s.data.session_id;
  };

  const run = async () => {
    setPhase("lookup");
    await ensureSession();
    const force = debug.enabled && debug.force !== "auto" ? debug.force : undefined;
    const [r] = await Promise.all([
      lookupWorkflow({ utterance: intent || "walk me through this", lang, screen_state: null }, force),
      new Promise((ok) => setTimeout(ok, 1100)), // let the lookup read as a deliberate moment
    ]);
    const res = force && r.source === "live" && r.data.match ? { ...r.data, match: { ...r.data.match, coverage: { ...r.data.match.coverage, status: force } } } : r.data;
    setLookup(force === "missing" ? { match: null, onet: r.data.onet } : res);
    setSource(r.source);
    if (res.match?.workflow_id && force !== "missing") {
      const m = await getWorkflow(res.match.workflow_id);
      setMap(m.data);
    }
    setPhase("result");
  };

  // Direct entry from a workflow card / "Practice next": skip the lookup, go straight to the map.
  const didDirect = useRef(false);
  useEffect(() => {
    if (!directWf || didDirect.current) return;
    didDirect.current = true;
    (async () => {
      await ensureSession();
      const m = await getWorkflow(directWf);
      setMap(m.data);
      setSource(m.source);
      setLookup({ match: { workflow_id: m.data.workflow_id, score: 1, coverage: m.data.coverage }, onet: m.data.onet });
      setPhase(startStep ? "loop" : "result");
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [directWf]);

  const share = async () => {
    try {
      await capture.start();
    } catch {
      /* user cancelled — still allow lookup by intent */
    }
  };

  return (
    <div>
      {phase !== "invoke" || capture.active || source ? (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {capture.active ? (
              <span className="inline-flex items-center gap-1.5 rounded-[4px] border-2 border-ink bg-ready px-2 py-0.5 text-xs font-bold text-on-fill">
                <MonitorUp className="size-3.5" aria-hidden /> {t("learn.sharing")}
              </span>
            ) : null}
            <SourceNote source={source} />
          </div>
          {phase !== "invoke" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setPhase("invoke");
                setLookup(null);
                setMap(null);
                setMastery({});
                setLoopStart(null);
              }}
            >
              <RotateCw aria-hidden /> {t("learn.end")}
            </Button>
          ) : null}
        </div>
      ) : null}

      {phase === "invoke" && (
        <Invoke
          intent={intent}
          setIntent={setIntent}
          sharing={capture.active}
          preview={capture.lastKeyframe?.url}
          onShare={share}
          onGo={run}
          voiceOn={voice.status === "connected"}
          onVoice={() => (voice.status === "connected" ? voice.end() : voice.start().catch(() => {}))}
        />
      )}
      {phase === "lookup" && <Lookup preview={capture.lastKeyframe?.url} />}
      {phase === "result" && status === "missing" && (
        <Missing lookup={lookup} intent={intent} sessionId={sessionId} keyframe={capture.lastKeyframe ? `live_${capture.lastKeyframe.seq}` : null} />
      )}
      {phase === "result" && status !== "missing" && map && <Found status={status} map={map} onStart={() => setPhase("loop")} />}
      {phase === "loop" && map && (
        <Loop
          map={map}
          partial={status === "partial"}
          startAt={loopStart}
          onDone={(m) => {
            setMastery(m);
            writeLocalMastery(map.workflow_id, m);
            setPhase("mastery");
          }}
          onAnswerVoice={voice.status === "connected" ? (txt) => voice.sendText(txt) : undefined}
        />
      )}
      {phase === "mastery" && map && (
        <Mastery
          map={map}
          levels={mastery}
          onPractice={(id) => {
            setLoopStart(id);
            setPhase("loop");
          }}
        />
      )}
    </div>
  );
}

/* ---------------- invoke ---------------- */

function Invoke(p: {
  intent: string;
  setIntent: (s: string) => void;
  sharing: boolean;
  preview?: string;
  onShare: () => void;
  onGo: () => void;
  voiceOn: boolean;
  onVoice: () => void;
}) {
  const { t } = useUi();
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div>
        <ClarosSays speaking>
          <h1 className="text-4xl font-black leading-[0.95] tracking-[-0.045em] sm:text-5xl">{t("learn.invoke.title")}</h1>
        </ClarosSays>
        <p className="mt-4 max-w-[52ch] leading-relaxed text-ink-2">{t("learn.invoke.sub")}</p>

        <form
          className="mt-8 space-y-5"
          onSubmit={(e) => {
            e.preventDefault();
            p.onGo();
          }}
        >
          <div className="flex flex-wrap gap-3">
            <Button type="button" variant={p.sharing ? "outline" : "secondary"} size="lg" onClick={p.onShare} className={cn(p.sharing && "bg-ready text-on-fill hover:bg-ready")}>
              {p.sharing ? <Check aria-hidden /> : <MonitorUp aria-hidden />}
              {p.sharing ? t("learn.sharing") : t("learn.invoke.share")}
            </Button>
            <Button type="button" variant={p.voiceOn ? "claros" : "outline"} size="lg" onClick={p.onVoice} aria-pressed={p.voiceOn}>
              <Mic aria-hidden />
              {p.voiceOn ? t("learn.invoke.voiceOn") : t("learn.invoke.voice")}
            </Button>
          </div>
          <div>
            <label htmlFor="intent" className="mb-1.5 block text-sm font-bold">
              {t("learn.invoke.intent")}
            </label>
            <div className="flex flex-col gap-3 sm:flex-row">
              <Input id="intent" value={p.intent} onChange={(e) => p.setIntent(e.target.value)} placeholder={t("learn.invoke.intent.ph")} className="h-12 min-w-0 flex-1 bg-card text-base" />
              <Button type="submit" variant="claros" size="lg">
                {t("learn.start")} <ArrowRight aria-hidden />
              </Button>
            </div>
          </div>
        </form>
      </div>
      <div>
        {p.preview ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={p.preview} alt="" className="aspect-[16/10] w-full rounded-base border-2 border-ink object-cover object-top shadow-hard" />
        ) : (
          <div className="hatch grid aspect-[16/10] place-items-center rounded-base border-2 border-dashed border-ink p-6 text-center">
            <div>
              <MonitorUp className="mx-auto size-8" aria-hidden />
              <p className="mt-2 max-w-[32ch] text-sm font-semibold">{t("learn.invoke.preview")}</p>
              <p className="mt-1 max-w-[36ch] text-xs text-ink-2">{t("capture.preflight.window.sub")}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Lookup({ preview }: { preview?: string }) {
  const { t } = useUi();
  return (
    <div className="mx-auto max-w-xl py-12 text-center" role="status" aria-live="polite">
      <div className="mx-auto w-fit">
        <ClarosDot size={72} speaking />
      </div>
      <h1 className="mt-6 text-3xl font-black tracking-[-0.03em]">{t("learn.lookup")}</h1>
      <p className="mt-2 text-ink-2">{t("learn.lookup.sub")}</p>
      {preview ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={preview} alt="" className="mx-auto mt-6 aspect-[16/10] w-72 rounded-[4px] border-2 border-ink object-cover object-top" />
      ) : null}
    </div>
  );
}

/* ---------------- result states ---------------- */

function Found({ status, map, onStart }: { status: Coverage["status"]; map: WorkMap; onStart: () => void }) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const confirmed = steps.filter(isConfirmed).length;
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div>
        <ClarosSays speaking>
          <h1 className="text-4xl font-black leading-[0.95] tracking-[-0.045em] sm:text-5xl">{t(status === "ready" ? "learn.ready.title" : "learn.partial.title")}</h1>
        </ClarosSays>
        <p className="mt-4 max-w-[52ch] leading-relaxed text-ink-2">
          {status === "ready" ? t("learn.ready.sub", { experts: map.experts.map((e) => e.name).join(", ") }) : t("learn.partial.sub")}
        </p>
        <Panel className="mt-6 p-4">
          <p className="text-lg font-extrabold leading-snug tracking-[-0.02em]">{map.name}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <CoverageChip status={status} long />
            {map.apps.map((a) => (
              <AppChip key={a} name={a} />
            ))}
            {map.onet ? <OnetTag onet={map.onet} /> : null}
            <AvatarStack users={map.experts} size={24} />
          </div>
        </Panel>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button variant="claros" size="xl" onClick={onStart} autoFocus>
            {t("learn.start")} <ArrowRight aria-hidden />
          </Button>
          <Link href={`/map/${encodeURIComponent(map.workflow_id)}`} className={buttonVariants({ variant: "ghost", size: "xl" })}>
            <MapIcon aria-hidden /> {t("wf.view")}
          </Link>
        </div>
      </div>
      <ol className="space-y-2" aria-label={t("learn.steps")}>
        {steps.map((s) => {
          const ok = status === "ready" || isConfirmed(s);
          return (
            <li key={s.id} className={cn("flex items-center gap-3 rounded-base border-2 border-ink p-2.5", ok ? "bg-card" : "hatch-partial border-dashed")}>
              <span className="tnum grid size-8 shrink-0 place-items-center rounded-[4px] border-2 border-ink bg-ink font-mono text-xs font-black text-paper">{s.order}</span>
              <span className="flex-1 text-sm font-semibold">{s.title}</span>
              {!ok ? <span className="text-[11px] font-bold">{t("learn.notConfirmed")}</span> : null}
            </li>
          );
        })}
        {status === "partial" ? (
          <li className="tnum pt-1 font-mono text-xs text-ink-2">
            {confirmed}/{steps.length}
          </li>
        ) : null}
      </ol>
    </div>
  );
}

function Missing({ lookup, intent, sessionId, keyframe }: { lookup: LookupResponse | null; intent: string; sessionId: string | null; keyframe: string | null }) {
  const { t } = useUi();
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const ask = async () => {
    setBusy(true);
    await createRequest({
      workflow_hint: intent || lookup?.onet?.task || "Workflow on this screen",
      requested_by: LEA,
      moment: sessionId ? { session_id: sessionId, keyframe_ids: keyframe ? [keyframe] : [], t: Date.now(), utterance_ids: [] } : null,
    });
    setBusy(false);
    setSent(true);
  };
  return (
    <div className="mx-auto max-w-2xl">
      <Panel className="p-6 sm:p-8">
        <ClarosSays speaking={!sent}>
          <h1 className="text-3xl font-black leading-tight tracking-[-0.03em] sm:text-4xl">{t("learn.missing.title")}</h1>
          <p className="mt-3 max-w-[52ch] leading-relaxed text-ink-2">{t("learn.missing.sub")}</p>
        </ClarosSays>
        {lookup?.onet ? (
          <div className="mt-5 rounded-[4px] border-2 border-dashed border-ink p-3 text-sm">
            <span className="font-bold">{t("learn.missing.onet")}:</span> {lookup.onet.task ?? lookup.onet.occupation_title}
            <span className="ml-1 font-mono text-[11px]">O*NET {lookup.onet.occupation_code}</span>
          </div>
        ) : null}
        <div className="mt-6">
          {sent ? (
            <div className="claros-enter flex items-start gap-3 rounded-base border-2 border-ink bg-ready p-4 text-on-fill" role="status">
              <CheckCircle2 className="mt-0.5 size-6 shrink-0" aria-hidden />
              <div>
                <p className="text-lg font-extrabold">{t("learn.missing.asked")}</p>
                <p className="text-sm">{t("learn.missing.asked.sub", { name: firstName(EXPERT.name) })}</p>
                <Link href="/" className={cn(buttonVariants({ variant: "secondary", size: "sm" }), "mt-3")}>
                  {t("learn.missing.home")} <ArrowRight aria-hidden />
                </Link>
              </div>
            </div>
          ) : (
            <Button variant="claros" size="xl" onClick={ask} loading={busy} autoFocus>
              {!busy ? <Hand aria-hidden /> : null} {t("learn.missing.ask")}
            </Button>
          )}
        </div>
      </Panel>
    </div>
  );
}

/* ---------------- learning loop ---------------- */

function Loop({
  map,
  partial,
  startAt,
  onDone,
  onAnswerVoice,
}: {
  map: WorkMap;
  partial: boolean;
  startAt?: string | null;
  onDone: (m: Record<string, MasteryNode["level"]>) => void;
  onAnswerVoice?: (text: string) => void;
}) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const [idx, setIdx] = useState(() => Math.max(0, startAt ? steps.findIndex((s) => s.id === startAt) : 0));
  const [levels, setLevels] = useState<Record<string, MasteryNode["level"]>>({});
  const highlighted = useLiveStore((s) => s.highlightedStepId);

  useEffect(() => {
    if (!highlighted) return;
    const i = steps.findIndex((s) => s.id === highlighted);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (i >= 0) setIdx(i);
  }, [highlighted, steps]);

  const step = steps[idx];
  const next = (level: MasteryNode["level"]) => {
    const l = { ...levels, [step.id]: level };
    setLevels(l);
    if (idx + 1 >= steps.length) onDone(l);
    else setIdx(idx + 1);
  };

  return (
    <div className="grid gap-8 lg:grid-cols-[240px_minmax(0,1fr)]">
      <nav aria-label={t("learn.steps")} className="lg:sticky lg:top-24 lg:self-start">
        <p className="mb-2 font-mono text-xs font-semibold text-ink-2">{t("learn.step", { n: idx + 1, total: steps.length })}</p>
        <ol className="flex gap-1.5 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible">
          {steps.map((s, i) => (
            <li key={s.id} className="shrink-0">
              <button
                type="button"
                onClick={() => setIdx(i)}
                aria-current={i === idx ? "step" : undefined}
                aria-label={`${s.order}. ${s.title}`}
                className={cn(
                  "flex items-center gap-2 rounded-[6px] border-2 px-2 py-1.5 text-left text-xs font-semibold transition-colors lg:w-full",
                  i === idx ? "border-ink bg-card shadow-hard-sm" : "border-transparent hover:border-ink",
                  partial && !isConfirmed(s) && "hatch",
                )}
              >
                <span className={cn("tnum grid size-6 shrink-0 place-items-center rounded-[3px] border-2 border-ink font-mono text-[10px] font-bold text-on-fill", levels[s.id] ? LEVEL_BG[levels[s.id]] : "bg-card text-ink")}>
                  {s.order}
                </span>
                <span className="hidden line-clamp-2 lg:inline">{s.title}</span>
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <div key={step.id} className="claros-enter min-w-0">
        <StepTeach map={map} step={step} partial={partial} onNext={next} onAnswerVoice={onAnswerVoice} />
      </div>
    </div>
  );
}

function StepTeach({
  map,
  step,
  partial,
  onNext,
  onAnswerVoice,
}: {
  map: WorkMap;
  step: Step;
  partial: boolean;
  onNext: (l: MasteryNode["level"]) => void;
  onAnswerVoice?: (text: string) => void;
}) {
  const { t } = useUi();
  const guards = guardrailsFor(map, step);
  const unconfirmed = partial && !isConfirmed(step);
  const reasons = quotesFor(map, step.decision?.reason_quote_ids);

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <Panel className={cn("p-4 sm:p-5", unconfirmed && "hatch-partial")}>
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-2xl font-black leading-tight tracking-[-0.03em]">{step.title}</h1>
          <AvatarStack users={step.experts.map((id) => expertById(map, id))} size={26} />
        </div>
        <ScreenThumb className="mt-4" keyframeId={step.moment?.keyframe_ids?.[0]} title={step.state_signature.view ?? ""} highlight={!unconfirmed ? null : stepHighlight(step)} />
        {unconfirmed ? (
          <div className="mt-4 rounded-[4px] border-2 border-ink bg-card p-3">
            <p className="flex items-center gap-2 font-extrabold">
              <AlertOctagon className="size-5" aria-hidden /> {t("learn.notConfirmed")}
            </p>
            <p className="mt-1 text-sm">{t("learn.notConfirmed.sub")}</p>
          </div>
        ) : null}
      </Panel>

      <div>
        {step.conflict ? (
          <ConflictBanner map={map} step={step} />
        ) : unconfirmed ? null : guards.length ? (
          <Intervention map={map} step={step} onDone={onNext} />
        ) : step.decision?.kind === "judgment" ? (
          <Predict map={map} step={step} onDone={onNext} onAnswerVoice={onAnswerVoice} reasons={reasons} />
        ) : (
          <Panel className="p-4">
            <p className="font-semibold">{step.decision?.description ?? step.title}</p>
            {reasons.map((q) => (
              <div key={q.id} className="mt-3">
                <QuoteBlock quote={q} compact />
              </div>
            ))}
          </Panel>
        )}
        {(unconfirmed || step.conflict || (!guards.length && step.decision?.kind !== "judgment")) && (
          <Button variant="primary" size="lg" className="mt-5" onClick={() => onNext(unconfirmed || step.conflict ? "unseen" : "unaided")}>
            {t("learn.next")} <ArrowRight aria-hidden />
          </Button>
        )}
      </div>
    </div>
  );
}

function Choices({ options, correct, picked, onPick }: { options: string[]; correct: string; picked: string | null; onPick: (o: string) => void }) {
  return (
    <div className="grid gap-2" role="radiogroup">
      {options.map((o, i) => {
        const state = picked == null ? "idle" : o === correct ? "right" : o === picked ? "wrong" : "dim";
        return (
          <button
            key={o}
            type="button"
            role="radio"
            aria-checked={picked === o}
            disabled={picked != null}
            onClick={() => onPick(o)}
            className={cn(
              "flex items-start gap-3 rounded-[6px] border-2 border-ink p-3 text-left text-sm font-semibold text-ink transition-[transform,box-shadow,background-color] duration-150",
              state === "idle" && "bg-card shadow-hard-sm hover:-translate-x-px hover:-translate-y-px hover:shadow-hard active:translate-x-0.5 active:translate-y-0.5 active:shadow-none",
              state === "right" && "bg-ready text-on-fill",
              state === "wrong" && "bg-missing text-on-fill",
              state === "dim" && "bg-card opacity-50",
            )}
          >
            <span className="tnum grid size-6 shrink-0 place-items-center rounded-[3px] border-2 border-ink bg-card font-mono text-xs text-ink">
              {state === "right" ? <Check className="size-3.5" /> : state === "wrong" ? <X className="size-3.5" /> : String.fromCharCode(65 + i)}
            </span>
            <span className="pt-0.5">{o}</span>
          </button>
        );
      })}
    </div>
  );
}

function Predict({
  map,
  step,
  reasons,
  onDone,
  onAnswerVoice,
}: {
  map: WorkMap;
  step: Step;
  reasons: ReturnType<typeof quotesFor>;
  onDone: (l: MasteryNode["level"]) => void;
  onAnswerVoice?: (text: string) => void;
}) {
  const { t } = useUi();
  const correct = step.decision!.description;
  const options = useMemo(() => {
    const others = MOCK_DISTRACTORS[step.id] ?? map.steps.filter((s) => s.id !== step.id && s.decision).slice(0, 2).map((s) => s.decision!.description);
    return shuffle([correct, ...others.slice(0, 2)], step.order * 7 + 3);
  }, [correct, map.steps, step.id, step.order]);
  const [picked, setPicked] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);
  const right = picked === correct;
  const done = picked != null || revealed;

  return (
    <div className="rounded-[10px] border-2 border-claros bg-card p-4 shadow-claros sm:p-5">
      <ClarosSays speaking={!done}>
        <p className="text-xl font-black tracking-[-0.02em]">{t("learn.predict.q")}</p>
      </ClarosSays>
      <div className="mt-4">
        <Choices options={options} correct={correct} picked={picked ?? (revealed ? correct : null)} onPick={(o) => setPicked(o)} />
      </div>
      {!done ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {onAnswerVoice ? (
            <Button variant="claros" size="sm" onClick={() => onAnswerVoice(t("learn.predict.q"))}>
              <Mic aria-hidden /> {t("learn.predict.voice")}
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => setRevealed(true)}>
            {t("learn.predict.reveal")}
          </Button>
        </div>
      ) : (
        <div className="claros-enter mt-4 space-y-3" aria-live="polite">
          <p className="font-extrabold">{picked ? (right ? t("learn.predict.correct") : t("learn.predict.wrong")) : null}</p>
          {reasons.map((q) => (
            <QuoteBlock key={q.id} quote={q} compact />
          ))}
          {step.decision?.counterfactual ? (
            <p className="text-sm">
              <span className="font-bold">{t("map.counterfactual")}:</span> {step.decision.counterfactual}
            </p>
          ) : null}
          <Button variant="primary" size="lg" autoFocus onClick={() => onDone(revealed && !picked ? "hinted" : right ? "unaided" : "caught")}>
            {t("learn.next")} <ArrowRight aria-hidden />
          </Button>
        </div>
      )}
    </div>
  );
}

function Intervention({ map, step, onDone }: { map: WorkMap; step: Step; onDone: (l: MasteryNode["level"]) => void }) {
  const { t } = useUi();
  const g = guardrailsFor(map, step)[0];
  const who = expertById(map, g.experts[0] ?? step.experts[0]);
  const name = firstName(who.name);
  const quotes = quotesFor(map, g.quote_ids);
  const correct = g.text;
  const options = shuffle([correct, ...(MOCK_DISTRACTORS[g.id] ?? []).slice(0, 2)], g.id.length * 13);
  const [picked, setPicked] = useState<string | null>(null);
  const frames = g.evidence.flatMap((e) => e.keyframe_ids);
  return (
    <div className="claros-enter rounded-[10px] border-[3px] border-ink bg-claros p-4 text-claros-ink shadow-hard-lg sm:p-5" role="alert">
      <div className="flex items-center gap-3">
        <ShieldAlert className="size-7 shrink-0" aria-hidden />
        <div>
          <p className="text-2xl font-black leading-tight tracking-[-0.03em]">{t("learn.intervene.title", { name })}</p>
          <p className="text-lg font-bold opacity-90">{t("learn.intervene.ask")}</p>
        </div>
      </div>
      <div className="mt-4 rounded-[6px] border-2 border-ink bg-card p-3 text-ink">
        <p className="mb-2 text-xs font-bold">{t("learn.intervene.flip", { name })}</p>
        <Flipbook ids={frames.length ? frames : step.moment?.keyframe_ids ?? []} title={step.state_signature.view ?? ""} highlight={stepHighlight(step)} />
      </div>
      <div className="mt-4">
        <Choices options={options} correct={correct} picked={picked} onPick={setPicked} />
      </div>
      {picked ? (
        <div className="claros-enter mt-4 space-y-3">
          {quotes.map((q) => (
            <div key={q.id} className="text-ink">
              <QuoteBlock quote={q} />
            </div>
          ))}
          <p className="text-sm font-semibold">
            {t(("guard." + g.action) as DictKey)}
            {g.owner ? ` · ${t("map.owner")}: ${g.owner}` : ""}
          </p>
          <Button variant="secondary" size="lg" autoFocus onClick={() => onDone(picked === correct ? "unaided" : "caught")}>
            {t("learn.intervene.ack")} <ArrowRight aria-hidden />
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/* ---------------- mastery ---------------- */

function Mastery({ map, levels, onPractice }: { map: WorkMap; levels: Record<string, MasteryNode["level"]>; onPractice: (stepId: string | null) => void }) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const rank = { caught: 0, hinted: 1, unseen: 2, unaided: 3 } as const;
  const weakest = [...steps].filter((s) => levels[s.id] !== "unseen").sort((a, b) => rank[levels[a.id] ?? "unseen"] - rank[levels[b.id] ?? "unseen"])[0];
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div>
        <h1 className="text-4xl font-black tracking-[-0.04em]">{t("learn.mastery.title")}</h1>
        <p className="mt-2 text-ink-2">{t("learn.mastery.sub")}</p>
        <ul className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {steps.map((s) => {
            const lvl = levels[s.id] ?? "unseen";
            return (
              <li key={s.id} className={cn("flex min-h-28 flex-col rounded-base border-2 border-ink p-3", LEVEL_BG[lvl], lvl === "unseen" ? "hatch text-ink" : "text-on-fill")}>
                <span className="font-mono text-xs font-bold">{s.order}</span>
                <span className="mt-1 flex-1 text-sm font-semibold leading-snug">{s.title}</span>
                <span className="mt-2 text-xs font-black">{t(("learn.lvl." + lvl) as DictKey)}</span>
              </li>
            );
          })}
        </ul>
        <div className="mt-4 flex flex-wrap gap-3 text-xs font-semibold">
          {(["unaided", "hinted", "caught", "unseen"] as const).map((l) => (
            <span key={l} className="inline-flex items-center gap-1.5">
              <span className={cn("size-3.5 rounded-[2px] border-2 border-ink", LEVEL_BG[l])} /> {t(("learn.lvl." + l) as DictKey)}
            </span>
          ))}
        </div>
      </div>
      <aside className="space-y-4">
        <button
          type="button"
          onClick={() => onPractice(weakest?.id ?? null)}
          className="group flex w-full items-center gap-4 rounded-[10px] border-[3px] border-ink bg-claros p-5 text-left text-claros-ink shadow-hard-lg transition-[transform,box-shadow] duration-150 hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[8px_8px_0_0_var(--ink)] active:translate-x-1.5 active:translate-y-1.5 active:shadow-none"
        >
          <ClarosDot size={40} />
          <span className="flex-1">
            <span className="block text-2xl font-black tracking-[-0.03em]">{t("learn.mastery.practice")}</span>
            <span className="block text-sm opacity-90">{t("learn.mastery.practice.sub")}</span>
            {weakest ? <span className="mt-2 block text-sm font-bold">{t("mastery.next", { step: weakest.title })}</span> : null}
          </span>
          <ArrowRight className="size-6 transition-transform group-hover:translate-x-1" aria-hidden />
        </button>
        <Link href={`/map/${map.workflow_id}`} className={buttonVariants({ variant: "secondary" })}>
          <MapIcon aria-hidden /> {t("wf.view")}
        </Link>
      </aside>
    </div>
  );
}
