"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowRight, Check, Inbox, Loader2 } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { useUi } from "@/components/claros/i18n";
import { ClarosDot, EmptyState, ErrorState, Loading, SectionHeader, SourceNote, useResource } from "@/components/claros/primitives";
import { MapCard, RequestCard, RequestStatus, isJunkRequest, isJunkWorkflow, useMaps } from "@/components/claros/cards";
import { LearnerSession } from "@/components/claros/LearnerSession";
import { buttonVariants } from "@/components/ui/button";
import { createSession, listRequests, listWorkflows } from "@/lib/api";
import { EXPERT, LEA } from "@/lib/mock";
import { readLastSession, type LastSession } from "@/lib/lastSession";
import { cn } from "@/lib/utils";

export default function HomePage() {
  return (
    <Shell>
      <Home />
    </Shell>
  );
}

function Home() {
  const { role } = useUi();
  return role === "learner" ? <LearnerSession below={<LearnerStatus />} /> : <ExpertHome />;
}

/* =====================================================================
   EXPERT HOME — one big action, then who's waiting, then the maps
   ===================================================================== */

function ExpertHome() {
  return (
    <div className="space-y-16 sm:space-y-20">
      <div className="grid items-start gap-12 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] xl:gap-14">
        <TeachHero />
        <Waiting />
      </div>
      <ExpertMaps />
    </div>
  );
}

function TeachHero() {
  const { t, lang } = useUi();
  const router = useRouter();
  const [starting, setStarting] = useState(false);
  const teach = async () => {
    setStarting(true);
    const r = await createSession({ mode: "capture", user: EXPERT, lang });
    router.push(`/capture/${r.data.session_id}`);
  };
  return (
    <section aria-label={t("eh.title")} className="min-w-0">
      <button
        type="button"
        onClick={teach}
        disabled={starting}
        aria-busy={starting || undefined}
        className="press press-xl group flex w-full flex-col items-start gap-8 rounded-[14px] border-[3px] border-ink bg-claros p-7 text-left text-claros-ink shadow-[8px_8px_0_0_var(--ink)] focus-visible:outline-3 focus-visible:outline-offset-4 focus-visible:outline-claros sm:p-10 2xl:p-12"
      >
        <span className="flex w-full items-center justify-between gap-6">
          <ClarosDot size={72} speaking={!starting} className="border-[3px]" />
          {starting ? <Loader2 className="size-10 animate-spin motion-reduce:animate-none" aria-hidden /> : <ArrowRight className="size-12" aria-hidden />}
        </span>
        <span className="block max-w-[14ch] text-5xl font-black leading-[0.96] tracking-[-0.045em] sm:text-6xl 2xl:text-7xl">{t("eh.title")}</span>
        <span className="block max-w-[40ch] text-xl font-semibold opacity-90 sm:text-2xl">{t("eh.sub")}</span>
      </button>
    </section>
  );
}

function Waiting() {
  const { t } = useUi();
  const reqs = useResource(listRequests, []);
  const list = [...(reqs.data ?? [])].filter((r) => !isJunkRequest(r) && r.status !== "done").sort((a, b) => Number(b.status === "open") - Number(a.status === "open") || b.created_at - a.created_at);
  const open = list.filter((r) => r.status === "open").length;
  return (
    <section aria-labelledby="waiting" className="min-w-0">
      <SectionHeader id="waiting" title={t("eh.waiting")} count={open} aside={<SourceNote source={reqs.source} />} />
      {reqs.loading ? (
        <Loading rows={2} />
      ) : reqs.error ? (
        <ErrorState message={reqs.error} onRetry={reqs.retry} />
      ) : !list.length ? (
        <EmptyState icon={<Inbox aria-hidden />}>{t("eh.waiting.empty")}</EmptyState>
      ) : (
        <div className="space-y-5">
          {list.slice(0, 4).map((r) => (
            <RequestCard key={r.id} r={r} />
          ))}
        </div>
      )}
    </section>
  );
}

function ExpertMaps() {
  const { t } = useUi();
  const wf = useResource(listWorkflows, []);
  const all = (wf.data ?? []).filter((w) => !isJunkWorkflow(w));
  const mine = all.filter((w) => w.experts.some((e) => e.id === EXPERT.id));
  const list = (mine.length ? mine : all).sort((a, b) => b.updated_at - a.updated_at);
  const maps = useMaps(list);
  return (
    <section aria-labelledby="maps">
      <SectionHeader
        id="maps"
        title={t("eh.maps")}
        aside={
          all.length > list.length ? (
            <Link href="/map" className={buttonVariants({ variant: "link" })}>
              {t("nav.maps")} <ArrowRight aria-hidden />
            </Link>
          ) : null
        }
      />
      {wf.loading ? (
        <Loading rows={1} />
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
    </section>
  );
}

/* =====================================================================
   LEARNER HOME — the Start hero (LearnerSession) + last session + requests
   ===================================================================== */

function LearnerStatus() {
  const { t } = useUi();
  const [last, setLast] = useState<LastSession | null>(null);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setLast(readLastSession()), []);
  const reqs = useResource(listRequests, []);
  const mine = (reqs.data ?? []).filter((r) => r.requested_by.id === LEA.id && !isJunkRequest(r)).sort((a, b) => b.created_at - a.created_at);
  if (!last && !mine.length) return null;
  return (
    <div className={cn("mt-20 grid items-start gap-12", last && mine.length ? "xl:grid-cols-2" : "")}>
      {last ? (
        <section aria-labelledby="last" className="min-w-0">
          <SectionHeader id="last" title={t("lh.last")} />
          <div className="rounded-base border-2 border-ink bg-card p-6 shadow-hard">
            {last.name ? <p className="text-2xl font-extrabold leading-snug tracking-[-0.02em]">{last.name}</p> : null}
            <div className="mt-5 grid gap-6 sm:grid-cols-2">
              <div>
                <p className="flex items-baseline gap-3 text-lg font-bold">
                  <span className="tnum text-5xl font-black leading-none">{last.own.length}</span> {t("lh.last.own")}
                </p>
              </div>
              <div>
                <p className="flex items-baseline gap-3 text-lg font-bold">
                  <span className="tnum text-5xl font-black leading-none text-claros">{last.practice.length}</span> {t("lh.last.practice")}
                </p>
                <ul className="mt-3 space-y-1.5">
                  {last.practice.slice(0, 3).map((x) => (
                    <li key={x} className="flex items-start gap-2 text-lg font-semibold leading-snug">
                      <ArrowRight className="mt-1 size-5 shrink-0" aria-hidden /> {x}
                    </li>
                  ))}
                  {!last.practice.length && last.own.length ? (
                    <li className="flex items-center gap-2 text-lg font-semibold">
                      <Check className="size-5" aria-hidden />
                    </li>
                  ) : null}
                </ul>
              </div>
            </div>
          </div>
        </section>
      ) : null}
      {mine.length ? (
        <section aria-labelledby="myreq" className="min-w-0">
          <SectionHeader id="myreq" title={t("lh.requests")} count={mine.length} />
          <ul className="space-y-3">
            {mine.slice(0, 4).map((r) => (
              <RequestStatus key={r.id} r={r} />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
