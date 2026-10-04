"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Check, Keyboard, Mic, OctagonAlert, Play, Send, SkipForward, Split, Square, X } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi } from "@/components/claros/i18n";
import { ClarosSays, EmptyState, ErrorState, Loading, Panel, ScreenThumb, SourceNote, useResource } from "@/components/claros/primitives";
import { sortedSteps } from "@/components/claros/mapUtils";
import { useJoinSession, useLive, useLiveStore } from "@/components/claros/live";
import { Button, buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { getSession, getWorkflow, latestWorkflowId } from "@/lib/api";
import { EXPERT, MOCK_UNKNOWNS } from "@/lib/mock";
import type { ExamCase, Step, Unknown, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

type Stage = "questions" | "teachback" | "exam" | "published";

export default function DebriefPage() {
  return (
    <Shell wide focus={{ label: "focus.debrief" }}>
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
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-4xl font-black tracking-[-0.04em]">{t("debrief.title")}</h1>
          <p className="mt-1 truncate text-sm font-semibold text-ink-2">{map.name}</p>
        </div>
        <div className="flex items-center gap-3">
          <SourceNote source={res.source} />
          <Stepper stage={stage} />
        </div>
      </div>
      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_360px]">
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
    <ol className="flex flex-wrap items-center gap-1" aria-label={t("debrief.title")}>
      {items.map(([s, label], i) => {
        const done = order.indexOf(stage) > order.indexOf(s);
        const cur = stage === s;
        return (
          <li key={s} className="flex items-center gap-1">
            <span
              aria-current={cur ? "step" : undefined}
              className={cn(
                "inline-flex items-center gap-1 rounded-[4px] border-2 border-ink px-2 py-0.5 text-xs font-bold",
                cur ? "bg-ink text-paper" : done ? "bg-ready text-on-fill" : "bg-card",
              )}
            >
              {done ? <Check className="size-3" aria-hidden /> : null}
              {label}
            </span>
            {i < items.length - 1 ? <span aria-hidden className="h-0.5 w-3 bg-ink" /> : null}
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
  const step = cur ? map.steps.find((s) => (cur.type === "conflict" ? s.conflict : false)) : undefined;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-5">
        <div className="rounded-[10px] border-[3px] border-ink bg-card px-5 py-2 shadow-hard-lg">
          <p className="text-xs font-bold">{t("debrief.ledger")}</p>
          <p key={remaining} className="ledger-tick tnum text-7xl font-black leading-none tracking-[-0.04em]" aria-live="polite">
            {remaining}
          </p>
        </div>
        <div className="mb-2 min-w-[12rem] flex-1">
          <div className="flex gap-1" aria-hidden>
            {queue.map((u, k) => (
              <span key={u.id} className={cn("h-3 flex-1 rounded-[2px] border-2 border-ink", k < i || remaining === 0 ? "bg-ready" : k === i ? "bg-claros" : "bg-card")} />
            ))}
          </div>
          <p className="mt-2 text-sm text-ink-2">{t("debrief.ledger.budget")}</p>
        </div>
      </div>

      {done ? (
        <Panel className="claros-enter flex flex-wrap items-center gap-4 p-5">
          <span className="grid size-11 place-items-center rounded-full border-2 border-ink bg-ready text-on-fill">
            <Check className="size-6" aria-hidden />
          </span>
          <p className="flex-1 text-xl font-black">{t("debrief.ledger.done")}</p>
          <Button variant="primary" size="lg" autoFocus onClick={onDone}>
            {t("debrief.teachback")} <ArrowRight aria-hidden />
          </Button>
        </Panel>
      ) : (
        <div key={cur.id} className="claros-enter grid gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div>
            <p className="mb-2 text-sm font-bold">{t("debrief.moment")}</p>
            <ScreenThumb
              keyframeId={cur.moment?.keyframe_ids?.[0] ?? step?.moment?.keyframe_ids?.[0]}
              title={cur.entity ?? step?.state_signature.view ?? ""}
              highlight={cur.entity ? { label: cur.entity, to: cur.hypothesis ? "?" : null } : null}
              className="shadow-hard"
            />
            {cur.type === "conflict" ? (
              <p className="mt-3 inline-flex items-center gap-1.5 rounded-[4px] border-2 border-ink bg-partial px-2 py-0.5 text-xs font-bold text-on-fill">
                <Split className="size-3.5" aria-hidden /> {t("q.kind.conflict")}
              </p>
            ) : null}
          </div>
          <div className="rounded-[10px] border-[3px] border-ink bg-claros p-4 text-claros-ink shadow-hard-lg">
            <ClarosSays speaking={voice.isSpeaking}>
              <p className="text-xs font-bold opacity-85">{t("debrief.question")}</p>
              <p className="mt-1 text-xl font-black leading-snug tracking-[-0.02em]">{cur.spoken_question}</p>
            </ClarosSays>
            {cur.hypothesis ? <p className="mt-3 rounded-[4px] border-2 border-ink bg-card p-2 text-sm text-ink">{cur.hypothesis}</p> : null}
            {typing ? (
              <form
                className="mt-4 space-y-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (answer.trim() && voice.status === "connected") voice.sendText(answer);
                  advance();
                }}
              >
                <Textarea autoFocus value={answer} onChange={(e) => setAnswer(e.target.value)} rows={3} className="bg-card text-ink" aria-label={t("debrief.answer.type")} />
                <Button type="submit" variant="secondary" size="sm">
                  <Send aria-hidden /> {t("debrief.answer.submit")}
                </Button>
              </form>
            ) : (
              <div className="mt-4 flex flex-wrap gap-2">
                <Button variant="secondary" onClick={() => (voice.status === "connected" ? advance() : voice.start().catch(() => advance()))}>
                  <Mic aria-hidden /> {t("debrief.answer.voice")}
                </Button>
                <Button variant="outline" onClick={() => setTyping(true)}>
                  <Keyboard aria-hidden /> {t("debrief.answer.type")}
                </Button>
                <Button variant="ghost" onClick={advance} className="text-claros-ink hover:border-claros-ink hover:bg-transparent">
                  <SkipForward aria-hidden /> {t("debrief.answer.skip")}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- teach-back ---------------- */

function TeachBack({ map, voice, onDone }: { map: WorkMap; voice: Voice; onDone: () => void }) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const liveHighlight = useLiveStore((s) => s.highlightedStepId);
  const [playing, setPlaying] = useState(false);
  const [k, setK] = useState(-1);
  const [stopped, setStopped] = useState<string[]>([]);
  const [fixes, setFixes] = useState<Record<string, string>>({});

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
  const stoppedSteps = stopped.map((id) => steps.find((s) => s.id === id)).filter(Boolean) as Step[];

  return (
    <div className="space-y-6">
      <Panel className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-black tracking-[-0.02em]">{t("debrief.teachback")}</h2>
            <p className="text-sm text-ink-2">{t("debrief.teachback.sub")}</p>
          </div>
          <Button variant="claros" onClick={play} disabled={playing}>
            <Play aria-hidden /> {t("debrief.teachback.play")}
          </Button>
        </div>
        <ol className="mt-4 space-y-2">
          {steps.map((s) => {
            const on = s.id === active;
            const flagged = stopped.includes(s.id);
            return (
              <li
                key={s.id}
                className={cn(
                  "flex items-center gap-3 rounded-[6px] border-2 border-ink p-2.5 transition-[background-color,transform,box-shadow] duration-200",
                  on ? "-translate-x-1 -translate-y-1 bg-claros-soft shadow-claros" : flagged ? "bg-missing/30" : "bg-card",
                )}
                aria-current={on ? "step" : undefined}
              >
                <span className="tnum grid size-7 shrink-0 place-items-center rounded-[4px] border-2 border-ink bg-ink font-mono text-xs font-black text-paper">{s.order}</span>
                <span className="flex-1 text-sm font-semibold">{s.decision?.description ?? s.title}</span>
                {flagged ? <OctagonAlert className="size-4" aria-hidden /> : null}
                {on && !flagged ? (
                  <Button
                    variant="destructive"
                    size="xs"
                    onClick={() => {
                      setStopped((x) => [...x, s.id]);
                      setPlaying(false);
                    }}
                  >
                    <Square aria-hidden /> {t("debrief.stop")}
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ol>
      </Panel>

      <Panel className="p-4 sm:p-5">
        <h2 className="font-extrabold">{t("debrief.correction")}</h2>
        {stoppedSteps.length ? (
          <ul className="mt-3 space-y-3">
            {stoppedSteps.map((s) => (
              <li key={s.id} className="overflow-hidden rounded-[6px] border-2 border-ink text-[13px]">
                <p className="bg-missing/35 px-3 py-1.5">
                  <span aria-hidden className="mr-2 font-mono font-bold">
                    −
                  </span>
                  <del className="decoration-2">{s.decision?.description ?? s.title}</del>
                </p>
                <form
                  className="flex flex-col gap-2 border-t-2 border-ink bg-ready/25 p-2 sm:flex-row"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const v = fixes[s.id]?.trim();
                    if (v && voice.status === "connected") voice.sendText(`Correction for "${s.title}": ${v}`);
                  }}
                >
                  <Textarea
                    rows={1}
                    value={fixes[s.id] ?? ""}
                    onChange={(e) => setFixes((f) => ({ ...f, [s.id]: e.target.value }))}
                    placeholder={t("debrief.correction.ph")}
                    aria-label={t("debrief.correction.ph")}
                    className="min-h-10 flex-1 bg-card"
                  />
                  <Button type="submit" variant="outline" size="sm" disabled={!fixes[s.id]?.trim()}>
                    <Check aria-hidden /> {t("debrief.correction.save")}
                  </Button>
                </form>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-ink-2">{t("debrief.correction.empty")}</p>
        )}
        <Button variant="primary" size="lg" className="mt-5" onClick={onDone}>
          {t("debrief.exam")} <ArrowRight aria-hidden />
        </Button>
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
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-black tracking-[-0.02em]">{t("debrief.exam")}</h2>
        <p className="text-sm text-ink-2">{t("debrief.exam.sub")}</p>
      </div>
      {!cases.length ? <EmptyState>{t("debrief.exam.empty")}</EmptyState> : null}
      <div className="grid gap-4 md:grid-cols-3">
        {cases.map((c, i) => (
          <Panel key={c.variant} className={cn("flex flex-col p-4", c.expert_verdict === "correct" && "bg-ready/30", c.expert_verdict === "wrong" && "bg-missing/30")}>
            <p className="font-extrabold leading-snug">{c.variant}</p>
            <div className="mt-3 flex-1 rounded-[6px] border-2 border-claros bg-claros-soft p-2.5">
              <p className="text-[11px] font-bold text-claros">{t("debrief.exam.predicts")}</p>
              <p className="mt-0.5 text-sm font-semibold">{c.predicted}</p>
              <div className="mt-2 flex items-center gap-2">
                <div className="h-2 flex-1 overflow-hidden rounded-[2px] border-2 border-ink bg-card">
                  <div className="h-full bg-claros" style={{ width: `${Math.round(c.confidence * 100)}%` }} />
                </div>
                <span className="tnum font-mono text-[11px]">
                  {Math.round(c.confidence * 100)}% {t("debrief.exam.confidence")}
                </span>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={published}
                aria-pressed={c.expert_verdict === "correct"}
                onClick={() => mark(i, "correct")}
                className={cn(c.expert_verdict === "correct" && "bg-ready text-on-fill hover:bg-ready")}
              >
                <Check aria-hidden /> {t("debrief.exam.right")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={published}
                aria-pressed={c.expert_verdict === "wrong"}
                onClick={() => mark(i, "wrong")}
                className={cn(c.expert_verdict === "wrong" && "bg-missing text-on-fill hover:bg-missing")}
              >
                <X aria-hidden /> {t("debrief.exam.wrong")}
              </Button>
            </div>
          </Panel>
        ))}
      </div>

      <div className={cn("flex flex-wrap items-center gap-4 rounded-[10px] border-[3px] border-ink p-5 shadow-hard-lg", published ? "bg-ready text-on-fill" : "bg-card")}>
        <div className="min-w-0 flex-1">
          <p className="text-2xl font-black tracking-[-0.03em]">{published ? t("debrief.published") : t("debrief.publish")}</p>
          <p className="text-sm">{allMarked || published ? t("debrief.publish.sub") : t("debrief.publish.blocked")}</p>
        </div>
        {published ? (
          <Link href={`/map/${map.workflow_id}`} className={buttonVariants({ variant: "secondary", size: "lg" })}>
            {t("wf.view")} <ArrowRight aria-hidden />
          </Link>
        ) : (
          <Button variant="primary" size="lg" disabled={!allMarked} onClick={onPublish}>
            <Check aria-hidden /> {t("debrief.publish")}
          </Button>
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
        <ol className="relative space-y-2 before:absolute before:bottom-2 before:left-[13px] before:top-2 before:w-0.5 before:bg-ink">
          {steps.map((s, i) => {
            const vis = i < shown;
            return (
              <li key={s.id} className={cn("relative flex items-start gap-2.5 transition-opacity duration-500", vis ? "opacity-100" : "opacity-35")}>
                <span
                  className={cn(
                    "tnum relative z-10 grid size-7 shrink-0 place-items-center rounded-full border-2 border-ink font-mono text-[11px] font-bold",
                    highlight === s.id ? "bg-claros text-claros-ink" : vis ? "bg-card" : "hatch bg-paper-2",
                  )}
                >
                  {s.order}
                </span>
                <div className={cn("min-w-0 flex-1 rounded-[4px] border-2 border-ink bg-card p-2", !vis && "border-dashed")}>
                  <p className="text-xs font-bold leading-snug">{s.title}</p>
                  {vis && s.guardrail_ids.length ? (
                    <p className="mt-1 flex items-center gap-1 text-[11px] font-bold">
                      <OctagonAlert className="size-3" aria-hidden /> {s.guardrail_ids.length} {t("map.rules")}
                    </p>
                  ) : null}
                  {vis && s.conflict ? (
                    <p className="mt-1 flex items-center gap-1 text-[11px] font-bold">
                      <Split className="size-3" aria-hidden /> {t("map.conflict")}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      </Panel>
    </aside>
  );
}
