"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import {
  AlertOctagon,
  ArrowRight,
  Check,
  CheckCircle2,
  Hand,
  Mic,
  MonitorUp,
  RotateCw,
  Send,
  ShieldAlert,
  X,
} from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import {
  AvatarStack,
  ClarosDot,
  ClarosSays,
  CoverageChip,
  Flipbook,
  Loading,
  Panel,
  QuoteBlock,
  ScreenThumb,
  SourceNote,
} from "@/components/claros/primitives";
import {
  expertById,
  firstName,
  guardrailsFor,
  isConfirmed,
  quotesFor,
  shuffle,
  sortedSteps,
  stepHighlight,
} from "@/components/claros/mapUtils";
import { useJoinSession, useLive, useLiveStore } from "@/components/claros/live";
import { ConflictBanner } from "@/components/claros/ConflictBanner";
import { createRequest, createSession, getWorkflow, lookupWorkflow, type Source } from "@/lib/api";
import { LEA, MOCK_DISTRACTORS } from "@/lib/mock";
import type { Coverage, LookupResponse, MasteryNode, Step, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

type Phase = "invoke" | "lookup" | "result" | "loop" | "mastery";
type Force = "auto" | Coverage["status"];

export default function LearnPage() {
  return (
    <Shell>
      <Suspense fallback={<Loading rows={2} />}>
        <Learn />
      </Suspense>
    </Shell>
  );
}

function Learn() {
  const { t, lang } = useUi();
  const params = useSearchParams();
  const [phase, setPhase] = useState<Phase>("invoke");
  const [intent, setIntent] = useState("");
  const [force, setForce] = useState<Force>((params.get("force") as Force) || (params.get("wf") ? "missing" : "auto"));
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [lookup, setLookup] = useState<LookupResponse | null>(null);
  const [source, setSource] = useState<Source>();
  const [map, setMap] = useState<WorkMap | null>(null);
  const [mastery, setMastery] = useState<Record<string, MasteryNode["level"]>>({});

  useJoinSession(sessionId, "learn", LEA, lang);
  const { capture, voice } = useLive(sessionId, "learn", lang, LEA.name);

  const status: Coverage["status"] = lookup?.match?.coverage.status ?? "missing";

  const run = async () => {
    setPhase("lookup");
    const s = sessionId ? { data: { session_id: sessionId } } : await createSession({ mode: "learn", user: LEA, lang });
    setSessionId(s.data.session_id);
    const [r] = await Promise.all([
      lookupWorkflow({ utterance: intent || "walk me through this", lang, screen_state: null }, force === "auto" ? undefined : force),
      new Promise((ok) => setTimeout(ok, 1100)), // let the lookup read as a deliberate moment
    ]);
    setLookup(r.data);
    setSource(r.source);
    if (r.data.match?.workflow_id) {
      const m = await getWorkflow(r.data.match.workflow_id);
      setMap(m.data);
    }
    setPhase("result");
  };

  const share = async () => {
    try {
      await capture.start();
    } catch {
      /* user cancelled — still allow lookup by intent */
    }
  };

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {capture.active ? (
            <span className="inline-flex items-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] bg-[var(--ready)] px-2 py-0.5 text-xs font-bold">
              <MonitorUp className="size-3.5" aria-hidden /> {t("learn.sharing")}
            </span>
          ) : null}
          <SourceNote source={source} />
        </div>
        {phase !== "invoke" ? (
          <button
            type="button"
            onClick={() => {
              setPhase("invoke");
              setLookup(null);
              setMap(null);
              setMastery({});
            }}
            className="inline-flex items-center gap-1.5 text-sm font-bold underline decoration-2 underline-offset-4"
          >
            <RotateCw className="size-4" aria-hidden /> {t("learn.end")}
          </button>
        ) : null}
      </div>

      {phase === "invoke" && (
        <Invoke
          intent={intent}
          setIntent={setIntent}
          sharing={capture.active}
          preview={capture.lastKeyframe?.url}
          onShare={share}
          onGo={run}
          force={force}
          setForce={setForce}
          voiceOn={voice.status === "connected"}
          onVoice={() => (voice.status === "connected" ? voice.end() : voice.start().catch(() => {}))}
        />
      )}
      {phase === "lookup" && <Lookup preview={capture.lastKeyframe?.url} />}
      {phase === "result" && status === "missing" && (
        <Missing lookup={lookup} intent={intent} sessionId={sessionId} keyframe={capture.lastKeyframe ? `live_${capture.lastKeyframe.seq}` : null} />
      )}
      {phase === "result" && status !== "missing" && map && (
        <Found status={status} map={map} onStart={() => setPhase("loop")} />
      )}
      {phase === "loop" && map && (
        <Loop
          map={map}
          partial={status === "partial"}
          onDone={(m) => {
            setMastery(m);
            setPhase("mastery");
          }}
          onAnswerVoice={voice.status === "connected" ? (txt) => voice.sendText(txt) : undefined}
        />
      )}
      {phase === "mastery" && map && <Mastery map={map} levels={mastery} onPractice={() => setPhase("loop")} />}
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
  force: Force;
  setForce: (f: Force) => void;
  voiceOn: boolean;
  onVoice: () => void;
}) {
  const { t } = useUi();
  return (
    <div className="grid gap-8 lg:grid-cols-[1.1fr_1fr]">
      <div>
        <ClarosSays speaking>
          <h1 className="text-4xl font-black leading-[0.95] tracking-[-0.04em] sm:text-5xl">{t("learn.invoke.title")}</h1>
        </ClarosSays>
        <p className="mt-4 max-w-[52ch] leading-relaxed text-[var(--ink-2)]">{t("learn.invoke.sub")}</p>

        <form
          className="mt-6 space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            p.onGo();
          }}
        >
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={p.onShare}
              className={cn(
                "inline-flex h-12 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] px-4 font-bold shadow-[var(--hard)] transition-[transform,box-shadow] hover:translate-x-1 hover:translate-y-1 hover:shadow-none",
                p.sharing ? "bg-[var(--ready)]" : "bg-white",
              )}
            >
              <MonitorUp className="size-5" aria-hidden />
              {p.sharing ? t("learn.sharing") : t("learn.invoke.share")}
            </button>
            <button
              type="button"
              onClick={p.onVoice}
              aria-pressed={p.voiceOn}
              className={cn(
                "inline-flex h-12 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] px-4 font-bold",
                p.voiceOn ? "bg-[var(--claros)] text-white" : "bg-white hover:bg-[var(--paper-2)]",
              )}
            >
              <Mic className="size-5" aria-hidden />
              {t("learn.predict.voice")}
            </button>
          </div>
          <label className="block">
            <span className="mb-1.5 block text-sm font-bold">{t("learn.invoke.intent")}</span>
            <div className="flex gap-2">
              <input
                value={p.intent}
                onChange={(e) => p.setIntent(e.target.value)}
                placeholder={t("learn.invoke.intent.ph")}
                className="h-12 min-w-0 flex-1 rounded-[5px] border-2 border-[var(--ink)] bg-white px-3 text-base placeholder:text-[var(--ink-2)]/70"
              />
              <button
                type="submit"
                className="inline-flex h-12 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--claros)] px-5 font-extrabold text-white shadow-[var(--hard)] transition-[transform,box-shadow] hover:translate-x-1 hover:translate-y-1 hover:shadow-none"
              >
                {t("learn.start")} <ArrowRight className="size-5" aria-hidden />
              </button>
            </div>
          </label>
          <fieldset className="flex flex-wrap items-center gap-2 pt-2 text-xs">
            <legend className="sr-only">{t("learn.invoke.demo")}</legend>
            <span className="font-mono font-semibold text-[var(--ink-2)]">{t("learn.invoke.demo")}:</span>
            {(["auto", "ready", "partial", "missing"] as Force[]).map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={p.force === f}
                onClick={() => p.setForce(f)}
                className={cn("rounded-[3px] border-2 border-dashed border-[var(--ink)] px-2 py-0.5 font-mono font-semibold", p.force === f ? "bg-[var(--ink)] text-[var(--paper)]" : "bg-white")}
              >
                {f}
              </button>
            ))}
          </fieldset>
        </form>
      </div>
      <div>
        {p.preview ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={p.preview} alt="" className="aspect-[16/10] w-full rounded-[6px] border-2 border-[var(--ink)] object-cover object-top shadow-[var(--hard)]" />
        ) : (
          <div className="hatch grid aspect-[16/10] place-items-center rounded-[6px] border-2 border-dashed border-[var(--ink)] p-6 text-center">
            <div>
              <MonitorUp className="mx-auto size-8" aria-hidden />
              <p className="mt-2 max-w-[30ch] text-sm font-semibold">{t("capture.preflight.window.sub")}</p>
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
    <div className="mx-auto max-w-xl py-10 text-center" role="status" aria-live="polite">
      <div className="mx-auto w-fit">
        <ClarosDot size={72} speaking />
      </div>
      <h1 className="mt-6 text-3xl font-black tracking-[-0.03em]">{t("learn.lookup")}</h1>
      <p className="mt-2 text-[var(--ink-2)]">{t("learn.lookup.sub")}</p>
      {preview ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={preview} alt="" className="mx-auto mt-6 aspect-[16/10] w-72 rounded-[4px] border-2 border-[var(--ink)] object-cover object-top" />
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
    <div className="grid gap-8 lg:grid-cols-[1.1fr_1fr]">
      <div>
        <ClarosSays speaking>
          <h1 className="text-4xl font-black leading-[0.95] tracking-[-0.04em] sm:text-5xl">
            {t(status === "ready" ? "learn.ready.title" : "learn.partial.title")}
          </h1>
        </ClarosSays>
        <p className="mt-4 max-w-[52ch] leading-relaxed text-[var(--ink-2)]">
          {status === "ready" ? t("learn.ready.sub", { experts: map.experts.map((e) => e.name).join(", ") }) : t("learn.partial.sub")}
        </p>
        <Panel className="mt-6 p-4">
          <p className="text-lg font-extrabold tracking-[-0.02em]">{map.name}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CoverageChip status={status} long />
            <AvatarStack users={map.experts} size={24} />
            {map.onet ? <span className="font-mono text-[11px]">O*NET {map.onet.occupation_code}</span> : null}
          </div>
        </Panel>
        <button
          type="button"
          onClick={onStart}
          autoFocus
          className="mt-6 inline-flex h-14 items-center gap-3 rounded-[6px] border-[3px] border-[var(--ink)] bg-[var(--claros)] px-6 text-lg font-black text-white shadow-[var(--hard-lg)] transition-[transform,box-shadow] hover:translate-x-1.5 hover:translate-y-1.5 hover:shadow-none"
        >
          {t("learn.start")} <ArrowRight className="size-5" aria-hidden />
        </button>
      </div>
      <ol className="space-y-2" aria-label={t("map.lane.steps")}>
        {steps.map((s) => {
          const ok = status === "ready" || isConfirmed(s);
          return (
            <li key={s.id} className={cn("flex items-center gap-3 rounded-[5px] border-2 border-[var(--ink)] p-2.5", ok ? "bg-white" : "hatch-partial")}>
              <span className="tnum grid size-7 shrink-0 place-items-center rounded-[3px] border-2 border-[var(--ink)] bg-[var(--paper)] font-mono text-xs font-bold">{s.order}</span>
              <span className="flex-1 text-sm font-semibold">{s.title}</span>
              {!ok ? <span className="text-[11px] font-bold uppercase">{t("learn.notConfirmed")}</span> : null}
            </li>
          );
        })}
        {status === "partial" ? (
          <li className="pt-1 font-mono text-xs text-[var(--ink-2)]">
            {confirmed}/{steps.length} ✓
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
          <p className="mt-3 max-w-[52ch] leading-relaxed text-[var(--ink-2)]">{t("learn.missing.sub")}</p>
        </ClarosSays>
        {lookup?.onet ? (
          <div className="mt-5 rounded-[4px] border-2 border-dashed border-[var(--ink)] p-3 text-sm">
            <span className="font-bold">{t("learn.missing.onet")}:</span> {lookup.onet.task ?? lookup.onet.occupation_title}
            <span className="ml-1 font-mono text-[11px]">O*NET {lookup.onet.occupation_code}</span>
          </div>
        ) : null}
        <div className="mt-6">
          {sent ? (
            <div className="claros-enter flex items-start gap-3 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--ready)] p-4" role="status">
              <CheckCircle2 className="mt-0.5 size-6 shrink-0" aria-hidden />
              <div>
                <p className="text-lg font-extrabold">{t("learn.missing.asked")}</p>
                <p className="text-sm">{t("learn.missing.asked.sub", { name: "Sabine" })}</p>
                <Link href="/inbox" className="mt-2 inline-block text-sm font-bold underline decoration-2 underline-offset-4">
                  {t("nav.inbox")} →
                </Link>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={ask}
              disabled={busy}
              autoFocus
              className="inline-flex h-14 items-center gap-3 rounded-[6px] border-[3px] border-[var(--ink)] bg-[var(--claros)] px-6 text-lg font-black text-white shadow-[var(--hard-lg)] transition-[transform,box-shadow] hover:translate-x-1.5 hover:translate-y-1.5 hover:shadow-none disabled:opacity-60"
            >
              <Hand className="size-5" aria-hidden /> {t("learn.missing.ask")}
            </button>
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
  onDone,
  onAnswerVoice,
}: {
  map: WorkMap;
  partial: boolean;
  onDone: (m: Record<string, MasteryNode["level"]>) => void;
  onAnswerVoice?: (text: string) => void;
}) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const [idx, setIdx] = useState(0);
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
    <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
      <nav aria-label={t("map.lane.steps")} className="hidden lg:block">
        <ol className="space-y-1.5">
          {steps.map((s, i) => (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => setIdx(i)}
                aria-current={i === idx ? "step" : undefined}
                className={cn(
                  "flex w-full items-center gap-2 rounded-[4px] border-2 px-2 py-1.5 text-left text-xs font-semibold",
                  i === idx ? "border-[var(--ink)] bg-white shadow-[2px_2px_0_0_var(--ink)]" : "border-transparent hover:border-[var(--ink)]",
                  partial && !isConfirmed(s) && "hatch",
                )}
              >
                <span className={cn("tnum grid size-5 shrink-0 place-items-center rounded-[3px] border-2 border-[var(--ink)] font-mono text-[10px]", levels[s.id] ? LEVEL_BG[levels[s.id]] : "bg-white")}>{s.order}</span>
                <span className="line-clamp-2">{s.title}</span>
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <div key={step.id} className="claros-enter">
        <p className="mb-2 font-mono text-xs font-semibold">{t("learn.step", { n: idx + 1, total: steps.length })}</p>
        <StepTeach map={map} step={step} partial={partial} onNext={next} onAnswerVoice={onAnswerVoice} />
      </div>
    </div>
  );
}

const LEVEL_BG: Record<MasteryNode["level"], string> = {
  unseen: "bg-white",
  caught: "bg-[var(--missing)]",
  hinted: "bg-[var(--partial)]",
  unaided: "bg-[var(--ready)]",
};

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
    <div className="grid gap-5 xl:grid-cols-[1.1fr_1fr]">
      <Panel className={cn("p-4 sm:p-5", unconfirmed && "hatch-partial")}>
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-2xl font-black leading-tight tracking-[-0.03em] text-balance">{step.title}</h1>
          <AvatarStack users={step.experts.map((id) => expertById(map, id))} size={26} />
        </div>
        <ScreenThumb className="mt-4" keyframeId={step.moment?.keyframe_ids?.[0]} title={step.state_signature.view ?? step.title} highlight={!unconfirmed ? null : stepHighlight(step)} />
        {unconfirmed ? (
          <div className="mt-4 rounded-[4px] border-2 border-[var(--ink)] bg-white p-3">
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
          <button
            type="button"
            onClick={() => onNext(unconfirmed || step.conflict ? "unseen" : "unaided")}
            className="mt-4 inline-flex h-11 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--ink)] px-5 font-bold text-[var(--paper)] shadow-[3px_3px_0_0_var(--claros)] hover:translate-x-[3px] hover:translate-y-[3px] hover:shadow-none"
          >
            {t("learn.next")} <ArrowRight className="size-4" aria-hidden />
          </button>
        )}
      </div>
    </div>
  );
}

function Choices({
  options,
  correct,
  picked,
  onPick,
}: {
  options: string[];
  correct: string;
  picked: string | null;
  onPick: (o: string) => void;
}) {
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
              "flex items-start gap-3 rounded-[5px] border-2 border-[var(--ink)] p-3 text-left text-sm font-semibold transition-[transform,box-shadow,background-color]",
              state === "idle" && "bg-white shadow-[3px_3px_0_0_var(--ink)] hover:translate-x-[3px] hover:translate-y-[3px] hover:shadow-none",
              state === "right" && "bg-[var(--ready)]",
              state === "wrong" && "bg-[var(--missing)]",
              state === "dim" && "bg-white opacity-50",
            )}
          >
            <span className="tnum grid size-6 shrink-0 place-items-center rounded-[3px] border-2 border-[var(--ink)] bg-white font-mono text-xs">
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
    <Panel tone="card" className="border-[var(--claros)] p-4 shadow-[var(--hard-claros)] sm:p-5">
      <ClarosSays speaking={!done}>
        <p className="text-xl font-black tracking-[-0.02em]">{t("learn.predict.q")}</p>
      </ClarosSays>
      <div className="mt-4">
        <Choices options={options} correct={correct} picked={picked ?? (revealed ? correct : null)} onPick={(o) => setPicked(o)} />
      </div>
      {!done ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {onAnswerVoice ? (
            <button type="button" onClick={() => onAnswerVoice(t("learn.predict.q"))} className="inline-flex h-10 items-center gap-2 rounded-[4px] border-2 border-[var(--ink)] bg-white px-3 text-sm font-bold">
              <Mic className="size-4" aria-hidden /> {t("learn.predict.voice")}
            </button>
          ) : null}
          <button type="button" onClick={() => setRevealed(true)} className="h-10 rounded-[4px] border-2 border-dashed border-[var(--ink)] px-3 text-sm font-bold">
            {t("learn.predict.reveal")}
          </button>
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
          <button
            type="button"
            autoFocus
            onClick={() => onDone(revealed && !picked ? "hinted" : right ? "unaided" : "caught")}
            className="inline-flex h-11 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--ink)] px-5 font-bold text-[var(--paper)] shadow-[3px_3px_0_0_var(--claros)] hover:translate-x-[3px] hover:translate-y-[3px] hover:shadow-none"
          >
            {t("learn.next")} <ArrowRight className="size-4" aria-hidden />
          </button>
        </div>
      )}
    </Panel>
  );
}

