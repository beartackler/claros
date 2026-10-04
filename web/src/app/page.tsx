"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, ChevronDown, CircleHelp, Inbox, Mic, MicVocal, Plus, Split, Undo2, UserRound } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import {
  ClarosDot,
  EmptyState,
  ErrorState,
  Loading,
  SectionHeader,
  SourceNote,
  useResource,
} from "@/components/claros/primitives";
import { LEVEL_BG, MasteryStrip, RequestCard, RequestStatus, WorkflowCard, isJunkRequest, useHealth } from "@/components/claros/cards";
import { openQuestions, sortedSteps, type OpenQuestion } from "@/components/claros/mapUtils";
import { QuestionLine } from "@/components/claros/questions";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { createSession, getMastery, getWorkflow, listRequests, listWorkflows, type Result } from "@/lib/api";
import { EXPERT, LEA, type WorkflowSummary } from "@/lib/mock";
import { mergeMastery } from "@/lib/localMastery";
import type { MasteryNode, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

export default function HomePage() {
  return (
    <Shell>
      <Home />
    </Shell>
  );
}

function Home() {
  const { role } = useUi();
  return role === "learner" ? <LearnerHome /> : <ExpertHome />;
}

/* =====================================================================
   EXPERT HOME — demand first: learner requests, then questions, then maps
   ===================================================================== */

function ExpertHome() {
  const { t, lang } = useUi();
  const router = useRouter();
  const [starting, setStarting] = useState(false);
  const teach = async () => {
    setStarting(true);
    const r = await createSession({ mode: "capture", user: EXPERT, lang });
    router.push(`/capture/${r.data.session_id}`);
  };

  return (
    <div className="space-y-12">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <div className="max-w-[60ch]">
          <h1 className="text-4xl font-black leading-[1] tracking-[-0.04em] sm:text-5xl">{t("home.expert.title")}</h1>
          <p className="mt-3 text-base leading-relaxed text-ink-2">{t("home.expert.sub")}</p>
        </div>
      </header>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <RequestQueue />
        <QuestionsForYou />
      </div>

      <ExpertWorkflows />

      <section aria-labelledby="teach" className="flex flex-col gap-4 rounded-base border-2 border-dashed border-ink p-5 sm:flex-row sm:items-center sm:p-6">
        <span className="grid size-12 shrink-0 place-items-center rounded-base border-2 border-ink bg-expert text-on-fill">
          <Plus className="size-6" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="teach" className="text-xl font-extrabold tracking-[-0.02em]">
            {t("home.teach.title")}
          </h2>
          <p className="mt-0.5 text-sm text-ink-2">{t("home.teach.sub")}</p>
        </div>
        <Button variant="secondary" size="lg" onClick={teach} loading={starting}>
          {!starting ? <MicVocal aria-hidden /> : null}
          {t("home.teach.cta")}
        </Button>
      </section>
    </div>
  );
}

const SNOOZE_KEY = "claros.snoozed";
function useSnoozed() {
  const [ids, setIds] = useState<string[]>([]);
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIds(JSON.parse(localStorage.getItem(SNOOZE_KEY) || "[]"));
    } catch {
      /* storage blocked */
    }
  }, []);
  const save = (next: string[]) => {
    setIds(next);
    try {
      localStorage.setItem(SNOOZE_KEY, JSON.stringify(next));
    } catch {
      /* storage blocked */
    }
  };
  return { ids, snooze: (id: string) => save([...ids, id]), restore: (id: string) => save(ids.filter((x) => x !== id)) };
}

