"use client";

import { Shell } from "@/components/claros/Shell";
import { useUi } from "@/components/claros/i18n";
import { EmptyState, ErrorState, Loading, SourceNote, useResource } from "@/components/claros/primitives";
import { WorkflowCard } from "@/components/claros/cards";
import { listWorkflows } from "@/lib/api";

/** /map — every Work Map, newest first. */
export default function WorkflowsPage() {
  return (
    <Shell crumbs={[{ key: "crumb.workflows" }]}>
      <Library />
    </Shell>
  );
}

function Library() {
  const { t, role } = useUi();
  const wf = useResource(listWorkflows, []);
  const list = [...(wf.data ?? [])].sort((a, b) => b.updated_at - a.updated_at);
  return (
    <div>
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-[62ch]">
          <h1 className="text-4xl font-black leading-[1] tracking-[-0.04em] sm:text-5xl">{t("lib.title")}</h1>
          <p className="mt-3 leading-relaxed text-ink-2">{t("lib.sub")}</p>
        </div>
        <SourceNote source={wf.source} />
      </header>
      {wf.loading ? (
        <Loading rows={3} />
      ) : wf.error ? (
        <ErrorState message={wf.error} onRetry={wf.retry} />
      ) : !list.length ? (
        <EmptyState>{t("state.empty.workflows")}</EmptyState>
      ) : (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {list.map((w) => (
            <WorkflowCard key={w.workflow_id} w={w} variant={role === "expert" ? "expert" : "learner"} />
          ))}
        </div>
      )}
    </div>
  );
}
