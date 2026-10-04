"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, X } from "lucide-react";
import type { ContextNote, Step, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from "@/components/ui/drawer";
import { useUi, type DictKey } from "./i18n";
import { ExpertAvatar, QuoteBlock, useMediaQuery } from "./primitives";
import { ConflictNote } from "./ConflictBanner";
import { ShotStrip } from "./Lightbox";
import { KindChip } from "./chips";
import { expertById, guardrailsFor, isJudgment, quotesFor, stepHighlight } from "./mapUtils";

/** Where to land inside the panel: "decision" | "conflict" | "context" | `guard-${id}` */
export type Section = string | null;

export function StepPanel({
  map,
  step,
  index,
  total,
  section,
  open,
  onOpenChange,
  onPrev,
  onNext,
  onApprove,
}: {
  map: WorkMap;
  step: Step | null;
  index: number;
  total: number;
  section: Section;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onPrev?: () => void;
  onNext?: () => void;
  onApprove: (id: string, v: boolean) => void;
}) {
  const desktop = useMediaQuery("(min-width: 768px)");
  if (!step) return null;
  const body = (
    <PanelBody map={map} step={step} index={index} total={total} section={section} onClose={() => onOpenChange(false)} onPrev={onPrev} onNext={onNext} onApprove={onApprove} desktop={desktop} />
  );
  if (desktop)
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" showCloseButton={false} className="w-full gap-0 border-0 border-l-2 border-ink bg-paper p-0 text-ink sm:max-w-[720px]">
          {body}
        </SheetContent>
      </Sheet>
    );
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="border-ink bg-paper text-ink">{body}</DrawerContent>
    </Drawer>
  );
}

