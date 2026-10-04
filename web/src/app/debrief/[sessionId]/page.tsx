"use client";

/**
 * Debrief (same session as capture): one big question at a time next to the screen moment it's about,
 * then Claros explains the process back step by step — "That's right" / "Correct this" — then Publish.
 */
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, Mic, PencilLine, SkipForward } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import { LookedUpList, WhyTag, useLatestWhy, useLookedUp } from "@/components/claros/Evidence";
import { ClarosDot, EmptyState, ErrorState, SourceNote, useResource } from "@/components/claros/primitives";
import { ZoomShot } from "@/components/claros/Lightbox";
import { MapBuilding } from "@/components/claros/MapBuilding";
import { sortedSteps, stepHighlight } from "@/components/claros/mapUtils";
import { sendControl, useJoinSession, useLive, useLiveStore } from "@/components/claros/live";
import { Button, buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { getSession, getWorkflow, latestWorkflowId, publishSession } from "@/lib/api";
import { stopAllScreenCapture } from "@/capture/useScreenCapture";
import { endSessionLocal } from "@/voice/useClarosSession";
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
  // the capture's question count ("0 open") must not leak into the debrief and skip its questions;
  // and the debrief never needs the screen: end any share the capture left running
  useEffect(() => {
    useLiveStore.setState({ ledger: null });
    stopAllScreenCapture();
  }, [sessionId]);
  useJoinSession(res.data ? sessionId : null, "debrief", EXPERT, lang, res.data?.workflow_id);
  const { voice } = useLive(sessionId, "debrief", lang, EXPERT.name);
  const [stage, setStage] = useState<Stage>("questions");
  // confirmed by voice ("yes, that's how it works"): the server already published — show it
  const db = useLiveStore((s) => s.debrief);
  if (db?.phase === "done" && stage !== "publish") setStage("publish");
  // done (published) or leaving the page: Claros stops listening and talking, and the session closes
  const voiceEnd = voice.end;
  const finish = useCallback(() => {
    voiceEnd();
    stopAllScreenCapture();
    void endSessionLocal();
  }, [voiceEnd]);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      setTimeout(() => { if (!mounted.current) finish(); }, 0); // survives React dev's mount-unmount-mount
    };
  }, [finish]);

  const forceBuilding = useSearchParams().get("building") === "1"; // review flag: hold the map-building wait
  if (res.loading || forceBuilding) return <MapBuilding sessionId={sessionId} />;
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
      {stage === "publish" && <Publish map={map} sessionId={sessionId} onPublished={finish} byVoice={db?.phase === "done" ? { second: !!db.needs_second_run } : null} />}
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

/* ---------------- questions: voice-first, mirrors the spoken debrief ---------------- */

