"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Check, Keyboard, Mic, Play, Send, ShieldAlert, SkipForward, Square, Users, X } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi } from "@/components/claros/i18n";
import { ClarosSays, ErrorState, Loading, Panel, ScreenThumb, SourceNote, useResource } from "@/components/claros/primitives";
import { sortedSteps } from "@/components/claros/mapUtils";
import { useJoinSession, useLive, useLiveStore } from "@/components/claros/live";
import { getSession, getWorkflow, latestWorkflowId } from "@/lib/api";
import { EXPERT, MOCK_UNKNOWNS } from "@/lib/mock";
import type { ExamCase, Unknown, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

type Stage = "questions" | "teachback" | "exam" | "published";

export default function DebriefPage() {
  return (
    <Shell wide>
      <Debrief />
    </Shell>
  );
}

function Debrief() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { t, lang } = useUi();
  // Debrief the workflow this session belongs to; fall back to the most recent workflow.
  const res = useResource(async () => {
    const sess = await getSession(sessionId);
    const wid = sess.source === "live" && sess.data.workflow_id ? sess.data.workflow_id : await latestWorkflowId();
    return getWorkflow(wid);
  }, [sessionId]);
  useJoinSession(res.data ? sessionId : null, "debrief", EXPERT, lang, res.data?.workflow_id);
  const { voice } = useLive(sessionId, "debrief", lang, EXPERT.name);
  const [stage, setStage] = useState<Stage>("questions");

  if (res.loading) return <Loading rows={3} />;
  if (res.error || !res.data) return <ErrorState message={res.error} onRetry={res.retry} />;
  const map = res.data;

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-4xl font-black tracking-[-0.04em]">{t("debrief.title")}</h1>
        <div className="flex items-center gap-3">
          <SourceNote source={res.source} />
          <Stepper stage={stage} />
        </div>
      </div>
      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="min-w-0 space-y-6">
          {stage === "questions" && <Questions map={map} demo={res.source === "mock"} voice={voice} onDone={() => setStage("teachback")} />}
          {stage === "teachback" && <TeachBack map={map} voice={voice} onDone={() => setStage("exam")} />}
          {(stage === "exam" || stage === "published") && <Exam map={map} published={stage === "published"} onPublish={() => setStage("published")} />}
        </div>
        <LiveMap map={map} stage={stage} />
      </div>
    </div>
  );
}