function PanelBody({
  map,
  step,
  index,
  total,
  section,
  onClose,
  onPrev,
  onNext,
  onApprove,
  desktop,
}: {
  map: WorkMap;
  step: Step;
  index: number;
  total: number;
  section: Section;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  onApprove: (id: string, v: boolean) => void;
  desktop: boolean;
}) {
  const { t } = useUi();
  const scroller = useRef<HTMLDivElement>(null);
  const [flash, setFlash] = useState<Section>(null);
  const Title = desktop ? SheetTitle : DrawerTitle;
  const Desc = desktop ? SheetDescription : DrawerDescription;

  useEffect(() => {
    const root = scroller.current;
    if (!root) return;
    if (!section) {
      root.scrollTo({ top: 0 });
      return;
    }
    const h = setTimeout(() => {
      const el = root.querySelector<HTMLElement>(`[data-section="${section}"]`);
      if (!el) return;
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      root.scrollTo({ top: el.offsetTop - 12, behavior: reduce ? "auto" : "smooth" });
      setFlash(section);
    }, 260);
    const h2 = setTimeout(() => setFlash(null), 1800);
    return () => {
      clearTimeout(h);
      clearTimeout(h2);
    };
  }, [section, step.id]);

  return (
    <div className="flex h-full max-h-full min-h-0 flex-col">
      <header className="flex items-start gap-3 border-b-2 border-ink bg-card px-4 py-3 sm:px-5 sm:py-4">
        <span className="tnum grid size-12 shrink-0 place-items-center rounded-base border-2 border-ink bg-ink font-mono text-lg font-black text-paper">{step.order}</span>
        <div className="min-w-0 flex-1">
          <Desc className="text-sm font-semibold text-ink-2">
            {t("map.step", { n: index + 1 })} / {total}
            {step.state_signature.app || step.state_signature.view ? ` · ${[step.state_signature.app, step.state_signature.view].filter(Boolean).join(" · ")}` : ""}
          </Desc>
          <Title className="mt-0.5 text-xl font-extrabold leading-snug tracking-[-0.02em] sm:text-2xl">{step.title}</Title>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="outline" size="icon-sm" onClick={onPrev} disabled={!onPrev} aria-label={t("common.prev")}>
            <ChevronLeft />
          </Button>
          <Button variant="outline" size="icon-sm" onClick={onNext} disabled={!onNext} aria-label={t("common.next")}>
            <ChevronRight />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label={t("common.close")}>
            <X />
          </Button>
        </div>
      </header>
      <div ref={scroller} className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-5 sm:px-5">
        <StepDetail map={map} step={step} flash={flash} onApprove={(v) => onApprove(step.id, v)} />
        {onNext ? (
          <Button variant="secondary" size="lg" className="mt-8 w-full justify-between" onClick={onNext}>
            {t("common.next")} <ArrowRight />
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function Block({ id, title, children, flash, className }: { id: string; title?: React.ReactNode; children: React.ReactNode; flash: Section; className?: string }) {
  return (
    <section
      data-section={id}
      className={cn("rounded-[6px] transition-[box-shadow] duration-500", flash === id && "shadow-[0_0_0_4px_var(--claros)]", className)}
    >
      {title ? <h3 className="mb-3 flex items-center gap-2 text-base font-extrabold text-ink-2">{title}</h3> : null}
      {children}
    </section>
  );
}

function citation(n: ContextNote, general: string) {
  if (n.source.startsWith("onet:")) return { label: `O*NET ${n.source.slice(5)}`, href: `https://www.onetonline.org/link/summary/${n.source.slice(5)}` };
  const src = n.source.replace(/^app_docs:/, "");
  if (src.startsWith("http")) {
    try {
      return { label: new URL(src).hostname.replace(/^www\./, ""), href: src };
    } catch {
      return { label: src, href: undefined };
    }
  }
  return { label: n.source === "llm" ? general : n.source, href: undefined };
}

export function StepDetail({ map, step, flash, onApprove }: { map: WorkMap; step: Step; flash: Section; onApprove: (v: boolean) => void }) {
  const { t, role } = useUi();
  const reasons = quotesFor(map, step.decision?.reason_quote_ids);
  const guards = guardrailsFor(map, step);
  const notes = step.context_note_ids.map((id) => map.context_notes.find((n) => n.id === id)).filter(Boolean) as ContextNote[];
  const frames = (step.moment?.keyframe_ids ?? []).map((id, i, all) => ({
    id,
    keyframeId: id,
    seed: step.order + i,
    title: step.title,
    highlight: i === all.length - 1 ? stepHighlight(step) : null,
    caption: `${step.order}. ${step.title}`,
  }));
  if (!frames.length) frames.push({ id: `synthetic-${step.id}`, keyframeId: "", seed: step.order, title: step.state_signature.view ?? step.title, highlight: stepHighlight(step), caption: `${step.order}. ${step.title}` });

  return (
    <div className="space-y-8">
      <Block id="screen" flash={flash}>
        <ShotStrip group={`step-${step.id}`} frames={frames} />
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm font-semibold text-ink-2">
          {step.experts.map((id) => (
            <span key={id} className="inline-flex items-center gap-1.5">
              <ExpertAvatar user={expertById(map, id)} size={24} index={Math.max(0, map.experts.findIndex((e) => e.id === id))} />
              {expertById(map, id).name}
            </span>
          ))}
        </div>
      </Block>

      {step.conflict ? (
        <Block id="conflict" flash={flash}>
          <ConflictNote map={map} step={step} detailed />
        </Block>
      ) : null}

      {step.decision && !step.conflict ? (
        <Block id="decision" flash={flash} title={isJudgment(step) ? <KindChip kind="judgment" size="sm">{t("chip.judgment")}</KindChip> : t("map.decision")}>
          <p className="text-2xl font-extrabold leading-snug tracking-[-0.02em]">{step.decision.description}</p>
          {step.decision.from_value || step.decision.to_value ? (
            <p className="mt-3 flex flex-wrap items-center gap-2 font-mono text-lg">
              {step.decision.from_value ? <span className="text-ink-2 line-through decoration-2">{step.decision.from_value}</span> : null}
              <ArrowRight className="size-5" aria-hidden />
              <span className="rounded-[3px] bg-claros-soft px-1.5 font-bold">{step.decision.to_value}</span>
            </p>
          ) : null}
          {step.decision.counterfactual ? (
            <p className="mt-3 text-lg">
              <span className="font-bold">{t("map.counterfactual")}:</span> {step.decision.counterfactual}
            </p>
          ) : null}
          {reasons.length ? (
            <div className="mt-4 space-y-3">
              {reasons.map((q) => (
                <QuoteBlock key={q.id} quote={q} />
              ))}
            </div>
          ) : null}
        </Block>
      ) : null}

      {guards.length ? (
        <div className="space-y-3">
          <h3 className="text-base font-extrabold text-ink-2">{t("map.rules")}</h3>
          {guards.map((g) => (
            <Block key={g.id} id={`guard-${g.id}`} flash={flash}>
              <div className="rounded-base border-2 border-ink bg-ink p-4 text-paper">
                <p className="flex flex-wrap items-center gap-2 text-sm font-bold">
                  <KindChip kind="guardrail" size="sm" className="border-paper">
                    {t(("guard." + g.action) as DictKey)}
                  </KindChip>
                  {g.owner ? <span>· {t("map.owner")}: {g.owner}</span> : null}
                </p>
                <p className="mt-2 text-lg font-semibold leading-snug">{g.text}</p>
                {role === "expert" && g.predicate ? (
                  <code className="mt-3 block overflow-x-auto rounded-[3px] bg-paper/10 p-2 font-mono text-xs">{JSON.stringify(g.predicate)}</code>
                ) : null}
              </div>
              {quotesFor(map, g.quote_ids).map((q) => (
                <div key={q.id} className="mt-2">
                  <QuoteBlock quote={q} compact />
                </div>
              ))}
            </Block>
          ))}
        </div>
      ) : null}

      {notes.length ? (
        <Block id="context" flash={flash} title={t("map.context")}>
          <ul className="space-y-2">
            {notes.map((n) => {
              const c = citation(n, t("scope.universal"));
              return (
                <li key={n.id} className="rounded-base border-2 border-dashed border-ink bg-card p-3 text-base">
                  {n.text}
                  <span className="mt-1.5 block text-sm font-semibold text-ink-2">
                    {c.href ? (
                      <a href={c.href} target="_blank" rel="noreferrer" className="underline decoration-2 underline-offset-2 hover:text-ink">
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
        </Block>
      ) : null}

      {role === "expert" ? (
        <label className="flex items-center justify-between gap-3 rounded-base border-2 border-ink bg-card p-4 text-lg font-bold">
          {t("map.approve")}
          <Switch checked={step.approved} onCheckedChange={(v) => onApprove(Boolean(v))} aria-label={t("map.approve")} />
        </label>
      ) : null}
    </div>
  );
}
