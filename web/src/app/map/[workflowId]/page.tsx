"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Braces,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Download,
  FileCode2,
  GalleryVerticalEnd,
  Info,
  Link2,
  Mic,
  Split,
} from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import {
  AppChip,
  AvatarStack,
  CoverageChip,
  EmptyState,
  ErrorState,
  Loading,
  Meter,
  OnetTag,
  ScreenThumb,
  SourceNote,
  useResource,
} from "@/components/claros/primitives";
import { KIND, KindChip, type ChipKind } from "@/components/claros/chips";
import { StepPanel, type Section } from "@/components/claros/StepDetail";
import { QuestionLine } from "@/components/claros/questions";
import {
  flatOrder,
  guardrailsFor,
  hasConflict,
  hasGuardrail,
  isJudgment,
  isUnconfirmed,
  matches,
  stepGroups,
  stepHighlight,
  honestCoverage,
  openQuestions,
  shortTitle,
  type MapFilter,
} from "@/components/claros/mapUtils";
import { useLiveStore } from "@/components/claros/live";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { createSession, exportUrls, getWorkflow } from "@/lib/api";
import { EXPERT } from "@/lib/mock";
import type { Step, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

export default function MapPage() {
  return (
    <Suspense fallback={null}>
      <MapRoute />
    </Suspense>
  );
}

function MapRoute() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const res = useResource(() => getWorkflow(workflowId), [workflowId]);
  const name = res.data?.name;
  return (
    <Shell wide crumbs={[{ key: "crumb.workflows", href: "/map" }, { label: name ?? "…" }]}>
      {res.loading ? <Loading rows={4} /> : res.error ? <ErrorState message={res.error} onRetry={res.retry} /> : !res.data ? <NotFound /> : <WorkMapView key={res.data.workflow_id + res.data.version} map={res.data} source={res.source} />}
    </Shell>
  );
}

function NotFound() {
  const { t } = useUi();
  return <EmptyState>{t("map.notFound")}</EmptyState>;
}

/* ===================================================================== */

