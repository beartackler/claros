"use client";

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { BookOpen, Braces, Check, CircleHelp, FileCode2, GitFork, Link2, Scale, ShieldAlert, Users } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi, type DictKey } from "@/components/claros/i18n";
import {
  AvatarStack,
  CoverageChip,
  EmptyState,
  ErrorState,
  ExpertAvatar,
  Loading,
  Meter,
  Panel,
  QuoteBlock,
  ScreenThumb,
  SourceNote,
  useResource,
} from "@/components/claros/primitives";
import { ConflictBanner } from "@/components/claros/ConflictBanner";
import { expertById, firstName, guardrailsFor, quotesFor, sortedSteps, stepHighlight } from "@/components/claros/mapUtils";
import { useLiveStore } from "@/components/claros/live";
import { Switch } from "@/components/ui/switch";
import { exportUrls, getWorkflow } from "@/lib/api";
import type { ContextNote, Step, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";

export default function MapPage() {
  return (
    <Shell wide>
      <MapView />
    </Shell>
  );
}

function MapView() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const { t } = useUi();
  const res = useResource(() => getWorkflow(workflowId), [workflowId]);
  if (res.loading) return <Loading rows={4} />;
  if (res.error) return <ErrorState message={res.error} onRetry={res.retry} />;
  if (!res.data) return <EmptyState>{t("map.notFound")}</EmptyState>;
  return <WorkMapView map={res.data} source={res.source} />;
}

/** Longest-path depth from `after` edges → columns; same depth = can happen in any order. */
function columns(map: WorkMap): Step[][] {
  const steps = sortedSteps(map);
  const byId = new Map(steps.map((s) => [s.id, s]));
  const depth = new Map<string, number>();
  const d = (s: Step, guard = 0): number => {
    if (depth.has(s.id)) return depth.get(s.id)!;
    const v = guard > 50 ? 0 : Math.max(-1, ...s.after.map((a) => (byId.get(a) ? d(byId.get(a)!, guard + 1) : -1))) + 1;
    depth.set(s.id, v);
    return v;
  };
  steps.forEach((s) => d(s));
  const cols: Step[][] = [];
  steps.forEach((s) => (cols[depth.get(s.id)!] ??= []).push(s));
  return cols.filter(Boolean);
}

