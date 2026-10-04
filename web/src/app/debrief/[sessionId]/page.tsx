"use client";

/**
 * Debrief (same session as capture): one big question at a time next to the screen moment it's about,
 * then Claros explains the process back step by step — "That's right" / "Correct this" — then Publish.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Check, Mic, PencilLine, SkipForward } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import { LookedUpList, WhyTag, useLatestWhy, useLookedUp } from "@/components/claros/Evidence";
import { ClarosDot, EmptyState, ErrorState, Loading, SourceNote, useResource } from "@/components/claros/primitives";
import { ZoomShot } from "@/components/claros/Lightbox";
import { sortedSteps, stepHighlight } from "@/components/claros/mapUtils";
import { useJoinSession, useLive, useLiveStore } from "@/components/claros/live";
import { Button, buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { getSession, getWorkflow, latestWorkflowId } from "@/lib/api";
import { EXPERT, MOCK_UNKNOWNS } from "@/lib/mock";
import type { Step, Unknown, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

type Stage = "questions" | "teachback" | "publish";
const EMPTY = "__empty__";
type Voice = ReturnType<typeof useLive>["voice"];

export default function DebriefPage() {
  return (
    <Shell focus={{ label: "focus.debrief" }}>
      <Debrief />
    </Shell>
  );
}

function Debrief() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { t, lang } = useUi();
  const res = useResource(async () => {
    // Wait for THIS capture's map (the server builds it after Done) — never fall back to another map.
    const t0 = Date.now();
    for (;;) {
      const sess = await getSession(sessionId);
      if (sess.source !== "live") return getWorkflow(await latestWorkflowId()); // offline demo only
      const { workflow_id: wid, extra } = sess.data;
      const status = extra?.map_status;
      if (status === "empty") throw new Error(EMPTY);
      if (status === "failed") throw new Error("The map couldn't be built.");
      if (wid && (status === "ready" || (!status && Date.now() - t0 > 8000))) return getWorkflow(wid);
      if (Date.now() - t0 > 180_000) throw new Error("The map is taking too long to build.");
      await new Promise((r) => setTimeout(r, 1500));
    }
  }, [sessionId]);
  useJoinSession(res.data ? sessionId : null, "debrief", EXPERT, lang, res.data?.workflow_id);
  const { voice } = useLive(sessionId, "debrief", lang, EXPERT.name);
  const [stage, setStage] = useState<Stage>("questions");

  if (res.loading) return <Loading label={t("db.building")} rows={3} />;
  if (res.error === EMPTY)
    return (
      <EmptyState action={<Link href="/" className={buttonVariants({ variant: "primary", size: "lg" })}>{t("db.empty.cta")}</Link>}>
        <p className="text-2xl font-bold">{t("db.empty")}</p>
      </EmptyState>
    );
  if (res.error || !res.data) return <ErrorState message={res.error} onRetry={res.retry} />;
  const map = res.data;

  return (
    <div>
      <div className="mb-10 flex flex-wrap items-center justify-between gap-4">
        <p className="min-w-0 text-xl font-bold text-ink-2">{map.name}</p>
        <div className="flex items-center gap-3">
          <SourceNote source={res.source} />
          <Stepper stage={stage} />
        </div>
      </div>
      {stage === "questions" && <Questions map={map} demo={res.source === "mock"} voice={voice} onDone={() => setStage("teachback")} />}
      {stage === "teachback" && <TeachBack map={map} voice={voice} onDone={() => setStage("publish")} />}
      {stage === "publish" && <Publish map={map} />}
      <span className="sr-only">{t("debrief.title")}</span>
    </div>
  );
}

function Stepper({ stage }: { stage: Stage }) {
  const { t } = useUi();
  const items: [Stage, string][] = [
    ["questions", t("debrief.ledger")],
    ["teachback", t("debrief.teachback")],
    ["publish", t("db.publish")],
  ];
  const order: Stage[] = ["questions", "teachback", "publish"];
  return (
    <ol className="flex flex-wrap items-center gap-1.5" aria-label={t("debrief.title")}>
      {items.map(([s, label], i) => {
        const done = order.indexOf(stage) > order.indexOf(s);
        const cur = stage === s;
        return (
          <li key={s} className="flex items-center gap-1.5">
            <span
              aria-current={cur ? "step" : undefined}
              className={cn("inline-flex items-center gap-1.5 rounded-[4px] border-2 border-ink px-2.5 py-1 text-sm font-bold", cur ? "bg-ink text-paper" : done ? "bg-ready text-on-fill" : "bg-card text-ink-2")}
            >
              {done ? <Check className="size-4" aria-hidden /> : null}
              {label}
            </span>
            {i < items.length - 1 ? <span aria-hidden className="h-0.5 w-4 bg-ink" /> : null}
          </li>
        );
      })}
    </ol>
  );
}

/* ---------------- questions: one at a time, next to its screen moment ---------------- */