function Questions({ map, demo, voice, onDone }: { map: WorkMap; demo: boolean; voice: Voice; onDone: () => void }) {
  const { t } = useUi();
  const db = useLiveStore((s) => s.debrief);
  const transcript = useLiveStore((s) => s.transcript);
  // offline demo only: page through mock questions locally
  const mockQueue: Unknown[] = useMemo(() => (demo ? MOCK_UNKNOWNS.filter((u) => u.status === "deferred") : []), [demo]);
  const [mi, setMi] = useState(0);
  const latestWhy = useLatestWhy();
  const lookedUp = useLookedUp([]);

  // the server moved on to the spoken teach-back (or finished): follow it
  useEffect(() => {
    if (!demo && db && db.phase !== "questions") onDone();
  }, [demo, db, onDone]);

  const cur: Unknown | null | undefined = demo ? mockQueue[mi] : db?.current;
  const n = demo ? mi + 1 : Math.max(db?.asked ?? 0, 1);
  const voiceOn = voice.status === "connected";
  const skip = () => (demo ? setMi((x) => x + 1) : sendControl("debrief_skip"));
  // what the expert is saying right now (their answer, live), after this question appeared
  const [mark, setMark] = useState<{ id?: string; from: number }>({ from: 0 });
  if (cur?.id !== mark.id) setMark({ id: cur?.id, from: transcript.length }); // new question: answers start here
  const said = transcript.slice(mark.from).filter((l) => l.role === "user").map((l) => l.text).join(" ") || undefined;

  if (demo && !cur)
    return (
      <div className="claros-enter flex flex-col items-start gap-8 py-10">
        <ClarosDot size={72} />
        <h1 className="max-w-[18ch] text-5xl font-black leading-[1] tracking-[-0.045em] sm:text-6xl">{t("db.done")}</h1>
        <Button variant="claros" size="xl" autoFocus onClick={onDone}>
          {t("db.next")} <ArrowRight aria-hidden />
        </Button>
      </div>
    );

  if (!cur)
    return (
      <div className="claros-enter flex flex-col items-start gap-6 py-10" role="status" aria-live="polite">
        <ClarosDot size={72} />
        <h1 className="max-w-[20ch] text-4xl font-black leading-[1.05] tracking-[-0.04em] sm:text-5xl">{t("db.preparing")}</h1>
        {!voiceOn ? (
          <Button variant="claros" size="xl" onClick={() => void voice.start()}>
            <Mic aria-hidden /> {t("db.voice.on")}
          </Button>
        ) : null}
      </div>
    );

  const step: Step | undefined =
    map.steps.find((s) => s.id === cur.entity) ??
    map.steps.find((s) => cur.moment?.keyframe_ids?.some((k) => s.moment?.keyframe_ids.includes(k))) ??
    (cur.type === "conflict" ? map.steps.find((s) => s.conflict) : undefined);
  const kf = cur.moment?.keyframe_ids?.[0] ?? step?.moment?.keyframe_ids?.[0] ?? null;
  const q = cur.spoken_question ?? cur.hypothesis ?? "";
  return (
    <>
      <div key={cur.id} className="claros-enter grid items-start gap-10 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] xl:gap-14">
        <div className="min-w-0">
          <p className="tnum text-lg font-bold text-ink-2">{t("db.q.n", { n })}</p>
          <h1 className={cn("mt-6 font-black leading-[1.05] tracking-[-0.04em]", q.length > 80 ? "text-3xl sm:text-4xl" : "text-4xl sm:text-5xl")}>{q}</h1>
          <WhyTag why={latestWhy ?? (cur.created_t ? { when: t("ev.debrief.when"), what: step || cur.entity ? `${step?.title ?? cur.entity} · ${t(`q.kind.${cur.type}` as DictKey)}` : t(`q.kind.${cur.type}` as DictKey), scope: cur.scope === "company" || cur.scope === "personal_judgment" ? cur.scope : undefined } : null)} className="mt-5" />

          {/* the answer is spoken: show that Claros is listening, and the words as they come */}
          <div className="mt-10 rounded-base border-2 border-ink bg-card p-5 shadow-hard" aria-live="polite">
            {voiceOn || demo ? (
              <>
                <p className="flex items-center gap-3 text-xl font-extrabold">
                  <span className="relative flex size-4" aria-hidden>
                    <span className="absolute inline-flex size-full animate-ping rounded-full bg-claros opacity-60 motion-reduce:animate-none" />
                    <span className="relative inline-flex size-4 rounded-full bg-claros" />
                  </span>
                  {t("db.listening")}
                </p>
                <p className={cn("mt-3 min-h-[3.5rem] text-2xl leading-snug", said ? "font-semibold text-ink" : "text-ink-2")}>
                  {said ? `“${said}”` : t("db.listening.sub")}
                </p>
              </>
            ) : (
              <Button variant="claros" size="xl" onClick={() => void voice.start()}>
                <Mic aria-hidden /> {t("db.voice.on")}
              </Button>
            )}
          </div>
          <div className="mt-6">
            <Button variant="ghost" size="lg" onClick={skip}>
              <SkipForward aria-hidden /> {t("db.skip")}
            </Button>
          </div>
        </div>
        <div className="min-w-0 space-y-4">
          <ZoomShot
            group={`db-${cur.id}`}
            frames={[{ id: kf ?? `synthetic-${cur.id}`, keyframeId: kf, title: step?.title ?? cur.entity ?? "", highlight: step ? stepHighlight(step) : cur.entity ? { label: cur.entity, to: "?" } : null, caption: q }]}
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

function Publish({ map, sessionId, onPublished, byVoice }: { map: WorkMap; sessionId: string; onPublished: () => void; byVoice: { second: boolean } | null }) {
  const { t } = useUi();
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">(byVoice ? "done" : "idle");
  const [second, setSecond] = useState(byVoice?.second ?? false);
  const wrapped = useRef(false);
  useEffect(() => {
    if (!byVoice || wrapped.current) return;
    wrapped.current = true;
    onPublished(); // already published on the server; just wrap up the session (once)
  }, [byVoice, onPublished]);
  const publish = async () => {
    setState("busy");
    const r = await publishSession(sessionId); // the server approves the steps and rules; nothing is "published" locally
    if (!r) return setState("error");
    setSecond(r.needs_second_run);
    setState("done");
    onPublished();
  };
  const published = state === "done";
  return (
    <div className="claros-enter flex flex-col items-start gap-8 py-10">
      <h1 className="max-w-[18ch] text-5xl font-black leading-[1] tracking-[-0.045em] sm:text-7xl">{published ? t("db.published") : t("db.publish")}</h1>
      <p className="text-2xl text-ink-2">{published && second ? t("db.published.second") : t("db.publish.sub")}</p>
      {published ? (
        <Link href={`/map/${map.workflow_id}`} className={buttonVariants({ variant: "primary", size: "xl" })}>
          {t("db.view")} <ArrowRight aria-hidden />
        </Link>
      ) : (
        <Button variant="claros" size="xl" autoFocus disabled={state === "busy"} onClick={() => void publish()} className="h-20 px-10 text-3xl">
          <Check aria-hidden /> {t("db.publish")}
        </Button>
      )}
      {state === "error" ? <ErrorState message={t("db.publish.error")} onRetry={() => void publish()} /> : null}
    </div>
  );
}