function RequestQueue() {
  const { t } = useUi();
  const reqs = useResource(listRequests, []);
  const sn = useSnoozed();
  const all = [...(reqs.data ?? [])].filter((r) => !isJunkRequest(r)).sort((a, b) => b.created_at - a.created_at);
  const open = all.filter((r) => r.status === "open" && !sn.ids.includes(r.id));
  const inProgress = all.filter((r) => r.status === "accepted" || r.status === "recorded");
  const later = all.filter((r) => r.status === "open" && sn.ids.includes(r.id));

  return (
    <section aria-labelledby="requests">
      <SectionHeader id="requests" title={t("home.requests.title")} count={open.length} sub={t("home.requests.sub")} aside={<SourceNote source={reqs.source} />} />
      {reqs.loading ? (
        <Loading rows={2} />
      ) : reqs.error ? (
        <ErrorState message={reqs.error} onRetry={reqs.retry} />
      ) : (
        <div className="space-y-4">
          {open.length ? (
            open.map((r) => <RequestCard key={r.id} r={r} onLater={() => sn.snooze(r.id)} />)
          ) : (
            <EmptyState icon={<Inbox aria-hidden />}>{t("home.requests.empty")}</EmptyState>
          )}
          {inProgress.map((r) => (
            <RequestCard key={r.id} r={r} />
          ))}
          {later.length ? (
            <Collapsible>
              <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="group/later -ml-2" />}>
                <ChevronDown className="transition-transform group-data-panel-open/later:rotate-180" aria-hidden />
                {t("req.laterList", { n: later.length })}
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul className="mt-2 space-y-2">
                  {later.map((r) => (
                    <li key={r.id} className="flex items-center gap-3 rounded-base border-2 border-dashed border-ink bg-card p-3">
                      <span className="min-w-0 flex-1 truncate text-sm font-bold">{r.workflow_hint}</span>
                      <span className="hidden text-xs text-ink-2 sm:inline">{r.requested_by.name}</span>
                      <Button variant="outline" size="xs" onClick={() => sn.restore(r.id)}>
                        <Undo2 aria-hidden /> {t("common.undo")}
                      </Button>
                    </li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          ) : null}
        </div>
      )}
    </section>
  );
}

type OpenQ = { q: OpenQuestion; map: WorkMap };

async function loadQuestions(): Promise<Result<OpenQ[]>> {
  const wf = await listWorkflows();
  const mine = wf.data.filter((w) => w.experts.some((e) => e.id === EXPERT.id));
  const pool = (mine.length ? mine : wf.data).slice(0, 8);
  const maps = await Promise.all(pool.map((w) => getWorkflow(w.workflow_id)));
  const out: OpenQ[] = maps.flatMap((m) => openQuestions(m.data).map((q) => ({ q, map: m.data })));
  const rank = (x: OpenQ) => (x.q.kind === "conflict" ? 0 : x.q.unknown.type === "coverage" ? 1 : 2);
  const prio = (x: OpenQ) => (x.q.kind === "unknown" ? x.q.unknown.priority : 1);
  return { data: out.sort((a, b) => rank(a) - rank(b) || prio(b) - prio(a)), source: wf.source };
}

function QuestionsForYou() {
  const { t, lang } = useUi();
  const router = useRouter();
  const qs = useResource(loadQuestions, []);
  const [busy, setBusy] = useState<string | null>(null);
  const answer = async (x: OpenQ) => {
    setBusy(x.q.id);
    const s = await createSession({ mode: "debrief", user: EXPERT, lang, workflow_id: x.map.workflow_id });
    router.push(`/debrief/${s.data.session_id}`);
  };
  const items = qs.data ?? [];
  return (
    <section aria-labelledby="questions">
      <SectionHeader id="questions" title={t("home.questions.title")} count={items.length} sub={t("home.questions.sub")} />
      {qs.loading ? (
        <Loading rows={3} />
      ) : qs.error ? (
        <ErrorState message={qs.error} onRetry={qs.retry} />
      ) : !items.length ? (
        <EmptyState icon={<CircleHelp aria-hidden />}>{t("home.questions.empty")}</EmptyState>
      ) : (
        <ul className="space-y-3">
          {items.slice(0, 5).map((x) => {
            const conflict = x.q.kind === "conflict";
            const type = x.q.kind === "conflict" ? "conflict" : x.q.unknown.type;
            const key = `${x.map.workflow_id}-${x.q.id}`;
            return (
              <li key={key} className={cn("rounded-base border-2 border-ink p-4", conflict ? "bg-partial/25 shadow-hard" : "bg-card")}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={conflict ? "partial" : type === "coverage" ? "expert" : "neutral"}>
                    {conflict ? <Split aria-hidden /> : type === "coverage" ? <UserRound aria-hidden /> : <CircleHelp aria-hidden />}
                    {t(`q.kind.${type}` as DictKey)}
                  </Badge>
                  <span className="min-w-0 truncate text-xs font-semibold text-ink-2">{t("q.in", { name: x.map.name })}</span>
                </div>
                <QuestionLine map={x.map} q={x.q} viewer={EXPERT} className="mt-2 font-bold leading-snug" />
                {x.q.kind === "conflict" ? <p className="mt-1 text-xs text-ink-2">{t("q.conflict.at", { step: x.q.step.title })}</p> : null}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button variant="claros" size="sm" onClick={() => answer(x)} loading={busy === x.q.id} disabled={busy != null && busy !== x.q.id}>
                    {busy !== x.q.id ? <Mic aria-hidden /> : null}
                    {t("q.answer")}
                  </Button>
                  <Link
                    href={`/map/${encodeURIComponent(x.map.workflow_id)}${x.q.kind === "conflict" ? `?step=${x.q.step.id}&focus=conflict` : ""}`}
                    className={buttonVariants({ variant: "ghost", size: "sm" })}
                  >
                    {t("q.see")}
                  </Link>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function ExpertWorkflows() {
  const { t } = useUi();
  const wf = useResource(listWorkflows, []);
  const mine = (wf.data ?? []).filter((w) => w.experts.some((e) => e.id === EXPERT.id));
  const list = mine.length ? mine : wf.data ?? [];
  const health = useHealth(list);
  return (
    <section aria-labelledby="your-wf">
      <SectionHeader
        id="your-wf"
        title={mine.length || !wf.data?.length ? t("home.workflows.title") : t("home.workflows.team")}
        aside={
          <Link href="/map" className={buttonVariants({ variant: "link", size: "sm" })}>
            {t("nav.workflows")} <ArrowRight aria-hidden />
          </Link>
        }
      />
      {wf.loading ? (
        <Loading rows={1} />
      ) : wf.error ? (
        <ErrorState message={wf.error} onRetry={wf.retry} />
      ) : !list.length ? (
        <EmptyState>{t("home.workflows.empty")}</EmptyState>
      ) : (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {list.map((w) => (
            <WorkflowCard key={w.workflow_id} w={w} variant="expert" health={health[w.workflow_id]} />
          ))}
        </div>
      )}
    </section>
  );
}

/* =====================================================================
   LEARNER HOME — get unstuck now, then keep going
   ===================================================================== */

function LearnerHome() {
  const wf = useResource(listWorkflows, []);
  return (
    <div className="space-y-14">
      <div className="grid gap-10 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <LearnerHero />
        <ContinueLearning workflows={wf.data} loading={wf.loading} />
      </div>
      <Library wf={wf} />
      <MyRequests />
    </div>
  );
}

function LearnerHero() {
  const { t } = useUi();
  return (
    <section aria-labelledby="learner-hero">
      <h1 id="learner-hero" className="text-4xl font-black leading-[0.95] tracking-[-0.045em] sm:text-6xl">
        {t("home.learner.title")}
      </h1>
      <p className="mt-4 max-w-[50ch] text-base leading-relaxed text-ink-2 sm:text-lg">{t("home.learner.sub")}</p>
      <Link
        href="/learn"
        className="group mt-8 flex items-center gap-4 rounded-[10px] border-[3px] border-ink bg-claros p-5 text-claros-ink shadow-hard-lg transition-[transform,box-shadow] duration-150 hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[8px_8px_0_0_var(--ink)] active:translate-x-1.5 active:translate-y-1.5 active:shadow-none sm:p-7"
      >
        <ClarosDot size={56} speaking />
        <span className="min-w-0 flex-1">
          <span className="block text-2xl font-black tracking-[-0.03em] sm:text-3xl">{t("home.learner.cta")}</span>
          <span className="mt-1.5 flex items-center gap-1.5 text-sm font-semibold opacity-90">
            <Mic className="size-4 shrink-0" aria-hidden />
            {t("home.learner.hint")}
          </span>
        </span>
        <ArrowRight className="size-8 shrink-0 transition-transform duration-150 group-hover:translate-x-1" aria-hidden />
      </Link>
    </section>
  );
}

type Progress = { w: WorkflowSummary; map: WorkMap; levels: Record<string, MasteryNode["level"]> };

function ContinueLearning({ workflows, loading }: { workflows?: WorkflowSummary[]; loading: boolean }) {
  const { t } = useUi();
  const learnable = useMemo(() => (workflows ?? []).filter((w) => w.coverage.status !== "missing"), [workflows]);
  const [items, setItems] = useState<Progress[] | null>(null);
  useEffect(() => {
    if (!workflows) return;
    let alive = true;
    Promise.all(
      learnable.slice(0, 6).map(async (w) => {
        const [m, ms] = await Promise.all([getWorkflow(w.workflow_id), getMastery(LEA.id, w.workflow_id)]);
        return { w, map: m.data, levels: mergeMastery(w.workflow_id, ms.source === "live" ? ms.data : []) };
      }),
    ).then((r) => alive && setItems(r.filter((p) => Object.keys(p.levels).length > 0)));
    return () => {
      alive = false;
    };
  }, [workflows, learnable]);

  return (
    <section aria-labelledby="continue">
      <SectionHeader id="continue" title={t("home.continue.title")} />
      {loading || items === null ? (
        <Loading rows={2} />
      ) : !items.length ? (
        <EmptyState>{t("home.continue.empty")}</EmptyState>
      ) : (
        <ul className="space-y-4">
          {items.map(({ w, map, levels }) => {
            const steps = sortedSteps(map);
            const lv = steps.map((s) => levels[s.id] ?? "unseen");
            const done = lv.filter((l) => l === "unaided").length;
            const rank = { unseen: 0, caught: 1, hinted: 2, unaided: 3 } as const;
            const next = [...steps].sort((a, b) => rank[levels[a.id] ?? "unseen"] - rank[levels[b.id] ?? "unseen"] || a.order - b.order)[0];
            return (
              <li key={w.workflow_id} className="rounded-base border-2 border-ink bg-card p-4 shadow-hard">
                <p className="font-extrabold leading-snug">{w.name}</p>
                <div className="mt-3">
                  <MasteryStrip levels={lv} total={steps.length} />
                  <p className="tnum mt-1.5 text-xs font-semibold text-ink-2">{t("mastery.progress", { done, total: steps.length })}</p>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                  {next ? (
                    <p className="min-w-0 flex-1 text-sm">
                      <span className={cn("mr-1.5 inline-block size-2.5 rounded-[2px] border-2 border-ink align-middle", LEVEL_BG[levels[next.id] ?? "unseen"])} aria-hidden />
                      {t("mastery.next", { step: next.title })}
                    </p>
                  ) : null}
                  <Link href={`/learn?wf=${encodeURIComponent(w.workflow_id)}${next ? `&step=${next.id}` : ""}`} className={buttonVariants({ variant: "primary", size: "sm" })}>
                    {t("mastery.practice")} <ArrowRight aria-hidden />
                  </Link>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Library({ wf }: { wf: ReturnType<typeof useResource<WorkflowSummary[]>> }) {
  const { t } = useUi();
  const [app, setApp] = useState<string>("all");
  const [cov, setCov] = useState<"all" | "ready" | "partial" | "missing">("all");
  const list = wf.data ?? [];
  const apps = [...new Set(list.flatMap((w) => w.apps))].sort();
  const shown = list.filter((w) => (app === "all" || w.apps.includes(app)) && (cov === "all" || w.coverage.status === cov));
  const health = useHealth(list);
  const covs = (["ready", "partial", "missing"] as const).filter((c) => list.some((w) => w.coverage.status === c));

  return (
    <section aria-labelledby="library">
      <SectionHeader id="library" title={t("home.library.title")} aside={<SourceNote source={wf.source} />} />
      {list.length > 1 && (apps.length > 1 || covs.length > 1) ? (
        <div className="mb-5 flex flex-wrap items-center gap-x-6 gap-y-3">
          {apps.length > 1 ? (
            <FilterGroup label={t("home.library.app")} value={app} onChange={setApp} options={[{ v: "all", l: t("common.all") }, ...apps.map((a) => ({ v: a, l: a }))]} />
          ) : null}
          {covs.length > 1 ? (
            <FilterGroup
              label={t("home.library.coverage")}
              value={cov}
              onChange={(v) => setCov(v as typeof cov)}
              options={[{ v: "all", l: t("common.all") }, ...covs.map((c) => ({ v: c, l: t(`cov.${c}` as DictKey) }))]}
            />
          ) : null}
        </div>
      ) : null}
      {wf.loading ? (
        <Loading rows={2} />
      ) : wf.error ? (
        <ErrorState message={wf.error} onRetry={wf.retry} />
      ) : !list.length ? (
        <EmptyState>{t("home.library.empty")}</EmptyState>
      ) : !shown.length ? (
        <EmptyState>{t("home.library.none")}</EmptyState>
      ) : (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((w) => (
            <WorkflowCard key={w.workflow_id} w={w} health={health[w.workflow_id]} />
          ))}
        </div>
      )}
    </section>
  );
}

function FilterGroup({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-xs font-semibold text-ink-2">{label}</span>
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          aria-pressed={value === o.v}
          onClick={() => onChange(o.v)}
          className={cn(
            "h-8 rounded-[4px] border-2 border-ink px-2.5 text-xs font-bold transition-[background-color,transform] duration-150 active:translate-y-px",
            value === o.v ? "bg-ink text-paper" : "bg-card hover:bg-paper-2",
          )}
        >
          {o.l}
        </button>
      ))}
    </div>
  );
}

function MyRequests() {
  const { t } = useUi();
  const reqs = useResource(listRequests, []);
  const mine = (reqs.data ?? []).filter((r) => r.requested_by.id === LEA.id && !isJunkRequest(r)).sort((a, b) => b.created_at - a.created_at);
  return (
    <section aria-labelledby="myreq">
      <SectionHeader id="myreq" title={t("home.myreq.title")} count={mine.length} />
      {reqs.loading ? (
        <Loading rows={1} />
      ) : reqs.error ? (
        <ErrorState message={reqs.error} onRetry={reqs.retry} />
      ) : !mine.length ? (
        <EmptyState>{t("home.myreq.empty")}</EmptyState>
      ) : (
        <ul className="space-y-3">
          {mine.map((r) => (
            <RequestStatus key={r.id} r={r} />
          ))}
        </ul>
      )}
    </section>
  );
}