function WorkMapView({ map: initial, source }: { map: WorkMap; source?: "live" | "mock" }) {
  const { t } = useUi();
  const [map, setMap] = useState(initial);
  const cols = useMemo(() => columns(map), [map]);
  const highlight = useLiveStore((s) => s.highlightedStepId);
  const [sel, setSel] = useState<string>(map.steps.find((s) => s.conflict)?.id ?? sortedSteps(map)[0]?.id);
  const selected = map.steps.find((s) => s.id === (highlight ?? sel));
  const urls = exportUrls(map.workflow_id);
  const [copied, setCopied] = useState(false);

  const setApproved = (id: string, v: boolean) => setMap((m) => ({ ...m, steps: m.steps.map((s) => (s.id === id ? { ...s, approved: v } : s)) }));
  const approvedCount = map.steps.filter((s) => s.approved).length;

  return (
    <div className="space-y-6">
      {/* header */}
      <header className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <CoverageChip status={map.coverage.status} long />
            <span className="rounded-[3px] border-2 border-[var(--ink)] bg-white px-1.5 font-mono text-xs font-bold">{t("map.version", { n: map.version })}</span>
            {map.apps.map((a) => (
              <span key={a} className="rounded-[3px] border-2 border-[var(--ink)] bg-[var(--paper-2)] px-1.5 font-mono text-xs font-bold">{a}</span>
            ))}
            <SourceNote source={source} />
          </div>
          <h1 className="mt-3 text-3xl font-black leading-[1] tracking-[-0.04em] text-balance sm:text-5xl">{map.name}</h1>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
            <span className="inline-flex items-center gap-2 font-semibold">
              <AvatarStack users={map.experts} size={28} />
              {map.experts.map((e) => e.name).join(" · ")}
            </span>
            {map.onet ? (
              <span className="inline-flex items-center gap-1.5 rounded-[3px] border-2 border-[var(--ink)] bg-white px-2 py-0.5 text-xs" title={map.onet.task ?? undefined}>
                <BookOpen className="size-3.5" aria-hidden />
                <span className="font-mono font-bold">O*NET {map.onet.occupation_code}</span> {map.onet.occupation_title}
              </span>
            ) : null}
          </div>
        </div>
        <Panel className="space-y-3 p-4">
          <div className="flex items-center justify-between">
            <p className="font-extrabold">{t("map.coverage")}</p>
            <p className="flex gap-3 text-xs font-bold">
              <span className="inline-flex items-center gap-1"><CircleHelp className="size-3.5" aria-hidden />{map.coverage.open_unknowns}</span>
              <span className="inline-flex items-center gap-1"><Users className="size-3.5" aria-hidden />{map.coverage.conflicts}</span>
              <span className="tnum inline-flex items-center gap-1"><Check className="size-3.5" aria-hidden />{approvedCount}/{map.steps.length}</span>
            </p>
          </div>
          <Meter value={map.coverage.steps_with_evidence} label={t("map.evidence")} tone="ready" />
          <Meter value={map.coverage.judgments_complete} label={t("map.judgments")} />
          <Meter value={map.coverage.guardrails_complete} label={t("map.guardrails")} />
          <div className="flex flex-wrap gap-2 border-t-2 border-dashed border-[var(--ink)]/40 pt-3">
            <span className="sr-only">{t("map.export")}</span>
            <a href={urls.skill} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] bg-white px-2.5 text-xs font-bold shadow-[2px_2px_0_0_var(--ink)] hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-none">
              <FileCode2 className="size-4" aria-hidden /> {t("map.export.skill")}
            </a>
            <button
              type="button"
              onClick={() => {
                navigator.clipboard?.writeText(urls.mcp).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1600);
                }).catch(() => {});
              }}
              className="inline-flex h-9 items-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] bg-white px-2.5 text-xs font-bold shadow-[2px_2px_0_0_var(--ink)] hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-none"
            >
              <Link2 className="size-4" aria-hidden /> <span aria-live="polite">{copied ? t("map.copied") : t("map.export.mcp")}</span>
            </button>
            <a href={urls.json} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] bg-white px-2.5 text-xs font-bold shadow-[2px_2px_0_0_var(--ink)] hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-none">
              <Braces className="size-4" aria-hidden /> {t("map.export.json")}
            </a>
          </div>
        </Panel>
      </header>

      {/* filmstrip + lanes */}
      <section aria-label={t("map.filmstrip")} className="scroll-thin overflow-x-auto rounded-[6px] border-2 border-[var(--ink)] bg-white shadow-[var(--hard)]">
        <div className="grid w-max min-w-full" style={{ gridTemplateColumns: `112px repeat(${cols.length}, 216px)` }}>
          {/* filmstrip row */}
          <LaneLabel icon={<GitFork className="size-4" aria-hidden />}>{t("map.filmstrip")}</LaneLabel>
          {cols.map((col, ci) => (
            <div key={ci} className="flex flex-col gap-2 border-b-2 border-l-2 border-[var(--ink)] bg-[var(--ink)] p-2">
              {col.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setSel(s.id)}
                  aria-pressed={selected?.id === s.id}
                  aria-label={`${s.order}. ${s.title}`}
                  className={cn("relative block rounded-[4px] text-left outline-offset-2 transition-transform", selected?.id === s.id ? "outline-3 outline-[var(--claros)]" : "hover:-translate-y-0.5")}
                >
                  <ScreenThumb keyframeId={s.moment?.keyframe_ids?.[0]} title={s.state_signature.view ?? s.title} seed={s.order} highlight={stepHighlight(s)} className="border-[var(--paper)]" />
                  <span className="tnum absolute left-1.5 top-1.5 grid size-6 place-items-center rounded-[3px] border-2 border-[var(--ink)] bg-white font-mono text-[11px] font-black">{s.order}</span>
                  {col.length > 1 ? <span className="absolute right-1.5 top-1.5 rounded-[3px] border-2 border-[var(--ink)] bg-[var(--expert)] px-1 font-mono text-[10px] font-bold">∥</span> : null}
                </button>
              ))}
            </div>
          ))}

          {/* steps lane */}
          <LaneLabel icon={<Check className="size-4" aria-hidden />}>{t("map.lane.steps")}</LaneLabel>
          {cols.map((col, ci) => (
            <LaneCell key={ci}>
              {col.map((s) => (
                <button key={s.id} type="button" onClick={() => setSel(s.id)} className={cn("w-full rounded-[4px] border-2 border-[var(--ink)] p-2 text-left text-xs font-bold leading-snug", selected?.id === s.id ? "bg-[var(--claros-soft)]" : s.approved ? "bg-white" : "hatch-partial")}>
                  {s.title}
                  {!s.approved ? <span className="mt-1 block text-[10px] font-black uppercase">{t("map.unconfirmed")}</span> : null}
                </button>
              ))}
            </LaneCell>
          ))}

          {/* judgment lane */}
          <LaneLabel icon={<Scale className="size-4" aria-hidden />}>{t("map.lane.judgment")}</LaneLabel>
          {cols.map((col, ci) => (
            <LaneCell key={ci}>
              {col.map((s) =>
                s.decision?.kind === "judgment" ? (
                  <button key={s.id} type="button" onClick={() => setSel(s.id)} className={cn("w-full rounded-[4px] border-2 border-[var(--ink)] p-2 text-left text-xs leading-snug", s.conflict ? "bg-[var(--partial)]" : "bg-[var(--expert-soft)]")}>
                    {s.conflict ? <span className="mb-1 flex items-center gap-1 text-[10px] font-black uppercase"><Users className="size-3" aria-hidden />{t("map.conflict")}</span> : null}
                    <span className="font-semibold">{s.decision.description}</span>
                    <span className="mt-1.5 flex -space-x-1.5">
                      {s.experts.map((id, i) => (
                        <ExpertAvatar key={id} user={expertById(map, id)} size={18} index={map.experts.findIndex((e) => e.id === id) === -1 ? i : map.experts.findIndex((e) => e.id === id)} />
                      ))}
                    </span>
                  </button>
                ) : null,
              )}
            </LaneCell>
          ))}

          {/* guardrails lane */}
          <LaneLabel icon={<ShieldAlert className="size-4" aria-hidden />} last>{t("map.lane.guardrails")}</LaneLabel>
          {cols.map((col, ci) => (
            <LaneCell key={ci} last>
              {col.flatMap((s) =>
                guardrailsFor(map, s).map((g) => (
                  <button key={g.id} type="button" onClick={() => setSel(s.id)} className="w-full rounded-[4px] border-2 border-[var(--ink)] bg-[var(--ink)] p-2 text-left text-xs leading-snug text-[var(--paper)]">
                    <span className="mb-1 block text-[10px] font-black uppercase text-[var(--missing)]">{t(("guard." + g.action) as DictKey)}</span>
                    {g.text}
                  </button>
                )),
              )}
            </LaneCell>
          ))}
        </div>
      </section>

      {/* step detail */}
      {selected ? <StepDetail key={selected.id} map={map} step={selected} onApprove={(v) => setApproved(selected.id, v)} /> : <EmptyState>{t("map.select")}</EmptyState>}

      {map.open_unknowns.length ? (
        <Panel tone="paper" className="p-4">
          <p className="mb-2 flex items-center gap-2 font-extrabold">
            <CircleHelp className="size-5" aria-hidden /> {map.open_unknowns.length} {t("home.openUnknowns")}
          </p>
          <ul className="space-y-1.5 text-sm">
            {map.open_unknowns.map((u) => (
              <li key={u.id} className="flex flex-wrap items-center gap-2">
                <span className="rounded-[3px] border-2 border-[var(--ink)] bg-white px-1.5 font-mono text-[10px] font-bold uppercase">{u.type}</span>
                {u.spoken_question ?? u.hypothesis}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}

function LaneLabel({ children, icon, last }: { children: React.ReactNode; icon: React.ReactNode; last?: boolean }) {
  return (
    <div className={cn("sticky left-0 z-10 flex items-start gap-1.5 bg-[var(--paper-2)] p-2.5 text-xs font-black uppercase tracking-wide", !last && "border-b-2 border-[var(--ink)]")}>
      {icon}
      {children}
    </div>
  );
}
function LaneCell({ children, last }: { children: React.ReactNode; last?: boolean }) {
  return <div className={cn("flex flex-col gap-2 border-l-2 border-[var(--ink)] p-2", !last && "border-b-2")}>{children}</div>;
}

function citation(n: ContextNote, general: string) {
  if (n.source.startsWith("onet:")) return { label: `O*NET ${n.source.slice(5)}`, href: `https://www.onetonline.org/link/summary/${n.source.slice(5)}` };
  if (n.source.startsWith("http")) {
    try {
      return { label: new URL(n.source).hostname.replace(/^www\./, ""), href: n.source };
    } catch {
      return { label: n.source, href: undefined };
    }
  }
  return { label: n.source === "llm" ? general : n.source, href: undefined };
}

function StepDetail({ map, step, onApprove }: { map: WorkMap; step: Step; onApprove: (v: boolean) => void }) {
  const { t } = useUi();
  const reasons = quotesFor(map, step.decision?.reason_quote_ids);
  const guards = guardrailsFor(map, step);
  const notes = step.context_note_ids.map((id) => map.context_notes.find((n) => n.id === id)).filter(Boolean) as ContextNote[];
  return (
    <section aria-label={step.title} className="claros-enter grid gap-5 lg:grid-cols-[1fr_1.2fr]">
      <Panel className="p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-xs font-black uppercase tracking-wide">{t("map.screen")}</p>
          <label className="inline-flex items-center gap-2 text-sm font-bold">
            {t("map.approve")}
            <Switch checked={step.approved} onCheckedChange={(v) => onApprove(Boolean(v))} aria-label={t("map.approve")} />
          </label>
        </div>
        <ScreenThumb keyframeId={step.moment?.keyframe_ids?.[0]} title={step.state_signature.view ?? step.title} seed={step.order} highlight={stepHighlight(step)} />
        <p className="mt-2 font-mono text-[11px] text-[var(--ink-2)]">
          {[step.state_signature.app, step.state_signature.view].filter(Boolean).join(" · ")}
          {step.moment ? ` · ${step.moment.session_id} @ ${Math.round(step.moment.t / 1000)}s` : ""}
        </p>
        <div className="mt-3 flex items-center gap-2 text-sm font-semibold">
          {step.experts.map((id, i) => (
            <span key={id} className="inline-flex items-center gap-1.5">
              <ExpertAvatar user={expertById(map, id)} size={22} index={i} /> {firstName(expertById(map, id).name)}
            </span>
          ))}
        </div>
      </Panel>

      <div className="space-y-4">
        <h2 className="text-2xl font-black leading-tight tracking-[-0.03em] text-balance">
          <span className="tnum mr-2 font-mono text-lg">{step.order}.</span>
          {step.title}
        </h2>
        {step.conflict ? <ConflictBanner map={map} step={step} /> : null}

        {step.decision && !step.conflict ? (
          <Panel className="p-4">
            <p className="text-xs font-black uppercase tracking-wide">{t("map.decision")}</p>
            <p className="mt-1 font-bold">{step.decision.description}</p>
            {step.decision.from_value || step.decision.to_value ? (
              <p className="mt-2 font-mono text-sm">
                <span className="line-through decoration-2">{step.decision.from_value}</span> → <span className="bg-[var(--claros-soft)] px-1 font-bold">{step.decision.to_value}</span>
              </p>
            ) : null}
            {step.decision.counterfactual ? (
              <p className="mt-2 text-sm">
                <span className="font-bold">{t("map.counterfactual")}:</span> {step.decision.counterfactual}
              </p>
            ) : null}
          </Panel>
        ) : null}

        {reasons.length && !step.conflict ? (
          <div>
            <p className="mb-2 text-xs font-black uppercase tracking-wide">{t("map.reason")}</p>
            <div className="space-y-2">
              {reasons.map((q) => (
                <QuoteBlock key={q.id} quote={q} />
              ))}
            </div>
          </div>
        ) : null}

        {guards.map((g) => (
          <Panel key={g.id} tone="ink" className="p-4">
            <p className="flex flex-wrap items-center gap-2 text-xs font-black uppercase tracking-wide">
              <ShieldAlert className="size-4 text-[var(--missing)]" aria-hidden /> {t(("guard." + g.action) as DictKey)}
              {g.owner ? <span className="font-mono normal-case">· {t("map.owner")}: {g.owner}</span> : null}
              {!g.approved ? <span className="rounded-[3px] bg-[var(--partial)] px-1 text-[var(--ink)]">{t("map.unconfirmed")}</span> : null}
            </p>
            <p className="mt-1.5 font-semibold">{g.text}</p>
            {g.predicate ? <code className="mt-2 block overflow-x-auto rounded-[3px] bg-white/10 p-1.5 font-mono text-[11px]">{JSON.stringify(g.predicate)}</code> : null}
            <p className="mt-2 flex items-center gap-1.5 text-xs">
              {g.experts.map((id, i) => (
                <ExpertAvatar key={id} user={expertById(map, id)} size={18} index={i} />
              ))}
            </p>
          </Panel>
        ))}

        {step.variants.length && !step.conflict ? (
          <div>
            <p className="mb-2 text-xs font-black uppercase tracking-wide">{t("map.variants")}</p>
            <ul className="space-y-1.5">
              {step.variants.map((v, i) => (
                <li key={v.expert_id} className="flex items-center gap-2 rounded-[4px] border-2 border-[var(--ink)] bg-white p-2 text-sm">
                  <ExpertAvatar user={expertById(map, v.expert_id)} size={20} index={i} /> {v.description}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {notes.length ? (
          <div>
            <p className="mb-2 text-xs font-black uppercase tracking-wide">{t("map.context")}</p>
            <ul className="space-y-1.5">
              {notes.map((n) => {
                const c = citation(n, t("scope.universal"));
                return (
                  <li key={n.id} className="rounded-[4px] border-2 border-dashed border-[var(--ink)] bg-[var(--paper)] p-2.5 text-sm">
                    {n.text}{" "}
                    <span className="whitespace-nowrap font-mono text-[11px] font-bold">
                      — {t(("scope." + n.scope) as DictKey)} ·{" "}
                      {c.href ? (
                        <a href={c.href} target="_blank" rel="noreferrer" className="underline decoration-2 underline-offset-2">
                          {c.label}
                        </a>
                      ) : (
                        c.label
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}
