"use client";

/**
 * "How Claros decided" — judge/demo evidence, off by default (user menu toggle or ?judge=1).
 * Live signals (typing · speaking · screen · gate), the `why` behind each question, and
 * what Claros looked up itself instead of asking the expert.
 */
import { BookOpen, Ear, Keyboard, MonitorCheck, MonitorDot, MonitorOff, Timer, TrafficCone } from "lucide-react";
import type { LookedUpMsg, ServerMsg, SignalsMsg, Why } from "@/lib/contracts";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { useUi, type DictKey } from "./i18n";
import { useLiveStore } from "./live";

export const useEvidence = () => useUi().evidence;

/** Latest `signals` from the server; falls back to what this tab knows (capture activity, VAD, agent mode). */
function useSignals(mock?: Partial<SignalsMsg>): SignalsMsg {
  const log = useLiveStore((s) => s.serverLog);
  const activity = useLiveStore((s) => s.activity);
  const userSpeaking = useLiveStore((s) => s.userSpeaking);
  const agentMode = useLiveStore((s) => s.agentMode);
  const last = [...log].reverse().find((m): m is SignalsMsg => m.type === "signals");
  if (last) return last;
  return {
    type: "signals",
    typing: mock?.typing ?? activity === "typing",
    speaking: mock?.speaking ?? userSpeaking,
    screen: mock?.screen ?? (activity === "away" ? "away" : activity === "typing" || activity === "scrolling" || activity === "navigating" ? "changing" : "settled"),
    gate: mock?.gate ?? (agentMode === "speaking" ? "asking" : activity === "idle" ? "ready" : "quiet"),
  };
}

export function SignalsBar({ mock, className }: { mock?: Partial<SignalsMsg>; className?: string }) {
  const { t, evidence } = useUi();
  const s = useSignals(mock);
  if (!evidence) return null;
  const ScreenIcon = s.screen === "settled" ? MonitorCheck : s.screen === "away" ? MonitorOff : MonitorDot;
  const items: { on: boolean; icon: typeof Ear; label: string; tone?: string }[] = [
    { on: s.typing, icon: Keyboard, label: t("ev.typing") },
    { on: s.speaking, icon: Ear, label: t("ev.speaking") },
    { on: s.screen === "settled", icon: ScreenIcon, label: t(`ev.screen.${s.screen}` as DictKey) },
    {
      on: s.gate !== "quiet",
      icon: TrafficCone,
      label: t(`ev.gate.${s.gate}` as DictKey),
      tone: s.gate === "asking" ? "bg-claros text-claros-ink" : s.gate === "ready" ? "bg-ready text-on-fill" : undefined,
    },
  ];
  return (
    <div role="status" aria-label={t("ev.title")} className={cn("flex flex-wrap items-center gap-2", className)}>
      <span className="mr-1 text-base font-extrabold">{t("ev.title")}</span>
      {items.map((it, i) => (
        <span
          key={i}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-base border-2 border-ink px-2.5 py-1 text-base font-bold transition-colors",
            it.on ? it.tone ?? "bg-ink text-paper" : "border-dashed bg-card text-ink-2",
          )}
        >
          <it.icon className="size-4" aria-hidden />
          {it.label}
        </span>
      ))}
    </div>
  );
}

/** Small tag under a question: when Claros chose to ask, and what it noticed. */
export function WhyTag({ why, className }: { why?: Why | null; className?: string }) {
  const { evidence, t } = useUi();
  if (!evidence || !why) return null;
  return (
    <p className={cn("flex flex-wrap items-center gap-2 text-base font-semibold", className)}>
      <Badge variant="claros" className="text-sm">
        <Timer aria-hidden /> {why.when}
      </Badge>
      <span className="text-ink-2">{why.what}</span>
      {why.scope ? <Badge variant="expert">{t(`scope.${why.scope}` as DictKey)}</Badge> : null}
    </p>
  );
}

/** The `why` of the latest thing Claros asked (say/ask). */
export function useLatestWhy(): Why | null {
  const log = useLiveStore((s) => s.serverLog);
  const m = [...log].reverse().find((x: ServerMsg) => (x.type === "say" || x.type === "ask") && "why" in x && x.why);
  return m && (m.type === "say" || m.type === "ask") ? m.why ?? null : null;
}

export function useLookedUp(mock: LookedUpMsg[] = []): LookedUpMsg[] {
  const log = useLiveStore((s) => s.serverLog);
  const live = log.filter((m): m is LookedUpMsg => m.type === "looked_up");
  return live.length ? live : mock;
}

/** "Looked it up instead of asking you · source" */
export function LookedUpList({ items, className }: { items: LookedUpMsg[]; className?: string }) {
  // always visible: it is the point of "ask less" (signals / why stay judge-only)
  const { t } = useUi();
  if (!items.length) return null;
  return (
    <section aria-labelledby="looked-up" className={className}>
      <h2 id="looked-up" className="flex items-center gap-2 text-base font-extrabold text-ink-2">
        <BookOpen className="size-5" aria-hidden /> {t("ev.lookedUp")}
      </h2>
      <ul className="mt-3 grid gap-3 lg:grid-cols-2">
        {items.slice(-4).reverse().map((m, i) => (
          <li key={i} className="rounded-base border-2 border-dashed border-ink bg-card p-4">
            <p className="text-lg font-bold leading-snug">{m.unknown_summary}</p>
            <p className="mt-1 text-base">{m.answer}</p>
            <p className="mt-2 text-sm font-semibold text-ink-2">
              {t(`ev.src.${m.source.kind}` as DictKey)} ·{" "}
              {m.source.url ? (
                <a href={m.source.url} target="_blank" rel="noreferrer" className="underline decoration-2 underline-offset-2 hover:text-ink">
                  {m.source.title}
                </a>
              ) : (
                m.source.title
              )}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
