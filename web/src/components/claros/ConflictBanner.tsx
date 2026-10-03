"use client";

import { Users } from "lucide-react";
import type { Step, WorkMap } from "@/lib/contracts";
import { useUi } from "./i18n";
import { ExpertAvatar, QuoteBlock } from "./primitives";
import { expertById, firstName, quotesFor } from "./mapUtils";

export function ConflictBanner({ map, step }: { map: WorkMap; step: Step }) {
  const { t } = useUi();
  // Experts without an explicit variant hold the step's main decision (merge keeps one side as the decision).
  const positions = [
    ...step.experts
      .filter((id) => !step.variants.some((v) => v.expert_id === id))
      .slice(0, step.decision ? 1 : 0)
      .map((id) => ({ expert_id: id, description: step.decision!.description, reason_quote_ids: step.decision!.reason_quote_ids })),
    ...step.variants,
  ];
  return (
    <div className="rounded-[6px] border-2 border-[var(--ink)] bg-[var(--partial)] p-4 shadow-[var(--hard)]" role="note">
      <p className="flex items-center gap-2 text-lg font-black tracking-[-0.02em]">
        <Users className="size-5" aria-hidden /> {t("learn.conflict.title")}
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {positions.map((v, i) => {
          const ex = expertById(map, v.expert_id);
          return (
            <div key={v.expert_id} className="rounded-[4px] border-2 border-[var(--ink)] bg-white p-3">
              <p className="flex items-center gap-2 text-sm font-extrabold">
                <ExpertAvatar user={ex} size={22} index={i} /> {firstName(ex.name)}
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

