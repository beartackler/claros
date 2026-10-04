"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, Check, CheckCircle2, CircleDashed, Clock, Mic } from "lucide-react";
import type { CaptureRequest, WorkMap } from "@/lib/contracts";
import type { WorkflowSummary } from "@/lib/mock";
import { acceptRequest, getWorkflow } from "@/lib/api";
import { EXPERT } from "@/lib/mock";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { timeAgo, useUi } from "./i18n";
import { AvatarStack, ExpertAvatar, ScreenThumb } from "./primitives";
import { ZoomShot } from "./Lightbox";
import { sortedSteps } from "./mapUtils";

/* ---------------- full maps for a list of summaries (thumbnails + honest status) ---------------- */

export function useMaps(list: WorkflowSummary[] | undefined) {
  const [maps, setMaps] = useState<Record<string, WorkMap>>({});
  const ids = (list ?? []).map((w) => w.workflow_id).join(",");
  useEffect(() => {
    if (!ids) return;
    let alive = true;
    Promise.all(ids.split(",").slice(0, 12).map(async (id) => [id, (await getWorkflow(id)).data] as const)).then((r) => alive && setMaps(Object.fromEntries(r)));
    return () => {
      alive = false;
    };
  }, [ids]);
  return maps;
}

/** One clear state per map: Ready, or Needs a second run (anything unresolved). */
export function mapReady(w: Pick<WorkflowSummary, "coverage">, map?: WorkMap) {
  if (map) return map.coverage.status === "ready" && !map.steps.some((s) => s.conflict || !s.approved);
  return w.coverage.status === "ready" && !w.coverage.conflicts;
}

export function MapStatus({ ready, className }: { ready: boolean; className?: string }) {
  const { t } = useUi();
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[4px] border-2 border-ink px-2 py-0.5 text-sm font-extrabold text-on-fill",
        ready ? "bg-ready" : "hatch-partial",
        className,
      )}
    >
      {ready ? <CheckCircle2 className="size-4" aria-hidden /> : <CircleDashed className="size-4" aria-hidden />}
      {ready ? t("status.ready") : t("status.second")}
    </span>
  );
}

/** The step a map is best remembered by: its first judgment call, else its first step. */
export function heroStep(map?: WorkMap) {
  if (!map) return undefined;
  const steps = sortedSteps(map);
  return steps.find((s) => s.decision?.kind === "judgment" && s.moment?.keyframe_ids.length) ?? steps[0];
}

/* ---------------- work map card (expert home, /map) ---------------- */

export function MapCard({ w, map }: { w: WorkflowSummary; map?: WorkMap }) {
  const { tn } = useUi();
  const hero = heroStep(map);
  const href = `/map/${encodeURIComponent(w.workflow_id)}`;
  return (
    <article className="press-within relative flex h-full flex-col overflow-hidden rounded-base border-2 border-ink bg-card shadow-hard">
      <div className="aspect-[16/10] overflow-hidden border-b-2 border-ink bg-paper-2">
        {hero ? (
          <ScreenThumb keyframeId={hero.moment?.keyframe_ids?.[0]} title={hero.state_signature.view ?? w.name} rounded={false} className="h-full border-0" />
        ) : (
          <div className="hatch h-full w-full" />
        )}
      </div>
      <div className="flex flex-1 flex-col p-5">
        <MapStatus ready={mapReady(w, map)} className="self-start" />
        <h3 className="mt-3 text-xl font-extrabold leading-snug tracking-[-0.02em]">
          <Link href={href} className="after:absolute after:inset-0 after:z-[1] focus-visible:outline-none">
            {w.name}
          </Link>
        </h3>
        <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-2 pt-4 text-base font-semibold text-ink-2">
          {w.experts.length ? <AvatarStack users={w.experts} size={30} /> : null}
          {w.step_count ? <span className="tnum">{tn("steps", w.step_count)}</span> : null}
          <ArrowRight className="ml-auto size-6 text-ink" aria-hidden />
        </div>
      </div>
    </article>
  );
}