function Intervention({ map, step, onDone }: { map: WorkMap; step: Step; onDone: (l: MasteryNode["level"]) => void }) {
  const { t } = useUi();
  const g = guardrailsFor(map, step)[0];
  const who = expertById(map, g.experts[0] ?? step.experts[0]);
  const name = firstName(who.name);
  const quotes = quotesFor(map, g.quote_ids);
  const correct = g.text;
  const options = useMemo(() => shuffle([correct, ...(MOCK_DISTRACTORS[g.id] ?? []).slice(0, 2)], g.id.length * 13), [correct, g.id]);
  const [picked, setPicked] = useState<string | null>(null);
  const frames = g.evidence.flatMap((e) => e.keyframe_ids);
  return (
    <div className="claros-enter rounded-[8px] border-[3px] border-[var(--ink)] bg-[var(--claros)] p-4 text-white shadow-[var(--hard-lg)] sm:p-5" role="alert">
      <div className="flex items-center gap-3">
        <ShieldAlert className="size-7 shrink-0" aria-hidden />
        <div>
          <p className="text-2xl font-black leading-tight tracking-[-0.03em]">{t("learn.intervene.title", { name })}</p>
          <p className="text-lg font-bold opacity-90">{t("learn.intervene.ask")}</p>
        </div>
      </div>
      <div className="mt-4 rounded-[5px] border-2 border-[var(--ink)] bg-white p-3 text-[var(--ink)]">
        <p className="mb-2 text-xs font-bold">{t("learn.intervene.flip", { name })}</p>
        <Flipbook ids={frames.length > 1 ? frames : [frames[0] ?? "", "", ""]} title={step.state_signature.view ?? step.title} highlight={stepHighlight(step)} />
      </div>
      <div className="mt-4 text-[var(--ink)]">
        <Choices options={options} correct={correct} picked={picked} onPick={setPicked} />
      </div>
      {picked ? (
        <div className="claros-enter mt-4 space-y-3">
          {quotes.map((q) => (
            <div key={q.id} className="text-[var(--ink)]">
              <QuoteBlock quote={q} />
            </div>
          ))}
          <p className="text-sm font-semibold">
            {t(("guard." + g.action) as DictKey)}
            {g.owner ? ` · ${t("map.owner")}: ${g.owner}` : ""}
          </p>
          <button
            type="button"
            autoFocus
            onClick={() => onDone(picked === correct ? "unaided" : "caught")}
            className="inline-flex h-11 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-white px-5 font-black text-[var(--ink)] shadow-[var(--hard)] hover:translate-x-1 hover:translate-y-1 hover:shadow-none"
          >
            {t("learn.intervene.ack")} <ArrowRight className="size-4" aria-hidden />
          </button>
        </div>
      ) : null}
    </div>
  );
}