function Stepper({ stage }: { stage: Stage }) {
  const { t } = useUi();
  const items: [Stage, string][] = [
    ["questions", t("debrief.ledger")],
    ["teachback", t("debrief.teachback")],
    ["exam", t("debrief.exam")],
  ];
  const order = ["questions", "teachback", "exam", "published"];
  return (
    <ol className="hidden items-center gap-1 sm:flex" aria-label="Progress">
      {items.map(([s, label], i) => {
        const done = order.indexOf(stage) > order.indexOf(s);
        const cur = stage === s;
        return (
          <li key={s} className="flex items-center gap-1">
            <span aria-current={cur ? "step" : undefined} className={cn("rounded-[3px] border-2 border-[var(--ink)] px-2 py-0.5 text-xs font-bold", cur ? "bg-[var(--ink)] text-[var(--paper)]" : done ? "bg-[var(--ready)]" : "bg-white")}>
              {done ? "✓ " : ""}
              {label}
            </span>
            {i < items.length - 1 ? <span aria-hidden className="h-0.5 w-3 bg-[var(--ink)]" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

/* ---------------- questions (ledger drains to 0) ---------------- */

type Voice = ReturnType<typeof useLive>["voice"];

function Questions({ map, demo, voice, onDone }: { map: WorkMap; demo: boolean; voice: Voice; onDone: () => void }) {
  const { t } = useUi();
  const liveLedger = useLiveStore((s) => s.ledger);
  const queue: Unknown[] = useMemo(
    () => [...(demo ? MOCK_UNKNOWNS.filter((u) => u.status === "deferred") : []), ...map.open_unknowns].filter((u, i, a) => a.findIndex((x) => x.id === u.id) === i),
    [map.open_unknowns, demo],
  );
  const [i, setI] = useState(0);
  const [typing, setTyping] = useState(false);
  const [answer, setAnswer] = useState("");
  const remaining = liveLedger ? liveLedger.open : Math.max(0, queue.length - i);
  const cur: Unknown | undefined = liveLedger?.top ?? queue[i];

  const advance = () => {
    setAnswer("");
    setTyping(false);
    setI((x) => x + 1);
  };
  const done = remaining === 0 || !cur;

  return (
    <div className="space-y-5">
      <div className="flex items-end gap-5">
        <div className="rounded-[8px] border-[3px] border-[var(--ink)] bg-white px-5 py-2 shadow-[var(--hard-lg)]">
          <p className="text-xs font-bold uppercase tracking-wide">{t("debrief.ledger")}</p>
          <p key={remaining} className="ledger-tick tnum text-7xl font-black leading-none tracking-[-0.04em]" aria-live="polite">
            {remaining}
          </p>
        </div>
        <div className="mb-2 flex-1">
          <div className="flex gap-1" aria-hidden>
            {queue.map((u, k) => (
              <span key={u.id} className={cn("h-3 flex-1 rounded-[2px] border-2 border-[var(--ink)]", k < i || remaining === 0 ? "bg-[var(--ready)]" : k === i ? "bg-[var(--claros)]" : "bg-white")} />
            ))}
          </div>
          <p className="mt-2 text-sm text-[var(--ink-2)]">≤ 6 · 5 min</p>
        </div>
      </div>

      {done ? (
        <Panel className="claros-enter flex flex-wrap items-center gap-4 p-5">
          <Check className="size-8" aria-hidden />
          <p className="flex-1 text-xl font-black">{t("debrief.ledger.done")}</p>
          <button type="button" autoFocus onClick={onDone} className="inline-flex h-12 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--claros)] px-5 font-black text-white shadow-[var(--hard)] hover:translate-x-1 hover:translate-y-1 hover:shadow-none">
            {t("debrief.teachback")} <ArrowRight className="size-5" aria-hidden />
          </button>
        </Panel>
      ) : (
        <div key={cur.id} className="claros-enter grid gap-5 lg:grid-cols-[1.3fr_1fr]">
          <div>
            <p className="mb-2 text-xs font-bold uppercase tracking-wide">{t("debrief.moment")}</p>
            <ScreenThumb keyframeId={cur.moment?.keyframe_ids?.[0]} title={cur.entity ?? "Purchase Invoice"} highlight={cur.entity ? { label: cur.entity, to: cur.hypothesis ? "?" : null } : null} className="shadow-[var(--hard)]" />
          </div>
          <div className="rounded-[8px] border-[3px] border-[var(--ink)] bg-[var(--claros)] p-4 text-white shadow-[var(--hard-lg)]">
            <ClarosSays speaking={voice.isSpeaking}>
              <p className="text-xs font-bold uppercase tracking-wide opacity-80">{t("debrief.question")}</p>
              <p className="mt-1 text-xl font-black leading-snug tracking-[-0.02em]">{cur.spoken_question}</p>
            </ClarosSays>
            {cur.hypothesis ? <p className="mt-3 rounded-[4px] border-2 border-[var(--ink)] bg-white/95 p-2 text-sm text-[var(--ink)]">{cur.hypothesis}</p> : null}
            {typing ? (
              <form
                className="mt-4 space-y-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (answer.trim() && voice.status === "connected") voice.sendText(answer);
                  advance();
                }}
              >
                <textarea autoFocus value={answer} onChange={(e) => setAnswer(e.target.value)} rows={3} className="w-full rounded-[4px] border-2 border-[var(--ink)] bg-white p-2 text-[var(--ink)]" aria-label={t("debrief.answer.type")} />
                <button type="submit" className="inline-flex h-10 items-center gap-2 rounded-[4px] border-2 border-[var(--ink)] bg-white px-4 text-sm font-black text-[var(--ink)]">
                  <Send className="size-4" aria-hidden /> {t("debrief.answer.submit")}
                </button>
              </form>
            ) : (
              <div className="mt-4 flex flex-wrap gap-2 text-[var(--ink)]">
                <button
                  type="button"
                  onClick={() => (voice.status === "connected" ? advance() : voice.start().catch(() => advance()))}
                  className="inline-flex h-11 items-center gap-2 rounded-[4px] border-2 border-[var(--ink)] bg-white px-4 font-black shadow-[var(--hard)] hover:translate-x-1 hover:translate-y-1 hover:shadow-none"
                >
                  <Mic className="size-4" aria-hidden /> {t("debrief.answer.voice")}
                </button>
                <button type="button" onClick={() => setTyping(true)} className="inline-flex h-11 items-center gap-2 rounded-[4px] border-2 border-[var(--ink)] bg-white/90 px-3 text-sm font-bold">
                  <Keyboard className="size-4" aria-hidden /> {t("debrief.answer.type")}
                </button>
                <button type="button" onClick={advance} className="inline-flex h-11 items-center gap-2 rounded-[4px] border-2 border-dashed border-white px-3 text-sm font-bold text-white">
                  <SkipForward className="size-4" aria-hidden /> {t("debrief.answer.skip")}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- teach-back ---------------- */

const CORRECTIONS = [
  { before: "All equipment goes to the capital equipment account.", after: "Only equipment lines over 5,000 go to capital equipment; smaller items are expensed." },
  { before: "Hold every December invoice.", after: "Hold December invoices only from suppliers known to double-bill (Nordwind)." },
];

function TeachBack({ map, voice, onDone }: { map: WorkMap; voice: Voice; onDone: () => void }) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const liveHighlight = useLiveStore((s) => s.highlightedStepId);
  const [playing, setPlaying] = useState(false);
  const [k, setK] = useState(-1);
  const [stopped, setStopped] = useState<string[]>([]);

  useEffect(() => {
    if (!playing || voice.status === "connected") return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const h = setInterval(() => setK((x) => Math.min(x + 1, steps.length)), reduce ? 3200 : 2200);
    return () => clearInterval(h);
  }, [playing, steps.length, voice.status]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (k >= steps.length) setPlaying(false);
  }, [k, steps.length]);

  const active = liveHighlight ?? steps[k]?.id ?? null;
  const play = () => {
    setPlaying(true);
    setK(0);
    if (voice.status === "connected") voice.sendText("teach back");
  };

  return (
    <div className="space-y-5">
      <Panel className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex-1">
            <p className="text-xl font-black tracking-[-0.02em]">{t("debrief.teachback")}</p>
            <p className="text-sm text-[var(--ink-2)]">{t("debrief.teachback.sub")}</p>
          </div>
          <button type="button" onClick={play} disabled={playing} className="inline-flex h-11 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--claros)] px-4 font-black text-white shadow-[var(--hard)] hover:translate-x-1 hover:translate-y-1 hover:shadow-none disabled:opacity-60">
            <Play className="size-4" aria-hidden /> {t("debrief.teachback.play")}
          </button>
        </div>
        <ol className="mt-4 space-y-2">
          {steps.map((s) => {
            const on = s.id === active;
            const flagged = stopped.includes(s.id);
            return (
              <li
                key={s.id}
                className={cn(
                  "flex items-center gap-3 rounded-[5px] border-2 border-[var(--ink)] p-2.5 transition-[background-color,transform,box-shadow] duration-200",
                  on ? "-translate-x-1 -translate-y-1 bg-[var(--claros-soft)] shadow-[var(--hard-claros)]" : "bg-white",
                  flagged && "bg-[var(--missing)]/40",
                )}
                aria-current={on ? "step" : undefined}
              >
                <span className="tnum grid size-7 shrink-0 place-items-center rounded-[3px] border-2 border-[var(--ink)] bg-white font-mono text-xs font-bold">{s.order}</span>
                <span className="flex-1 text-sm font-semibold">{s.decision?.description ?? s.title}</span>
                {on ? (
                  <button type="button" onClick={() => { setStopped((x) => [...x, s.id]); setPlaying(false); }} className="inline-flex h-8 items-center gap-1 rounded-[3px] border-2 border-[var(--ink)] bg-[var(--ink)] px-2 text-xs font-bold text-[var(--paper)]">
                    <Square className="size-3" aria-hidden /> {t("debrief.stop")}
                  </button>
                ) : null}
              </li>
            );
          })}
        </ol>
      </Panel>

      <Panel className="p-4">
        <p className="font-extrabold">{t("debrief.correction")}</p>
        <ul className="mt-3 space-y-3">
          {CORRECTIONS.map((c) => (
            <li key={c.before} className="overflow-hidden rounded-[4px] border-2 border-[var(--ink)] font-mono text-[13px]">
              <p className="bg-[var(--missing)]/35 px-3 py-1.5">
                <span aria-hidden className="mr-2 font-bold">−</span>
                <del className="decoration-2">{c.before}</del>
              </p>
              <p className="border-t-2 border-[var(--ink)] bg-[var(--ready)]/45 px-3 py-1.5">
                <span aria-hidden className="mr-2 font-bold">+</span>
                <ins className="no-underline">{c.after}</ins>
              </p>
            </li>
          ))}
        </ul>
        <button type="button" onClick={onDone} className="mt-4 inline-flex h-11 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--ink)] px-5 font-bold text-[var(--paper)] shadow-[3px_3px_0_0_var(--claros)] hover:translate-x-[3px] hover:translate-y-[3px] hover:shadow-none">
          {t("debrief.exam")} <ArrowRight className="size-4" aria-hidden />
        </button>
      </Panel>
    </div>
  );
}

/* ---------------- exam + publish ---------------- */

function Exam({ map, published, onPublish }: { map: WorkMap; published: boolean; onPublish: () => void }) {
  const { t } = useUi();
  const [cases, setCases] = useState<ExamCase[]>(() => map.exam.map((c) => ({ ...c, expert_verdict: null })));
  const mark = (i: number, v: "correct" | "wrong") => setCases((cs) => cs.map((c, k) => (k === i ? { ...c, expert_verdict: v } : c)));
  const allMarked = cases.every((c) => c.expert_verdict);
  return (
    <div className="space-y-5">
      <div>
        <p className="text-xl font-black tracking-[-0.02em]">{t("debrief.exam")}</p>
        <p className="text-sm text-[var(--ink-2)]">{t("debrief.exam.sub")}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {cases.map((c, i) => (
          <Panel key={c.variant} className={cn("flex flex-col p-4", c.expert_verdict === "correct" && "bg-[var(--ready)]/40", c.expert_verdict === "wrong" && "bg-[var(--missing)]/40")}>
            <p className="font-extrabold leading-snug">{c.variant}</p>
            <div className="mt-3 flex-1 rounded-[4px] border-2 border-[var(--claros)] bg-[var(--claros-soft)] p-2.5">
              <p className="text-[11px] font-bold uppercase tracking-wide text-[var(--claros)]">{t("debrief.exam.predicts")}</p>
              <p className="mt-0.5 text-sm font-semibold">{c.predicted}</p>
              <div className="mt-2 flex items-center gap-2">
                <div className="h-2 flex-1 rounded-[2px] border-2 border-[var(--ink)] bg-white">
                  <div className="h-full bg-[var(--claros)]" style={{ width: `${Math.round(c.confidence * 100)}%` }} />
                </div>
                <span className="tnum font-mono text-[11px]">
                  {Math.round(c.confidence * 100)}% {t("debrief.exam.confidence")}
                </span>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button type="button" disabled={published} aria-pressed={c.expert_verdict === "correct"} onClick={() => mark(i, "correct")} className={cn("inline-flex h-10 items-center justify-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] text-sm font-black", c.expert_verdict === "correct" ? "bg-[var(--ready)]" : "bg-white hover:bg-[var(--paper-2)]")}>
                <Check className="size-4" aria-hidden /> {t("debrief.exam.right")}
              </button>
              <button type="button" disabled={published} aria-pressed={c.expert_verdict === "wrong"} onClick={() => mark(i, "wrong")} className={cn("inline-flex h-10 items-center justify-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] text-sm font-black", c.expert_verdict === "wrong" ? "bg-[var(--missing)]" : "bg-white hover:bg-[var(--paper-2)]")}>
                <X className="size-4" aria-hidden /> {t("debrief.exam.wrong")}
              </button>
            </div>
          </Panel>
        ))}
      </div>

      <div className={cn("flex flex-wrap items-center gap-4 rounded-[8px] border-[3px] border-[var(--ink)] p-5 shadow-[var(--hard-lg)]", published ? "bg-[var(--ready)]" : "bg-white")}>
        <div className="flex-1">
          <p className="text-2xl font-black tracking-[-0.03em]">{published ? t("debrief.published") : t("debrief.publish")}</p>
          <p className="text-sm">{allMarked || published ? t("debrief.publish.sub") : t("debrief.publish.blocked")}</p>
        </div>
        {published ? (
          <Link href={`/map/${map.workflow_id}`} className="inline-flex h-12 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-white px-5 font-black shadow-[var(--hard)] hover:translate-x-1 hover:translate-y-1 hover:shadow-none">
            {t("nav.map")} <ArrowRight className="size-5" aria-hidden />
          </Link>
        ) : (
          <button type="button" disabled={!allMarked} onClick={onPublish} className="inline-flex h-12 items-center gap-2 rounded-[5px] border-2 border-[var(--ink)] bg-[var(--ink)] px-6 font-black text-[var(--paper)] shadow-[4px_4px_0_0_var(--claros)] enabled:hover:translate-x-1 enabled:hover:translate-y-1 enabled:hover:shadow-none disabled:cursor-not-allowed disabled:opacity-40">
            <Check className="size-5" aria-hidden /> {t("debrief.publish")}
          </button>
        )}
      </div>
    </div>
  );
}

/* ---------------- live map at the side ---------------- */

function LiveMap({ map, stage }: { map: WorkMap; stage: Stage }) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const highlight = useLiveStore((s) => s.highlightedStepId);
  const shown = stage === "questions" ? Math.ceil(steps.length * 0.6) : steps.length;
  return (
    <aside aria-label={t("debrief.map")} className="xl:sticky xl:top-20 xl:self-start">
      <Panel tone="paper" className="p-4">
        <p className="mb-3 flex items-center justify-between font-extrabold">
          {t("debrief.map")}
          <span className="tnum font-mono text-xs">
            {shown}/{steps.length}
          </span>
        </p>
        <ol className="relative space-y-2 before:absolute before:bottom-2 before:left-[13px] before:top-2 before:w-0.5 before:bg-[var(--ink)]">
          {steps.map((s, i) => {
            const vis = i < shown;
            return (
              <li key={s.id} className={cn("relative flex items-start gap-2.5 transition-opacity duration-500", vis ? "opacity-100" : "opacity-30")}>
                <span className={cn("tnum relative z-10 grid size-7 shrink-0 place-items-center rounded-full border-2 border-[var(--ink)] font-mono text-[11px] font-bold", highlight === s.id ? "bg-[var(--claros)] text-white" : vis ? "bg-white" : "hatch bg-[var(--paper-2)]")}>
                  {s.order}
                </span>
                <div className={cn("min-w-0 flex-1 rounded-[4px] border-2 border-[var(--ink)] bg-white p-2", !vis && "border-dashed")}>
                  <p className="text-xs font-bold leading-snug">{s.title}</p>
                  {vis && s.guardrail_ids.length ? <p className="mt-1 flex items-center gap-1 text-[10px] font-black uppercase"><ShieldAlert className="size-3" aria-hidden /> {s.guardrail_ids.length} {t("map.lane.guardrails")}</p> : null}
                  {vis && s.conflict ? <p className="mt-1 flex items-center gap-1 text-[10px] font-black uppercase"><Users className="size-3" aria-hidden /> {t("map.conflict")}</p> : null}
                </div>
              </li>
            );
          })}
        </ol>
      </Panel>
    </aside>
  );
}
