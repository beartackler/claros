"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Download, FileCode2, GalleryVerticalEnd, Mic, Split } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import { AvatarStack, EmptyState, ErrorState, Loading, ScreenThumb, SourceNote, useMediaQuery, useResource } from "@/components/claros/primitives";
import { KIND, KindChip, type ChipKind } from "@/components/claros/chips";
import { StepDetail, StepPanel, type Section } from "@/components/claros/StepDetail";
import { conflictNames } from "@/components/claros/ConflictBanner";
import { MapStatus, mapReady } from "@/components/claros/cards";
import { expertById, firstName, flatOrder, guardrailsFor, hasGuardrail, isJudgment, isUnconfirmed, matches, shortTitle, stepGroups, type MapFilter } from "@/components/claros/mapUtils";
import { useLiveStore } from "@/components/claros/live";
import { Button, buttonVariants } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
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
  return (
    <Shell app>
      {res.loading ? (
        <Loading rows={4} />
      ) : res.error ? (
        <ErrorState message={res.error} onRetry={res.retry} />
      ) : !res.data ? (
        <NotFound />
      ) : (
        <WorkMapView key={res.data.workflow_id + res.data.version} map={res.data} source={res.source} />
      )}
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
  const wide = useMediaQuery("(min-width: 1024px)");
  // app layout: the page never scrolls; the title collapses as soon as either pane scrolls
  const [collapsed, setCollapsed] = useState(false);
  const onPaneScroll = (e: React.UIEvent<HTMLElement>) => {
    if (!wide) return;
    const top = e.currentTarget.scrollTop;
    if (top > 24 && !collapsed) setCollapsed(true);
    else if (top === 0 && collapsed) {
      const other = e.currentTarget.id === "step-pane" ? document.getElementById("step-list") : document.getElementById("step-pane");
      if (!other || other.scrollTop === 0) setCollapsed(false);
    }
  };
  const map = initial;
  const groups = useMemo(() => stepGroups(map), [map]);
  const order = useMemo(() => flatOrder(groups), [groups]);
  const [filter, setFilter] = useState<MapFilter>("all");
  const [sel, setSel] = useState<{ id: string; section: Section }>(() => {
    const s = params.get("step");
    return s && map.steps.some((x) => x.id === s) ? { id: s, section: params.get("focus") } : { id: order[0]?.id ?? "", section: null };
  });
  const [sheetOpen, setSheetOpen] = useState(Boolean(params.get("step")));
  const [pointed, setPointed] = useState<string | null>(null);

  const openStep = useCallback(
    (id: string, section: Section = null) => {
      setSel({ id, section });
      if (!wide) setSheetOpen(true);
      else document.getElementById("step-pane")?.scrollTo({ top: 0, behavior: "smooth" });
    },
    [wide],
  );

  // Claros points at a step by voice (highlight_step): select it and flash its card.
  const highlight = useLiveStore((s) => s.highlightedStepId);
  useEffect(() => {
    if (!highlight || !map.steps.some((s) => s.id === highlight)) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFilter("all");
    setPointed(highlight);
    setSel({ id: highlight, section: null });
    requestAnimationFrame(() => document.getElementById(`step-${highlight}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    const h = setTimeout(() => setPointed(null), 1600);
    return () => clearTimeout(h);
  }, [highlight, map.steps]);

  const visible = (s: Step) => matches(map, s, filter);
  const counts = { all: map.steps.length, judgment: map.steps.filter(isJudgment).length, guardrail: map.steps.filter(hasGuardrail).length };
  const idx = order.findIndex((s) => s.id === sel.id);
  const step = idx >= 0 ? order[idx] : null;
  const prev = idx > 0 ? () => openStep(order[idx - 1].id) : undefined;
  const next = idx >= 0 && idx < order.length - 1 ? () => openStep(order[idx + 1].id) : undefined;

  // ← → walk the steps on wide screens (unless typing or a lightbox is open)
  useEffect(() => {
    if (!wide) return;
    const onKey = (e: KeyboardEvent) => {
      if (document.getElementById("claros-lightbox")) return;
      const el = e.target as HTMLElement;
      if (el.closest("input,textarea,[role=menu],[role=dialog]")) return;
      if (e.key === "ArrowDown" && next) {
        e.preventDefault();
        next();
      }
      if (e.key === "ArrowUp" && prev) {
        e.preventDefault();
        prev();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [wide, next, prev]);

  const list = (
    <>
      {counts.judgment || counts.guardrail ? (
        <div role="group" aria-label={t("map.filter.label")} className="mb-5 flex flex-wrap gap-2">
          {(["all", "judgment", "guardrail"] as const)
            .filter((f) => f === "all" || counts[f] > 0)
            .map((f) => {
              const Icon = f === "all" ? GalleryVerticalEnd : KIND[f as ChipKind].Icon;
              return (
                <Button key={f} variant="outline" aria-pressed={filter === f} onClick={() => setFilter(f)} className={cn("h-11", filter === f && "bg-ink text-paper")}>
                  <Icon aria-hidden />
                  {t(`map.filter.${f}` as DictKey)}
                  <span className="tnum font-mono text-sm opacity-70">{counts[f]}</span>
                </Button>
              );
            })}
        </div>
      ) : null}
      <ol className="space-y-0" aria-label={t("learn.steps")}>
        {groups.map((g, gi) => {
          const steps = g.steps.filter(visible);
          if (!steps.length) return null;
          const any = steps.length > 1;
          return (
            <li key={g.depth} className="list-none">
              {gi > 0 ? <Connector /> : null}
              {any ? (
                <section aria-label={t("map.anyOrder")} className="rounded-base border-2 border-dashed border-ink p-3">
                  <p className="mb-3">
                    <KindChip kind="anyOrder">{t("map.anyOrder")}</KindChip>
                  </p>
                  <ol className="space-y-3">
                    {steps.map((s) => (
                      <StepCard key={s.id} map={map} step={s} pointed={pointed === s.id} active={(wide || sheetOpen) && sel.id === s.id} onOpen={openStep} showThumb={!wide} />
                    ))}
                  </ol>
                </section>
              ) : (
                <ol>
                  <StepCard map={map} step={steps[0]} pointed={pointed === steps[0].id} active={(wide || sheetOpen) && sel.id === steps[0].id} onOpen={openStep} showThumb={!wide} />
                </ol>
              )}
            </li>
          );
        })}
      </ol>
    </>
  );

  return (
    <div className="lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
      <MapHeader map={map} source={source} compact={wide && collapsed} />

      <div className="mt-8 lg:mt-6 lg:grid lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-8 2xl:gap-12">
        {/* left: the timeline — scrolls on its own */}
        <div id="step-list" onScroll={onPaneScroll} className="min-w-0 lg:-mx-2 lg:overflow-y-auto lg:overscroll-contain lg:px-2 lg:pt-1 lg:pb-10">
          {list}
        </div>

        {/* right: the big detail pane — scrolls on its own */}
        {wide && step ? (
          <aside aria-label={step.title} className="flex min-h-0 flex-col pb-6 pr-2">
            <div className="flex min-h-0 flex-1 flex-col rounded-base border-2 border-ink bg-card shadow-hard-lg">
              <header className="flex shrink-0 items-start gap-4 border-b-2 border-ink p-5">
                <span className="tnum grid size-14 shrink-0 place-items-center rounded-base border-2 border-ink bg-ink font-mono text-2xl font-black text-paper">{step.order}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-ink-2">{[step.state_signature.app, step.state_signature.view].filter(Boolean).join(" · ")}</p>
                  <h2 className="mt-0.5 text-3xl font-black leading-tight tracking-[-0.03em]">{step.title}</h2>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button variant="outline" size="icon-sm" onClick={prev} disabled={!prev} aria-label={t("common.prev")}>
                    <ChevronLeft />
                  </Button>
                  <Button variant="outline" size="icon-sm" onClick={next} disabled={!next} aria-label={t("common.next")}>
                    <ChevronRight />
                  </Button>
                </div>
              </header>
              <div id="step-pane" onScroll={onPaneScroll} className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-5">
                <StepDetail key={step.id} map={map} step={step} flash={sel.section} />
              </div>
            </div>
          </aside>
        ) : null}
      </div>

      {!wide ? (
        <StepPanel map={map} step={step} index={idx} total={order.length} section={sel.section} open={sheetOpen && Boolean(step)} onOpenChange={setSheetOpen} onPrev={prev} onNext={next} />
      ) : null}
    </div>
  );
}

function Connector() {
  return <div aria-hidden className="ml-[27px] h-4 w-0.5 bg-ink" />;
}

/* ---------------- header ---------------- */

function MapHeader({ map, source, compact }: { map: WorkMap; source?: "live" | "mock"; compact?: boolean }) {
  const { t, role, lang } = useUi();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const ready = mapReady({ coverage: map.coverage }, map);
  const second = async () => {
    setBusy(true);
    const s = await createSession({ mode: "debrief", user: EXPERT, lang, workflow_id: map.workflow_id });
    router.push(`/debrief/${s.data.session_id}`);
  };
  const confirmedBy = map.approved_by.map((id) => firstName(expertById(map, id).name));
  const when = (map as { updated_at?: number }).updated_at;
  const actions = (
    <div className="flex shrink-0 flex-wrap gap-3">
      {role === "expert" && !ready ? (
        <Button variant="claros" size={compact ? "default" : "lg"} onClick={second} loading={busy}>
          {!busy ? <Mic aria-hidden /> : null}
          {t("map.second")}
        </Button>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size={compact ? "default" : "lg"} />}>
          <Download aria-hidden /> {t("map.export")} <ChevronDown className="opacity-70" aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuItem render={<a href={exportUrls(map.workflow_id).skill} target="_blank" rel="noreferrer" />}>
            <FileCode2 aria-hidden /> {t("map.export.agents")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
  const confirmed = confirmedBy.length ? (
    <span className="inline-flex items-center gap-2 text-base font-semibold text-ink-2">
      <CheckCircle2 className="size-5 text-ink" aria-hidden />
      {when
        ? t("map.confirmedBy", { names: confirmedBy.join(lang === "ru" ? " и " : " & "), when: new Date(when < 1e12 ? when * 1000 : when).toLocaleDateString(lang, { month: "short", day: "numeric" }) })
        : t("map.confirmedBy.nodate", { names: confirmedBy.join(lang === "ru" ? " и " : " & ") })}
    </span>
  ) : null;

  if (compact)
    return (
      <header className="claros-enter flex shrink-0 items-center gap-4 border-b-2 border-ink pb-4">
        <Link href={role === "expert" ? "/map" : "/"} aria-label={t("map.back")} className={buttonVariants({ variant: "ghost", size: "icon-sm" })}>
          <ArrowLeft aria-hidden />
        </Link>
        <MapStatus ready={ready} className="shrink-0" />
        <h1 className="min-w-0 flex-1 truncate text-2xl font-black tracking-[-0.03em]">{map.name}</h1>
        <AvatarStack users={map.experts} size={30} />
        {actions}
      </header>
    );

  return (
    <header className="shrink-0">
      <Link href={role === "expert" ? "/map" : "/"} className="inline-flex items-center gap-1.5 text-base font-bold text-ink-2 underline decoration-transparent decoration-2 underline-offset-4 hover:text-ink hover:decoration-ink">
        <ArrowLeft className="size-5" aria-hidden /> {role === "expert" ? t("map.back") : t("nav.home")}
      </Link>
      <div className="mt-4 flex flex-wrap items-end justify-between gap-x-10 gap-y-6">
        <div className="min-w-0 max-w-[60rem]">
          <div className="flex flex-wrap items-center gap-3">
            <MapStatus ready={ready} />
            {confirmed}
            <SourceNote source={source} />
          </div>
          <h1 className="mt-4 text-4xl font-black leading-[1.02] tracking-[-0.04em] sm:text-5xl 2xl:text-6xl">{map.name}</h1>
          <p className="mt-4 flex flex-wrap items-center gap-3 text-lg font-semibold">
            <AvatarStack users={map.experts} size={34} />
            <span>{map.experts.map((e) => e.name).join(" · ")}</span>
          </p>
        </div>
        {actions}
      </div>
    </header>
  );
}

/* ---------------- step card ---------------- */

function StepCard({
  map,
  step: s,
  pointed,
  active,
  onOpen,
  showThumb,
}: {
  map: WorkMap;
  step: Step;
  pointed: boolean;
  active: boolean;
  onOpen: (id: string, section?: Section) => void;
  showThumb: boolean;
}) {
  const { t } = useUi();
  const guards = guardrailsFor(map, s);
  const unconfirmed = isUnconfirmed(map, s);
  const names = s.conflict ? conflictNames(map, s) : [];
  return (
    <li id={`step-${s.id}`} className="scroll-mt-28 list-none">
      <article
        className={cn(
          "relative rounded-base border-2 border-ink",
          active ? "translate-x-1 translate-y-1 bg-claros-soft" : "press-within bg-card shadow-hard",
          !s.approved && !active && "hatch-partial border-dashed",
          pointed && "claros-point",
        )}
      >
        <button
          type="button"
          data-open-step
          onClick={() => onOpen(s.id)}
          aria-current={active ? "step" : undefined}
          aria-label={`${t("map.step", { n: s.order })}: ${s.title}`}
          className="absolute inset-0 z-[1] cursor-pointer rounded-base focus-visible:outline-3 focus-visible:outline-offset-4 focus-visible:outline-claros"
        />
        <div className={cn("pointer-events-none relative grid items-start gap-4 p-4", showThumb && "sm:grid-cols-[minmax(0,1fr)_200px]")}>
          <div className="flex min-w-0 gap-4">
            <span
              className={cn(
                "tnum grid size-12 shrink-0 place-items-center rounded-base border-2 border-ink font-mono text-xl font-black",
                active ? "bg-claros text-claros-ink" : "bg-ink text-paper",
              )}
            >
              {s.order}
            </span>
            <div className="min-w-0 flex-1">
              <h3 className="text-xl font-extrabold leading-snug tracking-[-0.015em]">{s.title}</h3>
              {isJudgment(s) || guards.length || unconfirmed ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {isJudgment(s) && !s.conflict ? (
                    <KindChip kind="judgment" onClick={() => onOpen(s.id, "decision")} title={s.decision?.description}>
                      {t("chip.judgment")}
                    </KindChip>
                  ) : null}
                  {guards.map((g) => (
                    <KindChip key={g.id} kind="guardrail" onClick={() => onOpen(s.id, `guard-${g.id}`)} title={g.text}>
                      {shortTitle(g.text, 32)}
                    </KindChip>
                  ))}
                  {unconfirmed && !s.conflict ? <KindChip kind="unconfirmed">{t("chip.unconfirmed")}</KindChip> : null}
                </div>
              ) : null}
              {s.conflict ? (
                <p className="mt-3 flex items-start gap-2 rounded-[4px] border-2 border-ink bg-partial/40 px-2.5 py-1.5 text-base font-semibold leading-snug">
                  <Split className="mt-0.5 size-4 shrink-0" aria-hidden />
                  {t("map.conflict.note", { a: names[0] ?? "", b: names[1] ?? "" })}
                </p>
              ) : null}
            </div>
          </div>
          {showThumb ? (
            <div className="hidden sm:block">
              <ScreenThumb keyframeId={s.moment?.keyframe_ids?.[0]} title={s.state_signature.view ?? ""} seed={s.order} />
            </div>
          ) : null}
        </div>
      </article>
    </li>
  );
}