/* ---------------- request card (expert home: learners are waiting on) ---------------- */

export function RequestCard({ r }: { r: CaptureRequest }) {
  const { t, lang } = useUi();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const open = r.status === "open";
  const record = async () => {
    setBusy(true);
    const res = await acceptRequest(r.id, EXPERT, lang);
    router.push(`/capture/${res.data.session_id}?request=${r.id}`);
  };
  const kf = r.moment?.keyframe_ids?.[0];
  return (
    <article className="grid gap-4 rounded-base border-2 border-ink bg-card p-4 shadow-hard sm:grid-cols-[minmax(0,240px)_1fr] lg:grid-cols-1 2xl:grid-cols-[minmax(0,260px)_1fr]">
      <ZoomShot group={`req-${r.id}`} frames={[{ id: kf ?? r.id, keyframeId: kf, title: r.workflow_hint, caption: `${r.requested_by.name} · ${r.workflow_hint}` }]} label={t("req.moment")} />
      <div className="flex min-w-0 flex-col">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-base">
          <ExpertAvatar user={r.requested_by} size={30} index={2} />
          <span className="font-extrabold">{r.requested_by.name}</span>
          <span className="inline-flex items-center gap-1 text-ink-2">
            <Clock className="size-4" aria-hidden /> {timeAgo(t, r.created_at)}
          </span>
        </p>
        <p className="mt-2 text-xl font-extrabold leading-snug tracking-[-0.02em]">{r.workflow_hint}</p>
        <div className="mt-auto pt-4">
          {open ? (
            <Button variant="primary" size="lg" onClick={record} loading={busy}>
              {!busy ? <Mic aria-hidden /> : null}
              {t("eh.record")}
            </Button>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-[4px] border-2 border-ink bg-paper-2 px-2 py-0.5 text-sm font-bold">
              {t(`req.status.${r.status}` as "req.status.accepted")}
            </span>
          )}
        </div>
      </div>
    </article>
  );
}

/* ---------------- learner: request status ---------------- */

export function RequestStatus({ r }: { r: CaptureRequest }) {
  const { t } = useUi();
  const stage = r.status === "done" ? 2 : r.status === "recorded" || r.status === "accepted" ? 1 : 0;
  const labels = [t("myreq.asked"), t("myreq.recorded"), t("myreq.ready")];
  return (
    <li className="flex flex-col gap-3 rounded-base border-2 border-ink bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-lg font-extrabold leading-snug">{r.workflow_hint}</p>
        <span className="shrink-0 text-sm font-semibold text-ink-2">{timeAgo(t, r.created_at)}</span>
      </div>
      <ol className="flex items-center gap-1.5" aria-label={labels[stage]}>
        {labels.map((l, i) => (
          <li key={l} className="flex items-center gap-1.5">
            <span
              aria-current={i === stage ? "step" : undefined}
              className={cn(
                "inline-flex items-center gap-1 rounded-[4px] border-2 border-ink px-2 py-0.5 text-sm font-bold",
                i < stage ? "bg-ready text-on-fill" : i === stage ? "bg-ink text-paper" : "border-dashed bg-card text-ink-2",
              )}
            >
              {i < stage ? <Check className="size-4" aria-hidden /> : null}
              {l}
            </span>
            {i < labels.length - 1 ? <span aria-hidden className="h-0.5 w-3 bg-ink" /> : null}
          </li>
        ))}
      </ol>
    </li>
  );
}

/** Test/junk requests ("test", "x") never reach the queue. */
export const isJunkRequest = (r: CaptureRequest) => {
  const h = r.workflow_hint.trim();
  return h.length < 4 || /^(test|testing|asdf|foo|tmp)\b/i.test(h);
};

/** Test maps (wf_test_…, "test") never reach a list. */
export const isJunkWorkflow = (w: Pick<WorkflowSummary, "workflow_id" | "name">) =>
  /^wf_test/.test(w.workflow_id) || w.name.trim().length < 4 || /^(test|testing|asdf|foo|tmp)\b/i.test(w.name.trim());
