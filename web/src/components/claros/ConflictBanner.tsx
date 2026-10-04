"use client";

import { Split } from "lucide-react";
import type { Step, WorkMap } from "@/lib/contracts";
import { useUi } from "./i18n";
import { ExpertAvatar, QuoteBlock } from "./primitives";
import { expertById, firstName, positions, quotesFor } from "./mapUtils";

/** Names of the experts who disagree on a step ("Anna and Marco"). */
export function conflictNames(map: WorkMap, step: Step) {
  const names = positions(step).map((p) => firstName(expertById(map, p.expert_id).name));
  return [...new Set(names)];
}

/** One line on the step: experts differ, and it gets settled in the next debrief — never a lingering question. */
export function ConflictNote({ map, step, detailed }: { map: WorkMap; step: Step; detailed?: boolean }) {
  const { t } = useUi();
  const ps = positions(step);
  const [a, b] = conflictNames(map, step);
  return (
    <div className="rounded-base border-2 border-ink bg-partial/35 p-4" role="note">
      <p className="flex items-start gap-2.5 text-lg font-bold leading-snug">
        <Split className="mt-1 size-5 shrink-0" aria-hidden />
        <span>{t("map.conflict.note", { a: a ?? "", b: b ?? "" })}</span>
      </p>
      {detailed ? (
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {ps.map((v) => {
            const ex = expertById(map, v.expert_id);
            const idx = Math.max(0, map.experts.findIndex((e) => e.id === v.expert_id));
            return (
              <div key={v.expert_id} className="rounded-base border-2 border-ink bg-card p-4">
                <p className="flex items-center gap-2 text-base font-extrabold">
                  <ExpertAvatar user={ex} size={28} index={idx} /> {ex.name}
                </p>
                <p className="mt-2 text-lg font-semibold leading-snug">{v.description}</p>
                {quotesFor(map, v.reason_quote_ids).map((q) => (
                  <div key={q.id} className="mt-3">
                    <QuoteBlock quote={q} compact />
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
