"use client";

import { Inbox } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi } from "@/components/claros/i18n";
import { EmptyState, ErrorState, Loading, SourceNote, useResource } from "@/components/claros/primitives";
import { RequestRow } from "@/components/claros/RequestRow";
import { listRequests } from "@/lib/api";

export default function InboxPage() {
  return (
    <Shell>
      <InboxView />
    </Shell>
  );
}

function InboxView() {
  const { t } = useUi();
  const reqs = useResource(listRequests, []);
  const items = [...(reqs.data ?? [])].sort((a, b) => Number(a.status !== "open") - Number(b.status !== "open") || b.created_at - a.created_at);
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-4xl font-black tracking-[-0.04em]">{t("inbox.title")}</h1>
          <p className="mt-2 max-w-[60ch] text-[var(--ink-2)]">{t("inbox.sub")}</p>
        </div>
        <SourceNote source={reqs.source} />
      </div>
      {reqs.loading ? (
        <Loading rows={3} />
      ) : reqs.error ? (
        <ErrorState message={reqs.error} onRetry={reqs.retry} />
      ) : !items.length ? (
        <EmptyState icon={<Inbox className="size-5" aria-hidden />}>{t("state.empty.requests")}</EmptyState>
      ) : (
        <div className="space-y-4">
          {items.map((r) => (
            <RequestRow key={r.id} r={r} big />
          ))}
        </div>
      )}
    </div>
  );
}