function Questions({ map, demo, voice, onDone }: { map: WorkMap; demo: boolean; voice: Voice; onDone: () => void }) {
  const { t } = useUi();
  const liveLedger = useLiveStore((s) => s.ledger);
  const queue: Unknown[] = useMemo(
    () => [...(demo ? MOCK_UNKNOWNS.filter((u) => u.status === "deferred") : []), ...map.open_unknowns].filter((u, i, a) => a.findIndex((x) => x.id === u.id) === i),
    [map.open_unknowns, demo],
  );
  const [i, setI] = useState(0);
  const total = Math.max(queue.length, 1);
  const remaining = liveLedger ? liveLedger.open : Math.max(0, queue.length - i);
  const cur: Unknown | undefined = liveLedger?.top ?? queue[i];
  const done = remaining === 0 || !cur;
  const latestWhy = useLatestWhy();
  const lookedUp = useLookedUp(
    demo
      ? MOCK_UNKNOWNS.filter((u) => u.status === "resolved").map((u) => ({
          type: "looked_up" as const,
          unknown_summary: u.entity ?? u.type,
          answer: u.resolution ?? "",
          source: u.resolution_source?.startsWith("app_docs:") ? { kind: "app_docs" as const, title: "docs.erpnext.com", url: u.resolution_source.slice(9) } : { kind: "general" as const, title: "LLM" },
        }))
      : [],
  );

  const advance = () => {
    setI((x) => x + 1);
  };
  const step: Step | undefined = cur
    ? map.steps.find((s) => s.id === cur.entity) ?? map.steps.find((s) => cur.moment?.keyframe_ids?.some((k) => s.moment?.keyframe_ids.includes(k))) ?? (cur.type === "conflict" ? map.steps.find((s) => s.conflict) : undefined)
    : undefined;

  if (done)
    return (
      <div className="claros-enter flex flex-col items-start gap-8 py-10">
        <ClarosDot size={72} />
        <h1 className="max-w-[18ch] text-5xl font-black leading-[1] tracking-[-0.045em] sm:text-6xl">{t("db.done")}</h1>
        <Button variant="claros" size="xl" autoFocus onClick={onDone}>
          {t("db.next")} <ArrowRight aria-hidden />
        </Button>
      </div>
    );

  const kf = cur.moment?.keyframe_ids?.[0] ?? step?.moment?.keyframe_ids?.[0] ?? null;
  return (
    <>
    <div key={cur.id} className="claros-enter grid items-start gap-10 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] xl:gap-14">
      <div className="min-w-0">
        <p className="tnum text-lg font-bold text-ink-2">{t("db.q", { n: Math.min(i + 1, total), total })}</p>
        <div className="mt-3 flex gap-1.5" aria-hidden>
          {queue.map((u, k) => (
            <span key={u.id} className={cn("h-3 flex-1 rounded-[2px] border-2 border-ink", k < i ? "bg-ready" : k === i ? "bg-claros" : "bg-card")} />
          ))}
        </div>
        <h1 className={cn("mt-8 font-black leading-[1.05] tracking-[-0.04em]", (cur.spoken_question ?? cur.hypothesis ?? "").length > 80 ? "text-3xl sm:text-4xl" : "text-4xl sm:text-5xl")}>{cur.spoken_question ?? cur.hypothesis}</h1>
        {cur.hypothesis && cur.spoken_question && cur.type !== "conflict" ? <p className="mt-5 text-xl text-ink-2">{cur.hypothesis}</p> : null}
        <WhyTag why={latestWhy ?? (cur.created_t ? { when: t("ev.debrief.when"), what: step || cur.entity ? `${step?.title ?? cur.entity} · ${t(`q.kind.${cur.type}` as DictKey)}` : t(`q.kind.${cur.type}` as DictKey), scope: cur.scope === "company" || cur.scope === "personal_judgment" ? cur.scope : undefined } : null)} className="mt-5" />

          <div className="mt-10 flex flex-wrap gap-3">
            <Button variant="claros" size="xl" onClick={() => (voice.status === "connected" ? advance() : voice.start().catch(() => advance()))}>
              <Mic aria-hidden /> {t("db.voice")}
            </Button>
            <Button variant="ghost" size="xl" onClick={advance}>
              <SkipForward aria-hidden /> {t("db.skip")}
            </Button>
          </div>
      </div>
      <div className="min-w-0 space-y-4">
        <ZoomShot
          group={`db-${cur.id}`}
          frames={[{ id: kf ?? `synthetic-${cur.id}`, keyframeId: kf, title: step?.title ?? cur.entity ?? "", highlight: step ? stepHighlight(step) : cur.entity ? { label: cur.entity, to: "?" } : null, caption: cur.spoken_question ?? "" }]}
          priority
          className="shadow-hard-lg"
        />
      </div>
    </div>
    <LookedUpList items={lookedUp} className="mt-14 border-t-2 border-ink pt-6" />
    </>
  );
}

