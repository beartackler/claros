"use client";

import { Split } from "lucide-react";
import type { Step, WorkMap } from "@/lib/contracts";
import { useUi } from "./i18n";
import { ExpertAvatar, QuoteBlock } from "./primitives";
import { expertById, positions, quotesFor } from "./mapUtils";

/** "Experts differ here": both positions side by side, never silently picking one. */
export function ConflictBanner({ map, step }: { map: WorkMap; step: Step }) {
  const { t } = useUi();
  const ps = positions(step);
  return (
    <div className="rounded-base border-2 border-ink bg-partial p-4 text-on-fill shadow-hard" role="note">
      <p className="flex items-center gap-2 text-lg font-black tracking-[-0.02em]">
        <Split className="size-5" aria-hidden /> {t("learn.conflict.title")}
      </p>
      {step.conflict ? <p className="mt-1 text-sm font-semibold">{step.conflict}</p> : null}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {ps.map((v) => {
          const ex = expertById(map, v.expert_id);
          const idx = Math.max(0, map.experts.findIndex((e) => e.id === v.expert_id));
          return (
            <div key={v.expert_id} className="rounded-[4px] border-2 border-ink bg-card p-3 text-ink">
              <p className="flex items-center gap-2 text-sm font-extrabold">
                <ExpertAvatar user={ex} size={24} index={idx} /> {ex.name}
              </p>
              <p className="mt-1.5 text-sm font-semibold">{v.description}</p>
              {quotesFor(map, v.reason_quote_ids).map((q) => (
                <div key={q.id} className="mt-2">
                  <QuoteBlock quote={q} compact />
                </div>
              ))}
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-sm font-bold">{t("learn.conflict.ask")}</p>
    </div>
  );
}