/* ---------------- mastery ---------------- */

function Mastery({ map, levels, onPractice }: { map: WorkMap; levels: Record<string, MasteryNode["level"]>; onPractice: () => void }) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const rank = { caught: 0, hinted: 1, unseen: 2, unaided: 3 } as const;
  const weakest = [...steps].filter((s) => levels[s.id] !== "unseen").sort((a, b) => rank[levels[a.id] ?? "unseen"] - rank[levels[b.id] ?? "unseen"])[0];
  return (
    <div className="grid gap-8 lg:grid-cols-[1.4fr_1fr]">
      <div>
        <h1 className="text-4xl font-black tracking-[-0.04em]">{t("learn.mastery.title")}</h1>
        <p className="mt-2 text-[var(--ink-2)]">{t("learn.mastery.sub")}</p>
        <ul className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {steps.map((s) => {
            const lvl = levels[s.id] ?? "unseen";
            return (
              <li key={s.id} className={cn("flex min-h-28 flex-col rounded-[5px] border-2 border-[var(--ink)] p-3", LEVEL_BG[lvl], lvl === "unseen" && "hatch")}>
                <span className="font-mono text-xs font-bold">{s.order}</span>
                <span className="mt-1 flex-1 text-sm font-semibold leading-snug">{s.title}</span>
                <span className="mt-2 text-xs font-black uppercase tracking-wide">{t(("learn.lvl." + lvl) as DictKey)}</span>
              </li>
            );
          })}
        </ul>
        <div className="mt-4 flex flex-wrap gap-3 text-xs font-semibold">
          {(["unaided", "hinted", "caught", "unseen"] as const).map((l) => (
            <span key={l} className="inline-flex items-center gap-1.5">
              <span className={cn("size-3.5 rounded-[2px] border-2 border-[var(--ink)]", LEVEL_BG[l])} /> {t(("learn.lvl." + l) as DictKey)}
            </span>
          ))}
        </div>
      </div>
      <aside>
        <button
          type="button"
          onClick={onPractice}
          className="flex w-full items-center gap-4 rounded-[8px] border-[3px] border-[var(--ink)] bg-[var(--claros)] p-5 text-left text-white shadow-[var(--hard-lg)] transition-[transform,box-shadow] hover:translate-x-1.5 hover:translate-y-1.5 hover:shadow-none"
        >
          <ClarosDot size={40} />
          <span className="flex-1">
            <span className="block text-2xl font-black tracking-[-0.03em]">{t("learn.mastery.practice")}</span>
            <span className="block text-sm opacity-90">{t("learn.mastery.practice.sub")}</span>
            {weakest ? <span className="mt-2 block text-sm font-bold">→ {weakest.title}</span> : null}
          </span>
          <Send className="size-6" aria-hidden />
        </button>
        <Link href={`/map/${map.workflow_id}`} className="mt-4 inline-block text-sm font-bold underline decoration-2 underline-offset-4">
          {t("nav.map")} →
        </Link>
      </aside>
    </div>
  );
}