/* ---------------- teach-back: step by step, right / correct ---------------- */

function TeachBack({ map, voice, onDone }: { map: WorkMap; voice: Voice; onDone: () => void }) {
  const { t } = useUi();
  const steps = sortedSteps(map);
  const liveHighlight = useLiveStore((s) => s.highlightedStepId);
  const [k, setK] = useState(0);
  const [fixing, setFixing] = useState(false);
  const [fix, setFix] = useState("");
  const [fixed, setFixed] = useState<Record<string, string>>({});
  const [ok, setOk] = useState<string[]>([]);

  useEffect(() => {
    if (voice.status === "connected") voice.sendText("teach back");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const i = steps.findIndex((s) => s.id === liveHighlight);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (i >= 0) setK(i);
  }, [liveHighlight, steps]);

  const step = steps[k];
  if (!step) return null;
  const next = () => {
    setFixing(false);
    setFix("");
    if (k + 1 >= steps.length) onDone();
    else setK(k + 1);
  };
  const kf = step.moment?.keyframe_ids?.[0] ?? null;

  return (
    <div className="grid items-start gap-10 xl:grid-cols-[minmax(0,4fr)_minmax(0,8fr)] xl:gap-14">
      <ol className="order-last space-y-2 xl:order-none" aria-label={t("debrief.teachback")}>
        {steps.map((s, i) => {
          const on = i === k;
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => setK(i)}
                aria-current={on ? "step" : undefined}
                className={cn(
                  "flex w-full items-center gap-3 rounded-base border-2 border-ink p-3 text-left text-lg font-bold leading-snug",
                  on ? "translate-x-1 translate-y-1 bg-claros-soft" : "press bg-card shadow-hard",
                )}
              >
                <span
                  className={cn(
                    "tnum grid size-10 shrink-0 place-items-center rounded-base border-2 border-ink font-mono text-base font-black",
                    fixed[s.id] ? "bg-partial text-on-fill" : ok.includes(s.id) ? "bg-ready text-on-fill" : on ? "bg-claros text-claros-ink" : "bg-ink text-paper",
                  )}
                >
                  {ok.includes(s.id) && !fixed[s.id] ? <Check className="size-5" aria-hidden /> : fixed[s.id] ? <PencilLine className="size-5" aria-hidden /> : s.order}
                </span>
                <span className="min-w-0">{s.title}</span>
              </button>
            </li>
          );
        })}
      </ol>

      <section key={step.id} className="claros-enter min-w-0">
        <p className="tnum text-lg font-bold text-ink-2">
          {t("db.tb.title")} · {t("db.step", { n: k + 1, total: steps.length })}
        </p>
        <h1 className="mt-4 text-4xl font-black leading-[1.05] tracking-[-0.04em] sm:text-5xl">{step.decision?.description ?? step.title}</h1>
        <ZoomShot
          group={`tb-${step.id}`}
          frames={[{ id: kf ?? `synthetic-${step.id}`, keyframeId: kf, seed: step.order, title: step.title, highlight: stepHighlight(step), caption: `${step.order}. ${step.title}` }]}
          className="mt-8 shadow-hard-lg"
          priority
        />
        {fixing ? (
          <form
            className="mt-8 space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              const v = fix.trim();
              if (!v) return;
              if (voice.status === "connected") voice.sendText(`Correction for "${step.title}": ${v}`);
              setFixed((f) => ({ ...f, [step.id]: v }));
              next();
            }}
          >
            <Textarea autoFocus rows={2} value={fix} onChange={(e) => setFix(e.target.value)} placeholder={t("db.tb.fix.ph")} aria-label={t("db.tb.fix.ph")} className="bg-card text-lg" />
            <div className="flex flex-wrap gap-3">
              <Button type="submit" variant="primary" size="lg" disabled={!fix.trim()}>
                <Check aria-hidden /> {t("db.tb.fix.save")}
              </Button>
              <Button type="button" variant="ghost" size="lg" onClick={() => setFixing(false)}>
                {t("common.close")}
              </Button>
            </div>
          </form>
        ) : (
          <div className="mt-8 flex flex-wrap gap-4">
            <Button
              variant="primary"
              size="xl"
              autoFocus
              onClick={() => {
                setOk((o) => [...new Set([...o, step.id])]);
                next();
              }}
            >
              <Check aria-hidden /> {t("db.tb.right")}
            </Button>
            <Button variant="secondary" size="xl" onClick={() => setFixing(true)}>
              <PencilLine aria-hidden /> {t("db.tb.fix")}
            </Button>
          </div>
        )}
        {fixed[step.id] ? <p className="mt-4 text-lg font-semibold">✎ {fixed[step.id]}</p> : null}
      </section>
    </div>
  );
}

/* ---------------- publish ---------------- */

function Publish({ map }: { map: WorkMap }) {
  const { t } = useUi();
  const [published, setPublished] = useState(false);
  return (
    <div className="claros-enter flex flex-col items-start gap-8 py-10">
      <h1 className="max-w-[18ch] text-5xl font-black leading-[1] tracking-[-0.045em] sm:text-7xl">{published ? t("db.published") : t("db.publish")}</h1>
      <p className="text-2xl text-ink-2">{t("db.publish.sub")}</p>
      {published ? (
        <Link href={`/map/${map.workflow_id}`} className={buttonVariants({ variant: "primary", size: "xl" })}>
          {t("db.view")} <ArrowRight aria-hidden />
        </Link>
      ) : (
        <Button variant="claros" size="xl" autoFocus onClick={() => setPublished(true)} className="h-20 px-10 text-3xl">
          <Check aria-hidden /> {t("db.publish")}
        </Button>
      )}
    </div>
  );
}