function WorkMapView({ map: initial, source }: { map: WorkMap; source?: "live" | "mock" }) {
  const { t } = useUi();
  const params = useSearchParams();
  const [map, setMap] = useState(initial);
  const groups = useMemo(() => stepGroups(map), [map]);
  const order = useMemo(() => flatOrder(groups), [groups]);
  const [filter, setFilter] = useState<MapFilter>("all");
  const [panel, setPanel] = useState<{ id: string; section: Section } | null>(() => {
    const s = params.get("step");
    return s && map.steps.some((x) => x.id === s) ? { id: s, section: params.get("focus") } : null;
  });
  const [panelOpen, setPanelOpen] = useState(Boolean(panel));
  const [current, setCurrent] = useState<string | null>(order[0]?.id ?? null);
  const [pointed, setPointed] = useState<string | null>(null);
  const [mini, setMini] = useState(true);

  const openStep = useCallback((id: string, section: Section = null) => {
    setPanel({ id, section });
    setPanelOpen(true);
    setCurrent(id);
  }, []);

  const jump = useCallback((id: string) => {
    const el = document.getElementById(`step-${id}`);
    if (!el) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    el.querySelector<HTMLButtonElement>("[data-open-step]")?.focus({ preventScroll: true });
    setCurrent(id);
  }, []);

  // Claros points at a step by voice (highlight_step): scroll there, flash it, follow in the panel.
  const highlight = useLiveStore((s) => s.highlightedStepId);
  useEffect(() => {
    if (!highlight || !map.steps.some((s) => s.id === highlight)) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFilter("all");
    setPointed(highlight);
    requestAnimationFrame(() => jump(highlight));
    setPanel((p) => (p ? { id: highlight, section: null } : p));
    const h = setTimeout(() => setPointed(null), 1600);
    return () => clearTimeout(h);
  }, [highlight, map.steps, jump]);

  // which step is in the reading band → mini-map highlight
  useEffect(() => {
    const els = order.map((s) => document.getElementById(`step-${s.id}`)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (vis[0]) setCurrent(vis[0].target.id.replace(/^step-/, ""));
      },
      { rootMargin: "-35% 0px -55% 0px" },
    );
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [order, filter]);

  const visible = (s: Step) => matches(map, s, filter);
  const counts: Record<MapFilter, number> = {
    all: map.steps.length,
    judgment: map.steps.filter(isJudgment).length,
    guardrail: map.steps.filter(hasGuardrail).length,
    conflict: map.steps.filter(hasConflict).length,
    unconfirmed: map.steps.filter((s) => isUnconfirmed(map, s)).length,
  };
  const conflictSteps = order.filter(hasConflict);
  const idx = panel ? order.findIndex((s) => s.id === panel.id) : -1;
  const panelStep = idx >= 0 ? order[idx] : null;
  const setApproved = (id: string, v: boolean) => setMap((m) => ({ ...m, steps: m.steps.map((s) => (s.id === id ? { ...s, approved: v } : s)) }));
  const shownCount = order.filter(visible).length;

  return (
    <div>
      <MapHeader map={map} source={source} />

      {conflictSteps.length ? (
        <div className="mt-6 space-y-2">
          {conflictSteps.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => openStep(s.id, "conflict")}
              className="group flex w-full items-center gap-3 rounded-base border-2 border-ink bg-partial p-3 text-left text-on-fill shadow-hard transition-[transform,box-shadow] duration-150 hover:-translate-x-px hover:-translate-y-px hover:shadow-hard-lg active:translate-x-1 active:translate-y-1 active:shadow-none sm:p-4"
            >
              <span className="grid size-10 shrink-0 place-items-center rounded-[4px] border-2 border-ink bg-card text-ink">
                <Split className="size-5" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block font-black tracking-[-0.01em]">{t("map.conflictBanner", { n: s.order })}</span>
                <span className="block truncate text-sm font-semibold">{s.title}</span>
              </span>
              <span className="hidden items-center gap-1 text-sm font-extrabold sm:inline-flex">
                {t("map.conflictBanner.cta")} <ChevronRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
              </span>
              <ChevronRight className="size-5 sm:hidden" aria-hidden />
            </button>
          ))}
        </div>
      ) : null}

      {/* toolbar: filters + legend + mini-map toggle */}
      <div className="sticky top-16 z-20 -mx-4 mt-8 border-y-2 border-ink bg-paper px-4 py-2.5 sm:-mx-6 sm:px-6">
        <div className="flex items-center gap-3">
          <Tabs value={filter} onValueChange={(v) => setFilter(v as MapFilter)} className="min-w-0 flex-1">
            <div className="-my-1 overflow-x-auto py-1">
              <TabsList aria-label={t("map.filter.label")} className="h-auto w-max justify-start gap-1 border-2 bg-card p-1">
                {(["all", "judgment", "guardrail", "conflict", "unconfirmed"] as MapFilter[]).map((f) => {
                  const kind: ChipKind | null = f === "all" ? null : f;
                  const Icon = kind ? KIND[kind].Icon : GalleryVerticalEnd;
                  return (
                    <TabsTrigger key={f} value={f} disabled={f !== "all" && counts[f] === 0} className="h-8 gap-1.5 px-2.5 text-xs font-bold sm:text-sm">
                      <Icon className="size-4" aria-hidden />
                      {t(`map.filter.${f}` as DictKey)}
                      <span className="tnum rounded-[3px] border border-current/40 px-1 font-mono text-[10px] leading-4">{counts[f]}</span>
                    </TabsTrigger>
                  );
                })}
              </TabsList>
            </div>
          </Tabs>
          <Legend />
          <Button variant="outline" size="sm" className="hidden lg:inline-flex" aria-pressed={mini} onClick={() => setMini((m) => !m)}>
            <GalleryVerticalEnd aria-hidden /> {mini ? t("map.minimap.hide") : t("map.minimap.show")}
          </Button>
        </div>
        {mini ? <MiniMapStrip map={map} order={order} current={current} visible={visible} onJump={jump} /> : null}
      </div>

      <div className={cn("mt-6 grid gap-8", mini && "lg:grid-cols-[148px_minmax(0,1fr)]")}>
        {mini ? <MiniMapRail map={map} order={order} current={current} visible={visible} onJump={jump} /> : null}

        <div className="min-w-0">
          {shownCount === 0 ? (
            <EmptyState>{t("map.filter.empty")}</EmptyState>
          ) : (
            <ol className="space-y-0" aria-label={t("learn.steps")}>
              {groups.map((g, gi) => {
                const steps = g.steps.filter(visible);
                if (!steps.length) return null;
                const any = steps.length > 1;
                return (
                  <li key={g.depth} className="list-none">
                    {gi > 0 ? <Connector /> : null}
                    {any ? (
                      <section aria-label={t("map.anyOrder")} className="rounded-[10px] border-2 border-dashed border-ink p-3 sm:p-4">
                        <p className="mb-3 flex flex-wrap items-center gap-2 text-sm font-bold">
                          <KindChip kind="anyOrder">{t("map.anyOrder")}</KindChip>
                          <span className="font-semibold text-ink-2">{t("map.anyOrder.sub")}</span>
                        </p>
                        <ol className="grid gap-4 xl:grid-cols-2">
                          {steps.map((s) => (
                            <StepCard key={s.id} map={map} step={s} pointed={pointed === s.id} active={panelOpen && panel?.id === s.id} onOpen={openStep} compact />
                          ))}
                        </ol>
                      </section>
                    ) : (
                      <ol>
                        <StepCard map={map} step={steps[0]} pointed={pointed === steps[0].id} active={panelOpen && panel?.id === steps[0].id} onOpen={openStep} />
                      </ol>
                    )}
                  </li>
                );
              })}
            </ol>
          )}

          <OpenQuestions map={map} />
        </div>
      </div>

      <StepPanel
        map={map}
        step={panelStep}
        index={idx}
        total={order.length}
        section={panel?.section ?? null}
        open={panelOpen && Boolean(panelStep)}
        onOpenChange={setPanelOpen}
        onPrev={idx > 0 ? () => openStep(order[idx - 1].id) : undefined}
        onNext={idx >= 0 && idx < order.length - 1 ? () => openStep(order[idx + 1].id) : undefined}
        onApprove={setApproved}
      />
    </div>
  );
}

