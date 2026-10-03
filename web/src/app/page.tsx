"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight, ArrowUpRight, Inbox, Mic, Plus } from "lucide-react";
import { Shell, RoleSwitch } from "@/components/claros/Shell";
import { useUi } from "@/components/claros/i18n";
import {
  AvatarStack,
  ClarosDot,
  CoverageChip,
  EmptyState,
  ErrorState,
  Loading,
  SectionTitle,
  SourceNote,
  useResource,
} from "@/components/claros/primitives";
import { createSession, listRequests, listWorkflows } from "@/lib/api";
import { EXPERT, type WorkflowSummary } from "@/lib/mock";
import { RequestRow } from "@/components/claros/RequestRow";

export default function HomePage() {
  return (
    <Shell>
      <Home />
    </Shell>
  );
}

function Home() {
  const { t, role } = useUi();
  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="max-w-[40ch] text-sm font-semibold text-[var(--ink-2)]">{t("app.tagline")}</p>
        <RoleSwitch large />
      </div>
      {role === "learner" ? <LearnerHome /> : <ExpertHome />}
    </div>
  );
}

function LearnerHome() {
  const { t } = useUi();
  const wf = useResource(listWorkflows, []);
  return (
    <div className="grid gap-8 lg:grid-cols-[1.15fr_1fr]">
      <section aria-labelledby="learner-hero">
        <h1 id="learner-hero" className="text-4xl font-black leading-[0.95] tracking-[-0.04em] text-balance sm:text-6xl">
          {t("home.learner.title")}
        </h1>
        <p className="mt-4 max-w-[48ch] text-base leading-relaxed text-[var(--ink-2)]">{t("home.learner.sub")}</p>
        <Link
          href="/learn"
          className="group mt-7 flex items-center gap-4 rounded-[8px] border-[3px] border-[var(--ink)] bg-[var(--claros)] p-5 text-[var(--claros-ink)] shadow-[var(--hard-lg)] transition-[transform,box-shadow] duration-150 hover:translate-x-1.5 hover:translate-y-1.5 hover:shadow-none sm:p-7"
        >
          <ClarosDot size={52} speaking />
          <span className="flex-1">
            <span className="block text-2xl font-black tracking-[-0.03em] sm:text-3xl">{t("home.learner.cta")}</span>
            <span className="mt-1 flex items-center gap-1.5 text-sm font-semibold opacity-90">
              <Mic className="size-4" aria-hidden />
              {t("home.learner.hint")}
            </span>
          </span>
          <ArrowRight className="size-8 transition-transform group-hover:translate-x-1" aria-hidden />
        </Link>
      </section>

      <section aria-labelledby="recent">
        <SectionTitle aside={<SourceNote source={wf.source} />}>
          <span id="recent">{t("home.learner.recent")}</span>
        </SectionTitle>
        {wf.loading ? <Loading rows={4} /> : wf.error ? <ErrorState message={wf.error} onRetry={wf.retry} /> : !wf.data?.length ? (
          <EmptyState>{t("state.empty.workflows")}</EmptyState>
        ) : (
          <ul className="divide-y-2 divide-[var(--ink)] rounded-[6px] border-2 border-[var(--ink)] bg-white shadow-[var(--hard)]">
            {wf.data.map((w) => (
              <WorkflowRow key={w.workflow_id} w={w} href={w.coverage.status === "missing" ? `/learn?wf=${w.workflow_id}` : `/map/${w.workflow_id}`} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function WorkflowRow({ w, href }: { w: WorkflowSummary; href: string }) {
  const { t } = useUi();
  return (
    <li>
      <Link href={href} className="group flex items-center gap-3 p-3.5 hover:bg-[var(--paper)] sm:p-4">
        <div className="min-w-0 flex-1">
          <p className="font-bold leading-snug">{w.name}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--ink-2)]">
            <span className="font-mono">{w.apps.join(" · ")}</span>
            {w.onet_code ? <span className="font-mono">O*NET {w.onet_code}</span> : null}
            {w.coverage.open_unknowns ? <span>· {w.coverage.open_unknowns} {t("home.openUnknowns")}</span> : null}
            {w.coverage.conflicts ? <span>· {w.coverage.conflicts} {t("home.conflicts")}</span> : null}
          </p>
        </div>
        {w.experts.length ? <AvatarStack users={w.experts} size={26} /> : null}
        <CoverageChip status={w.coverage.status} />
        <ArrowUpRight className="size-4 shrink-0 opacity-50 transition-opacity group-hover:opacity-100" aria-hidden />
      </Link>
    </li>
  );
}

function ExpertHome() {
  const { t, lang } = useUi();
  const router = useRouter();
  const reqs = useResource(listRequests, []);
  const wf = useResource(listWorkflows, []);
  const [starting, setStarting] = useState(false);
  const open = (reqs.data ?? []).filter((r) => r.status === "open");
  const mine = (wf.data ?? []).filter((w) => w.experts.some((e) => e.id === EXPERT.id));

  const teach = async () => {
    setStarting(true);
    const r = await createSession({ mode: "capture", user: EXPERT, lang });
    router.push(`/capture/${r.data.session_id}`);
  };

  return (
    <div className="grid gap-8 lg:grid-cols-[1.3fr_1fr]">
      <section aria-labelledby="expert-hero">
        <h1 id="expert-hero" className="text-4xl font-black leading-[0.95] tracking-[-0.04em] text-balance sm:text-5xl">
          {t("home.expert.title")}
        </h1>
        <p className="mt-4 max-w-[52ch] leading-relaxed text-[var(--ink-2)]">{t("home.expert.sub")}</p>

        <div className="mt-7">
          <SectionTitle
            aside={
              <Link href="/inbox" className="inline-flex items-center gap-1 text-sm font-bold underline decoration-2 underline-offset-4">
                {t("home.expert.all")} <ArrowRight className="size-4" aria-hidden />
              </Link>
            }
          >
            <span className="inline-flex items-center gap-2">
              {t("home.expert.inbox")}
              {open.length ? (
                <span className="tnum grid min-w-6 place-items-center rounded-full border-2 border-[var(--ink)] bg-[var(--claros)] px-1.5 text-xs text-white">{open.length}</span>
              ) : null}
            </span>
          </SectionTitle>
          {reqs.loading ? <Loading rows={2} /> : reqs.error ? <ErrorState message={reqs.error} onRetry={reqs.retry} /> : !open.length ? (
            <EmptyState icon={<Inbox className="size-5" aria-hidden />}>{t("state.empty.requests")}</EmptyState>
          ) : (
            <div className="space-y-3">
              {open.slice(0, 3).map((r) => (
                <RequestRow key={r.id} r={r} />
              ))}
            </div>
          )}
        </div>
      </section>

      <aside className="space-y-6">
        <button
          type="button"
          onClick={teach}
          disabled={starting}
          className="flex w-full items-center gap-3 rounded-[6px] border-2 border-[var(--ink)] bg-white p-4 text-left shadow-[var(--hard)] transition-[transform,box-shadow] hover:translate-x-1 hover:translate-y-1 hover:shadow-none disabled:opacity-60"
        >
          <span className="grid size-11 place-items-center rounded-[4px] border-2 border-[var(--ink)] bg-[var(--expert)]">
            <Plus className="size-5" aria-hidden />
          </span>
          <span className="text-lg font-extrabold tracking-[-0.02em]">{t("home.expert.teach")}</span>
        </button>

        <div>
          <SectionTitle aside={<SourceNote source={wf.source} />}>{t("home.expert.contrib")}</SectionTitle>
          {wf.loading ? <Loading rows={2} /> : !mine.length ? (
            <EmptyState>{t("state.empty.contrib")}</EmptyState>
          ) : (
            <ul className="divide-y-2 divide-[var(--ink)] rounded-[6px] border-2 border-[var(--ink)] bg-white shadow-[var(--hard)]">
              {mine.map((w) => (
                <WorkflowRow key={w.workflow_id} w={w} href={`/map/${w.workflow_id}`} />
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}
