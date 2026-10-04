"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, ArrowUpRight, Check, CircleHelp, Clock, Mic, Split } from "lucide-react";
import type { CaptureRequest, MasteryNode } from "@/lib/contracts";
import type { WorkflowSummary } from "@/lib/mock";
import { acceptRequest, getWorkflow } from "@/lib/api";
import { honestCoverage } from "./mapUtils";
import { EXPERT } from "@/lib/mock";
import { cn } from "@/lib/utils";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { timeAgo, useUi } from "./i18n";
import { AppChip, AvatarStack, CoverageChip, ExpertAvatar, OnetTag, ScreenThumb } from "./primitives";

/* ---------------- workflow card (library, expert "your workflows") ---------------- */

function HealthBar({ value, label }: { value: number; label: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-[11px] font-semibold leading-tight text-ink-2">
        <span>{label}</span>
        <span className="tnum font-mono text-ink">{pct}%</span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-[2px] border-2 border-ink bg-card" aria-hidden>
        <div className={cn("h-full", pct >= 100 ? "bg-ready" : pct >= 50 ? "bg-partial" : "bg-missing")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export type Health = ReturnType<typeof honestCoverage>;

/** Honest per-workflow health, computed from the full maps (server ratios ignore conflicts). */
export function useHealth(list: WorkflowSummary[] | undefined, enabled = true) {
  const [h, setH] = useState<Record<string, Health>>({});
  const ids = (list ?? []).map((w) => w.workflow_id).join(",");
  useEffect(() => {
    if (!enabled || !ids) return;
    let alive = true;
    Promise.all(ids.split(",").slice(0, 12).map(async (id) => [id, honestCoverage((await getWorkflow(id)).data)] as const)).then(
      (r) => alive && setH(Object.fromEntries(r)),
    );
    return () => {
      alive = false;
    };
  }, [ids, enabled]);
  return h;
}

export function WorkflowCard({ w, variant = "learner", health }: { w: WorkflowSummary; variant?: "learner" | "expert"; health?: Health }) {
  const { t, tn } = useUi();
  const open = health ? health.open - health.conflicts : Math.max(0, w.coverage.open_unknowns - 2 * w.coverage.conflicts); // server stores one conflict as a mirrored pair
  const learnable = w.coverage.status !== "missing";
  return (
    <article className="group relative flex h-full flex-col rounded-base border-2 border-ink bg-card p-4 shadow-hard transition-[transform,box-shadow] duration-150 focus-within:-translate-x-px focus-within:-translate-y-px hover:-translate-x-px hover:-translate-y-px hover:shadow-hard-lg sm:p-5">
      <div className="flex flex-wrap items-center gap-1.5">
        <CoverageChip status={w.coverage.status} />
        {w.apps.map((a) => (
          <AppChip key={a} name={a} />
        ))}
      </div>
      <h3 className="mt-3 text-lg font-extrabold leading-snug tracking-[-0.02em]">
        <Link href={`/map/${encodeURIComponent(w.workflow_id)}`} className="after:absolute after:inset-0 after:rounded-base focus-visible:outline-none group-focus-within:underline">
          {w.name}
        </Link>
      </h3>
      {w.onet_task ? <p className="mt-1 line-clamp-2 text-sm text-ink-2">{w.onet_task}</p> : null}

      {variant === "expert" ? (
        <div className="mt-4 grid grid-cols-3 gap-3">
          <HealthBar value={health?.evidence ?? w.coverage.steps_with_evidence} label={t("wf.evidence")} />
          <HealthBar value={health?.explained ?? w.coverage.judgments_complete} label={t("wf.judgments")} />
          <HealthBar value={health?.rulesOk ?? w.coverage.guardrails_complete} label={t("wf.guardrails")} />
        </div>
      ) : null}

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-2 pt-4 text-xs font-semibold text-ink-2">
        {w.experts.length ? (
          <span className="inline-flex items-center gap-1.5">
            <AvatarStack users={w.experts} size={24} />
            <span className="max-w-[16ch] truncate">{w.experts.map((e) => e.name.split(" ")[0]).join(", ")}</span>
          </span>
        ) : null}
        {w.step_count ? <span className="tnum">{tn("steps", w.step_count)}</span> : null}
        {open ? (
          <span className="tnum inline-flex items-center gap-1">
            <CircleHelp className="size-3.5" aria-hidden />
            {tn("open", open)}
          </span>
        ) : null}
        {w.coverage.conflicts ? (
          <span className="tnum inline-flex items-center gap-1">
            <Split className="size-3.5" aria-hidden />
            {tn("conflicts", w.coverage.conflicts)}
          </span>
        ) : null}
        <span className="ml-auto inline-flex items-center gap-2">
          {variant === "learner" && learnable ? (
            <Link href={`/learn?wf=${encodeURIComponent(w.workflow_id)}`} className={cn(buttonVariants({ variant: "claros", size: "xs" }), "relative z-[2]")}>
              {t("wf.learn")}
            </Link>
          ) : null}
          <ArrowUpRight className="size-5 text-ink transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
        </span>
      </div>
    </article>
  );
}

/* ---------------- request card (expert queue) ---------------- */

export function RequestCard({ r, onLater }: { r: CaptureRequest; onLater?: () => void }) {
  const { t, lang } = useUi();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const open = r.status === "open";
  const record = async () => {
    setBusy(true);
    const res = await acceptRequest(r.id, EXPERT, lang);
    router.push(`/capture/${res.data.session_id}?request=${r.id}`);
  };
  return (
    <article className="grid gap-4 rounded-base border-2 border-ink bg-card p-3 shadow-hard sm:grid-cols-[200px_1fr] sm:p-4">
      <figure className="min-w-0">
        <ScreenThumb keyframeId={r.moment?.keyframe_ids?.[0]} alt={t("req.moment")} />
        <figcaption className="sr-only">{t("req.moment")}</figcaption>
      </figure>
      <div className="flex min-w-0 flex-col">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <ExpertAvatar user={r.requested_by} size={26} index={2} />
          <span className="font-extrabold">{r.requested_by.name}</span>
          <span className="inline-flex items-center gap-1 text-ink-2">
            <Clock className="size-3.5" aria-hidden /> {timeAgo(t, r.created_at)}
          </span>
          {!open ? <Badge variant={r.status === "done" ? "ready" : "neutral"}>{t(`req.status.${r.status}` as "req.status.accepted")}</Badge> : null}
        </div>
        <p className="mt-2.5 text-xs font-semibold text-ink-2">{t("req.trying")}</p>
        <p className="text-lg font-extrabold leading-snug tracking-[-0.02em]">{r.workflow_hint}</p>
        {r.onet ? (
          <div className="mt-2">
            <OnetTag onet={r.onet} />
          </div>
        ) : null}
        {open ? (
          <div className="mt-auto flex flex-wrap gap-2 pt-4">
            <Button variant="primary" onClick={record} loading={busy}>
              {!busy ? <Mic aria-hidden /> : null}
              {t("req.record")}
            </Button>
            {onLater ? (
              <Button variant="ghost" onClick={onLater} disabled={busy}>
                <Clock aria-hidden /> {t("req.later")}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
    </article>
  );
}

/* ---------------- learner: request status ---------------- */

export function RequestStatus({ r }: { r: CaptureRequest }) {
  const { t } = useUi();
  const stage = r.status === "done" ? 2 : r.status === "recorded" ? 1 : 0;
  const labels = [t("myreq.asked"), t("myreq.recorded"), t("myreq.ready")];
  return (
    <li className="flex flex-col gap-3 rounded-base border-2 border-ink bg-card p-3 sm:flex-row sm:items-center sm:p-4">
      <div className="min-w-0 flex-1">
        <p className="font-extrabold leading-snug">{r.workflow_hint}</p>
        <p className="mt-0.5 text-xs font-semibold text-ink-2">{timeAgo(t, r.created_at)}</p>
      </div>
      <ol className="flex items-center gap-1" aria-label={labels[stage]}>
        {labels.map((l, i) => (
          <li key={l} className="flex items-center gap-1">
            <span
              aria-current={i === stage ? "step" : undefined}
              className={cn(
                "inline-flex items-center gap-1 rounded-[4px] border-2 border-ink px-2 py-0.5 text-xs font-bold",
                i < stage ? "bg-ready text-on-fill" : i === stage ? "bg-ink text-paper" : "border-dashed bg-card text-ink-2",
              )}
            >
              {i < stage ? <Check className="size-3" aria-hidden /> : null}
              {l}
            </span>
            {i < labels.length - 1 ? <span aria-hidden className="h-0.5 w-2 bg-ink" /> : null}
          </li>
        ))}
      </ol>
      {stage === 2 && r.workflow_id ? (
        <Link href={`/learn?wf=${encodeURIComponent(r.workflow_id)}`} className={buttonVariants({ variant: "claros", size: "sm" })}>
          {t("myreq.start")} <ArrowRight aria-hidden />
        </Link>
      ) : null}
    </li>
  );
}

/* ---------------- mastery strip ---------------- */

export const LEVEL_BG: Record<MasteryNode["level"], string> = {
  unseen: "bg-card",
  caught: "bg-missing",
  hinted: "bg-partial",
  unaided: "bg-ready",
};

export function MasteryStrip({ levels, total }: { levels: MasteryNode["level"][]; total: number }) {
  return (
    <div className="flex gap-1" aria-hidden>
      {Array.from({ length: total }).map((_, i) => {
        const l = levels[i] ?? "unseen";
        return <span key={i} className={cn("h-3 flex-1 rounded-[2px] border-2 border-ink", LEVEL_BG[l], l === "unseen" && "hatch")} />;
      })}
    </div>
  );
}

/** Test/junk requests ("test", "x") never reach the queue. */
export const isJunkRequest = (r: CaptureRequest) => {
  const h = r.workflow_hint.trim();
  return h.length < 4 || /^(test|testing|asdf|foo|tmp)\b/i.test(h);
};