function Connector() {
  return <div aria-hidden className="ml-[30px] h-5 w-0.5 bg-ink sm:ml-[34px]" />;
}

/* ---------------- header ---------------- */

function MapHeader({ map, source }: { map: WorkMap; source?: "live" | "mock" }) {
  const { t, tn, role, lang } = useUi();
  const router = useRouter();
  const cov = honestCoverage(map);
  const openCount = cov.open - cov.conflicts;
  const urls = exportUrls(map.workflow_id);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const approved = map.steps.filter((s) => s.approved).length;
  const answer = async () => {
    setBusy(true);
    const s = await createSession({ mode: "debrief", user: EXPERT, lang, workflow_id: map.workflow_id });
    router.push(`/debrief/${s.data.session_id}`);
  };

  return (
    <header className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <CoverageChip status={map.coverage.status} long />
          <Badge variant="tag">{t("map.version", { n: map.version })}</Badge>
          {map.apps.map((a) => (
            <AppChip key={a} name={a} />
          ))}
          <SourceNote source={source} />
        </div>
        <h1 className="mt-4 text-3xl font-black leading-[1.02] tracking-[-0.04em] sm:text-5xl">{map.name}</h1>
        {map.onet ? (
          <div className="mt-4">
            <OnetTag onet={map.onet} full />
          </div>
        ) : null}
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm font-semibold">
          <span className="text-ink-2">{t("map.experts")}:</span>
          <AvatarStack users={map.experts} size={28} />
          <span>{map.experts.map((e) => e.name).join(" · ")}</span>
        </div>
        <div className="mt-6 flex flex-wrap gap-2">
          {role === "learner" && map.coverage.status !== "missing" ? (
            <Link href={`/learn?wf=${encodeURIComponent(map.workflow_id)}`} className={buttonVariants({ variant: "claros", size: "lg" })}>
              {t("map.learnThis")} <ArrowRight aria-hidden />
            </Link>
          ) : null}
          {role === "expert" && map.open_unknowns.length ? (
            <Button variant="claros" size="lg" onClick={answer} loading={busy}>
              {!busy ? <Mic aria-hidden /> : null}
              {t("q.answer")}
            </Button>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button variant="secondary" size="lg" className="data-popup-open:translate-x-1 data-popup-open:translate-y-1 data-popup-open:shadow-none" />}>
              <Download aria-hidden /> {t("map.export")} <ChevronDown className="opacity-70" aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-60">
              <DropdownMenuItem render={<a href={urls.skill} target="_blank" rel="noreferrer" />}>
                <FileCode2 aria-hidden /> {t("map.export.skill")}
              </DropdownMenuItem>
              <DropdownMenuItem render={<a href={urls.json} target="_blank" rel="noreferrer" />}>
                <Braces aria-hidden /> {t("map.export.json")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                closeOnClick={false}
                onClick={() =>
                  navigator.clipboard
                    ?.writeText(urls.mcp)
                    .then(() => {
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1800);
                    })
                    .catch(() => {})
                }
              >
                <Link2 aria-hidden /> <span aria-live="polite">{copied ? t("map.copied") : t("map.export.mcp")}</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <section aria-label={t("map.coverage")} className="space-y-3 self-start rounded-base border-2 border-ink bg-card p-4 shadow-hard">
        <div className="flex items-center justify-between">
          <h2 className="font-extrabold">{t("map.coverage")}</h2>
          <span className="tnum inline-flex items-center gap-1 font-mono text-xs font-bold" title={t("map.approve")}>
            <Check className="size-3.5" aria-hidden /> {approved}/{map.steps.length}
          </span>
        </div>
        <Meter value={cov.evidence} label={t("map.evidence")} tone="ready" />
        <Meter value={cov.explained} label={t("map.judgments")} />
        <Meter value={cov.rulesOk} label={t("map.guardrails")} />
        {map.coverage.status !== "ready" && (openCount || cov.conflicts || cov.unapproved) ? (
          <div className="border-t-2 border-dashed border-ink/30 pt-3 text-xs font-bold">
            <p className="mb-1.5 font-semibold text-ink-2">{t("map.cov.why")}</p>
            <ul className="flex flex-wrap gap-1.5">
              {cov.conflicts ? (
                <li>
                  <Badge variant="partial" className="text-on-fill">
                    <Split aria-hidden /> {tn("conflicts", cov.conflicts)}
                  </Badge>
                </li>
              ) : null}
              {openCount ? (
                <li>
                  <Badge variant="neutral">
                    <CircleHelp aria-hidden /> {tn("open", openCount)}
                  </Badge>
                </li>
              ) : null}
              {cov.unapproved ? (
                <li>
                  <Badge variant="dashed">{t("map.cov.unapproved", { n: cov.unapproved })}</Badge>
                </li>
              ) : null}
            </ul>
          </div>
        ) : null}
      </section>
    </header>
  );
}

/* ---------------- legend ---------------- */

function Legend() {
  const { t } = useUi();
  const kinds: ChipKind[] = ["judgment", "guardrail", "conflict", "unconfirmed", "anyOrder"];
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="sm" className="shrink-0 data-popup-open:bg-ink data-popup-open:text-paper" />}>
        <Info aria-hidden /> <span className="hidden sm:inline">{t("legend.title")}</span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 bg-card p-4 text-ink shadow-hard">
        <p className="mb-3 font-extrabold">{t("legend.title")}</p>
        <ul className="space-y-2.5">
          {kinds.map((k) => (
            <li key={k} className="grid grid-cols-[auto_1fr] items-start gap-2.5 text-sm">
              <KindChip kind={k} size="sm" className="mt-0.5" />
              <span>
                <span className="font-bold">{t(KIND[k].label)}</span> — {t(KIND[k].legend!)}
              </span>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

/* ---------------- step card ---------------- */

function StepCard({
  map,
  step: s,
  pointed,
  active,
  onOpen,
  compact,
}: {
  map: WorkMap;
  step: Step;
  pointed: boolean;
  active: boolean;
  onOpen: (id: string, section?: Section) => void;
  compact?: boolean;
}) {
  const { t, tn } = useUi();
  const guards = guardrailsFor(map, s);
  const unconfirmed = isUnconfirmed(map, s);
  const where = [s.state_signature.app, s.state_signature.view].filter(Boolean).join(" · ");
  return (
    <li id={`step-${s.id}`} className="scroll-mt-48 list-none">
      <article
        className={cn(
          "group relative rounded-[10px] border-2 border-ink transition-[transform,box-shadow] duration-150 ease-out",
          "hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[7px_7px_0_0_var(--ink)] focus-within:-translate-x-0.5 focus-within:-translate-y-0.5 focus-within:shadow-[7px_7px_0_0_var(--ink)]",
          !s.approved ? "hatch-partial border-dashed shadow-hard" : "bg-card shadow-hard",
          active && "shadow-claros! border-claros",
          pointed && "claros-point border-claros",
        )}
      >
        <button
          type="button"
          data-open-step
          onClick={() => onOpen(s.id)}
          aria-label={`${t("map.step", { n: s.order })}: ${s.title}. ${t("map.open")}`}
          className="absolute inset-0 z-[1] cursor-pointer rounded-[10px] focus-visible:outline-3 focus-visible:outline-offset-4 focus-visible:outline-claros"
        />
        <div className={cn("pointer-events-none relative grid items-start gap-4 p-4 sm:p-5", !compact && "md:grid-cols-[minmax(0,1fr)_168px]")}>
          <div className="flex min-w-0 gap-3.5">
            <span className="tnum grid size-11 shrink-0 place-items-center rounded-[6px] border-2 border-ink bg-ink font-mono text-lg font-black text-paper sm:size-12">{s.order}</span>
            <div className="min-w-0 flex-1">
              <h3 className="text-base font-extrabold leading-snug tracking-[-0.015em] sm:text-lg">{s.title}</h3>
              {where ? <p className="mt-0.5 truncate text-xs font-semibold text-ink-2">{where}</p> : null}
              {s.decision?.description && !s.conflict ? <p className="mt-2 line-clamp-2 text-sm text-ink-2">{s.decision.description}</p> : null}

              {isJudgment(s) || guards.length || s.conflict || unconfirmed || s.context_note_ids.length ? (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {isJudgment(s) ? (
                    <KindChip kind="judgment" onClick={() => onOpen(s.id, "decision")} title={s.decision?.description}>
                      {t("chip.judgment")}
                    </KindChip>
                  ) : null}
                  {guards.map((g) => (
                    <KindChip key={g.id} kind="guardrail" onClick={() => onOpen(s.id, `guard-${g.id}`)} title={`${t(("guard." + g.action) as DictKey)}: ${g.text}`}>
                      {shortTitle(g.text)}
                    </KindChip>
                  ))}
                  {s.conflict ? (
                    <KindChip kind="conflict" onClick={() => onOpen(s.id, "conflict")}>
                      {t("chip.conflict")}
                    </KindChip>
                  ) : null}
                  {unconfirmed ? <KindChip kind="unconfirmed">{t("chip.unconfirmed")}</KindChip> : null}
                  {s.context_note_ids.length ? (
                    <KindChip kind="notes" onClick={() => onOpen(s.id, "context")}>
                      {t("chip.notes", { n: s.context_note_ids.length })}
                    </KindChip>
                  ) : null}
                </div>
              ) : null}

              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-xs font-semibold text-ink-2">
                  {s.experts.length ? <AvatarStack users={s.experts.map((id) => map.experts.find((e) => e.id === id) ?? { id, name: id })} size={22} /> : null}
                  {s.moment?.keyframe_ids.length ? <span className="tnum">{tn("frames", s.moment.keyframe_ids.length)}</span> : null}
                </span>
                <span className="inline-flex items-center gap-1 text-sm font-extrabold transition-transform duration-150 group-hover:translate-x-0.5">
                  {t("map.open")} <ChevronRight className="size-4" aria-hidden />
                </span>
              </div>
            </div>
          </div>
          {!compact ? (
            <div className="hidden md:block">
              <ScreenThumb keyframeId={s.moment?.keyframe_ids?.[0]} title={s.state_signature.view ?? ""} seed={s.order} highlight={stepHighlight(s)} />
            </div>
          ) : null}
        </div>
      </article>
    </li>
  );
}

/* ---------------- mini-map ---------------- */

type MiniProps = { map: WorkMap; order: Step[]; current: string | null; visible: (s: Step) => boolean; onJump: (id: string) => void };

function MiniMapRail({ order, current, visible, onJump }: MiniProps) {
  const { t } = useUi();
  return (
    <nav aria-label={t("map.minimap")} className="sticky top-36 hidden max-h-[calc(100dvh-10rem)] self-start overflow-y-auto pb-2 pr-1 lg:block">
      <p className="mb-2 text-xs font-bold text-ink-2">{t("map.minimap")}</p>
      <ol className="space-y-2">
        {order.map((s) => {
          const on = current === s.id;
          const dim = !visible(s);
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onJump(s.id)}
                aria-current={on ? "step" : undefined}
                aria-label={`${s.order}. ${s.title}`}
                disabled={dim}
                className={cn(
                  "relative block w-full rounded-[6px] text-left transition-[transform,opacity] duration-150",
                  on ? "outline-3 outline-offset-2 outline-claros" : "hover:-translate-y-0.5",
                  dim && "opacity-30",
                )}
              >
                <ScreenThumb keyframeId={s.moment?.keyframe_ids?.[0]} seed={s.order} rounded className="rounded-[6px]" />
                <span className={cn("tnum absolute left-1 top-1 grid size-6 place-items-center rounded-[3px] border-2 border-ink font-mono text-[11px] font-black", on ? "bg-claros text-claros-ink" : "bg-card text-ink")}>
                  {s.order}
                </span>
                <span className="absolute bottom-1 right-1 flex gap-0.5">
                  {hasConflict(s) ? <Dot kind="conflict" /> : null}
                  {hasGuardrail(s) ? <Dot kind="guardrail" /> : null}
                  {isJudgment(s) ? <Dot kind="judgment" /> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function Dot({ kind }: { kind: ChipKind }) {
  const k = KIND[kind];
  return (
    <span className={cn("grid size-5 place-items-center rounded-[3px] border-2 border-ink [&_svg]:size-3", k.cls)}>
      <k.Icon aria-hidden />
    </span>
  );
}

/** phone/tablet: compact horizontal strip inside the sticky toolbar */
function MiniMapStrip({ order, current, visible, onJump }: MiniProps) {
  const { t } = useUi();
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => {
    // scroll the strip only (scrollIntoView would also move the page)
    const box = ref.current;
    const el = box?.querySelector<HTMLElement>(`[data-id="${current}"]`);
    if (!box || !el) return;
    const left = el.offsetLeft - box.offsetLeft;
    if (left < box.scrollLeft || left + el.offsetWidth > box.scrollLeft + box.clientWidth) box.scrollTo({ left: Math.max(0, left - 8) });
  }, [current]);
  return (
    <nav aria-label={t("map.minimap")} className="mt-2 lg:hidden">
      <ol ref={ref} className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1.5 pt-1">
        {order.map((s) => {
          const on = current === s.id;
          return (
            <li key={s.id} data-id={s.id} className="shrink-0">
              <button
                type="button"
                onClick={() => onJump(s.id)}
                aria-current={on ? "step" : undefined}
                aria-label={`${s.order}. ${s.title}`}
                disabled={!visible(s)}
                className={cn("relative block w-[72px] rounded-[4px] disabled:opacity-30", on && "outline-3 outline-offset-1 outline-claros")}
              >
                <ScreenThumb keyframeId={s.moment?.keyframe_ids?.[0]} seed={s.order} />
                <span className={cn("tnum absolute left-0.5 top-0.5 grid size-5 place-items-center rounded-[3px] border-2 border-ink font-mono text-[10px] font-black", on ? "bg-claros text-claros-ink" : "bg-card text-ink")}>
                  {s.order}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/* ---------------- open questions ---------------- */

function OpenQuestions({ map }: { map: WorkMap }) {
  const { t, role } = useUi();
  const qs = openQuestions(map);
  if (!qs.length) return null;
  return (
    <section aria-labelledby="openq" className="mt-12 rounded-base border-2 border-dashed border-ink p-4 sm:p-5">
      <h2 id="openq" className="flex items-center gap-2 text-lg font-extrabold">
        <CircleHelp className="size-5" aria-hidden /> {t("map.openq.title")}
        <span className="tnum inline-grid h-6 min-w-6 place-items-center rounded-full border-2 border-ink bg-ink px-1.5 text-xs font-black text-paper">{qs.length}</span>
      </h2>
      <p className="mt-1 text-sm text-ink-2">{t("map.openq.sub")}</p>
      <ul className="mt-4 space-y-2">
        {qs.map((q) => {
          const type = q.kind === "conflict" ? "conflict" : q.unknown.type;
          return (
            <li key={q.id} className="flex flex-wrap items-start gap-2 rounded-[4px] border-2 border-ink bg-card p-3 text-sm">
              <Badge variant={type === "conflict" ? "partial" : "neutral"}>{t(`q.kind.${type}` as DictKey)}</Badge>
              <QuestionLine map={map} q={q} viewer={role === "expert" ? EXPERT : null} className="min-w-0 flex-1 font-semibold" />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
