"use client";

// The expert's screen moment, large: what they saw + what they said (original + translation).
import { OctagonAlert, X } from "lucide-react";
import type { Step, WorkMap } from "@/lib/contracts";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useUi, type DictKey } from "./i18n";
import { ExpertAvatar, QuoteBlock } from "./primitives";
import { ZoomShot } from "./Lightbox";
import { expertById, firstName, guardrailsFor, quotesFor, stepHighlight } from "./mapUtils";

export type MomentKind = "stop" | "how";

export function MomentCard({
  map,
  step,
  kind,
  guardrailId,
  keyframes,
  onClose,
  className,
}: {
  map: WorkMap;
  step: Step;
  kind: MomentKind;
  guardrailId?: string | null;
  keyframes?: string[];
  onClose: () => void;
  className?: string;
}) {
  const { t } = useUi();
  const guard = kind === "stop" ? (guardrailsFor(map, step).find((g) => g.id === guardrailId) ?? guardrailsFor(map, step)[0]) : undefined;
  const quotes = guard ? quotesFor(map, guard.quote_ids) : quotesFor(map, step.decision?.reason_quote_ids);
  const quote = quotes[0];
  const who = quote ? expertById(map, quote.speaker_id) : expertById(map, guard?.experts[0] ?? step.experts[0] ?? "");
  const guardFrames = guard?.evidence.flatMap((e) => e.keyframe_ids) ?? [];
  const ids = keyframes?.length ? keyframes : [...guardFrames.slice(0, 1), ...(step.moment?.keyframe_ids ?? [])];
  const uniq = [...new Set(ids.filter(Boolean))];
  const frames = (uniq.length ? uniq : [`synthetic-${step.id}`]).map((id, i) => ({
    id,
    keyframeId: id.startsWith("synthetic-") ? null : id,
    seed: step.order + i,
    title: step.title,
    highlight: stepHighlight(step),
    caption: `${firstName(who.name)} · ${step.title}`,
  }));
  const name = firstName(who.name);

  return (
    <section
      aria-live="assertive"
      className={cn(
        "claros-enter rounded-[10px] border-[3px] border-ink bg-card shadow-[8px_8px_0_0_var(--claros)]",
        className,
      )}
    >
      <header className={cn("flex items-center gap-4 border-b-[3px] border-ink p-4 sm:p-5", kind === "stop" ? "bg-claros text-claros-ink" : "bg-expert-soft")}>
        <ExpertAvatar user={who} size={48} index={Math.max(0, map.experts.findIndex((e) => e.id === who.id))} />
        <h2 className="min-w-0 flex-1 text-3xl font-black leading-tight tracking-[-0.03em] sm:text-4xl">
          {t((kind === "stop" ? "live.moment.stop" : "live.moment.how") as DictKey, { name })}
        </h2>
        <Button variant="secondary" size="lg" onClick={onClose} className="shrink-0">
          <X aria-hidden /> <span className="hidden sm:inline">{t("live.moment.close")}</span>
        </Button>
      </header>
      <div className="space-y-5 p-4 sm:p-6">
        <ZoomShot group={`moment-${step.id}-${kind}`} frames={frames} priority />
        {guard ? (
          <p className="flex items-start gap-3 rounded-base border-2 border-ink bg-ink p-4 text-xl font-bold leading-snug text-paper">
            <OctagonAlert className="mt-1 size-6 shrink-0 text-missing" aria-hidden />
            {guard.text}
          </p>
        ) : step.decision ? (
          <p className="text-2xl font-extrabold leading-snug tracking-[-0.02em]">{step.decision.description}</p>
        ) : null}
        {quote ? <QuoteBlock quote={quote} size="lg" /> : null}
      </div>
    </section>
  );
}
