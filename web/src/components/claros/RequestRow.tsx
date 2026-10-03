"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight } from "lucide-react";
import type { CaptureRequest } from "@/lib/contracts";
import { acceptRequest } from "@/lib/api";
import { timeAgo, useUi } from "./i18n";
import { Panel, ScreenThumb } from "./primitives";

export function RequestRow({ r, big }: { r: CaptureRequest; big?: boolean }) {
  const { t } = useUi();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(r.status !== "open");
  const accept = async () => {
    setBusy(true);
    const res = await acceptRequest(r.id);
    setAccepted(true);
    router.push(`/capture/${res.data.session_id}?request=${r.id}`);
  };
  return (
    <Panel className={`grid gap-4 p-3.5 ${big ? "sm:grid-cols-[240px_1fr]" : "sm:grid-cols-[150px_1fr]"}`}>
      <div>
        <ScreenThumb keyframeId={r.moment?.keyframe_ids?.[0]} title={r.workflow_hint} alt={t("inbox.moment")} />
        {big ? <p className="mt-1 text-[11px] font-semibold text-[var(--ink-2)]">{t("inbox.moment")}</p> : null}
      </div>
      <div className="flex min-w-0 flex-col">
        <p className="text-lg font-extrabold leading-snug tracking-[-0.02em]">{r.workflow_hint}</p>
        <p className="mt-1 text-sm text-[var(--ink-2)]">{t("inbox.asked", { name: r.requested_by.name, ago: timeAgo(t, r.created_at) })}</p>
        {r.onet ? (
          <p className="mt-1 font-mono text-[11px]">
            O*NET {r.onet.occupation_code} · {r.onet.occupation_title}
          </p>
        ) : null}
        <div className="mt-auto flex flex-wrap gap-2 pt-3">
          <button
            type="button"
            onClick={accept}
            disabled={busy || accepted}
            className="inline-flex h-10 items-center gap-2 rounded-[4px] border-2 border-[var(--ink)] bg-[var(--ink)] px-4 text-sm font-bold text-[var(--paper)] shadow-[3px_3px_0_0_var(--claros)] transition-[transform,box-shadow] hover:translate-x-[3px] hover:translate-y-[3px] hover:shadow-none disabled:opacity-60"
          >
            {accepted ? t("inbox.accepted") : t("inbox.accept")}
            {!accepted ? <ArrowRight className="size-4" aria-hidden /> : null}
          </button>
          {big && !accepted ? (
            <button type="button" className="h-10 rounded-[4px] border-2 border-[var(--ink)] bg-white px-3 text-sm font-bold hover:bg-[var(--paper-2)]">
              {t("inbox.later")}
            </button>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}
