"use client";

import { Shell } from "@/components/claros/Shell";
import { useUi } from "@/components/claros/i18n";
import { EmptyState, ErrorState, Loading, SourceNote, useResource } from "@/components/claros/primitives";
import { MapCard, isJunkWorkflow, useMaps } from "@/components/claros/cards";
import { listWorkflows } from "@/lib/api";

/** /map — every Work Map, newest first. */
export default function WorkflowsPage() {
  return (
    <Shell>
      <Library />
    </Shell>
  );
}

function Library() {
  const { t } = useUi();
  const wf = useResource(listWorkflows, []);
  const list = [...(wf.data ?? [])].filter((w) => !isJunkWorkflow(w)).sort((a, b) => b.updated_at - a.updated_at);
  const maps = useMaps(list);
  return (
    <div>
      <header className="mb-10 flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-4xl font-black leading-[1] tracking-[-0.04em] sm:text-6xl">{t("nav.maps")}</h1>
        <SourceNote source={wf.source} />
      </header>
      {wf.loading ? (
        <Loading rows={3} />
      ) : wf.error ? (
        <ErrorState message={wf.error} onRetry={wf.retry} />
      ) : !list.length ? (
        <EmptyState>{t("eh.maps.empty")}</EmptyState>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {list.map((w) => (
            <MapCard key={w.workflow_id} w={w} map={maps[w.workflow_id]} />
          ))}
        </div>
      )}
    </div>
  );
}
