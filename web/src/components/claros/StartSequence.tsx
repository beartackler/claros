"use client";

/**
 * ONE primary "Start" → a guided, visible sequence:
 *   1 Share your app window  →  2 Allow microphone  →  3 Claros greets you (voice)
 * Each step shows waiting / in progress / done / blocked-with-the-fix. Voice is the product: no typed path.
 */
import { useEffect, useState } from "react";
import { AppWindow, Check, CloudOff, EyeOff, Loader2, Mic, RotateCw, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useUi, type DictKey } from "./i18n";
import { ClarosDot } from "./primitives";
import { useServerStatus } from "./live";
import { useClaros } from "@/voice/store";

type Key = "share" | "mic" | "greet";
type S = "idle" | "active" | "done" | "denied" | "warn";
/** ok · monitor/tab = wrong surface · cancelled = closed the picker · system = OS blocks screen recording (macOS) */
export type ShareResult = "ok" | "monitor" | "tab" | "cancelled" | "system";
export type MicResult = boolean | "missing";

export function StartSequence({
  title,
  sub,
  persona,
  onShare,
  onStopShare,
  onMic,
  onVoice,
  onDone,
  onSkipVoice,
  aside,
  privacy,
  className,
}: {
  title: React.ReactNode;
  sub?: React.ReactNode;
  persona: "expert" | "learner";
  onShare: () => Promise<ShareResult>;
  /** stop a wrong share so the picker can open again */
  onStopShare?: () => void;
  onMic: () => Promise<MicResult>;
  onVoice: () => Promise<boolean>;
  onDone: () => void;
  /** expert fallback when voice can't connect: keep going silently */
  onSkipVoice?: () => void;
  aside?: React.ReactNode;
  privacy: DictKey;
  className?: string;
}) {
  const { t } = useUi();
  const server = useServerStatus();
  const [st, setSt] = useState<Record<Key, S>>({ share: "idle", mic: "idle", greet: "idle" });
  // what went wrong, per step → [message, fix]
  const [why, setWhy] = useState<Partial<Record<Key, [DictKey, DictKey | null]>>>({});
  const set = (k: Key, v: S, w?: [DictKey, DictKey | null]) => {
    setSt((s) => ({ ...s, [k]: v }));
    setWhy((x) => ({ ...x, [k]: w }));
  };
  // the voice can connect after the wait gave up (slow network, a transient error): then just carry on
  const [slow, setSlow] = useState(false);
  const greetFailed = st.greet === "denied";
  useEffect(() => {
    if (!greetFailed) return;
    return useClaros.subscribe((c) => {
      if (c.voiceStatus !== "connected") return;
      setSt((s) => ({ ...s, greet: "done" }));
      setWhy((x) => ({ ...x, greet: undefined }));
      onDone();
    });
  }, [greetFailed]); // eslint-disable-line react-hooks/exhaustive-deps
  const started = Object.values(st).some((v) => v !== "idle");
  const busy = Object.values(st).some((v) => v === "active");

  // must be called straight from the click: the window picker needs the user gesture
  const run = async (from: Key) => {
    if (from === "share") {
      set("share", "active");
      const r = await onShare();
      if (r === "cancelled") return set("share", "denied", ["start.share.denied", "start.share.fix"]);
      if (r === "system") return set("share", "denied", ["start.share.system", "start.share.system.fix"]);
      if (r === "monitor" || r === "tab") return set("share", "warn", [r === "monitor" ? "start.share.monitor" : "start.share.tab", null]);
      set("share", "done");
    }
    await fromMic();
  };
  const fromMic = async (onlyVoice = false) => {
    if (!onlyVoice) {
      set("mic", "active");
      const m = await onMic();
      if (m === "missing") return set("mic", "denied", ["start.mic.missing", "start.mic.missing.fix"]);
      if (!m) return set("mic", "denied", ["start.mic.denied", "start.mic.fix"]);
      set("mic", "done");
    }
    set("greet", "active");
    setSlow(false);
    const slowTimer = setTimeout(() => setSlow(true), 8000);
    const ok = await onVoice();
    clearTimeout(slowTimer);
    setSlow(false);
    if (!ok) return set("greet", "denied", ["start.voice.denied", "start.voice.fix"]);
    set("greet", "done");
    onDone();
  };
  const retry = (k: Key) => (k === "share" ? run("share") : fromMic(k === "greet"));

  const steps: { k: Key; icon: React.ComponentType<{ className?: string }>; label: DictKey; active: DictKey }[] = [
    { k: "share", icon: AppWindow, label: "start.step.share", active: "start.state.share.active" },
    { k: "mic", icon: Mic, label: "start.step.mic", active: "start.state.mic.active" },
    { k: "greet", icon: ClarosIcon, label: persona === "learner" ? "start.step.greet.learner" : "start.step.greet", active: "start.state.greet.active" },
  ];

  return (
    <section className={cn("grid items-start gap-10 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] xl:gap-16", className)}>
      <div className="min-w-0">
        <h1 className="max-w-[16ch] text-5xl font-black leading-[0.98] tracking-[-0.045em] sm:text-6xl 2xl:text-7xl">{title}</h1>
        {sub ? <p className="mt-5 max-w-[44ch] text-xl text-ink-2 sm:text-2xl">{sub}</p> : null}
        <div className="mt-5 flex max-w-[60ch] gap-2.5 text-base text-ink-2">
          <EyeOff className="mt-0.5 size-5 shrink-0" aria-hidden />
          <p>
            <span className="font-bold text-ink">{t(privacy)}</span> {t("start.privacy.more")}
          </p>
        </div>

        <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-4">
          <Button
            variant="claros"
            onClick={() => run("share")}
            disabled={busy || st.greet === "done"}
            className="press-lg h-20 min-w-[16rem] rounded-[10px] border-[3px] px-10 text-3xl font-black tracking-[-0.02em] shadow-[6px_6px_0_0_var(--ink)] [&_svg]:size-8"
          >
            {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <ClarosDot size={34} />}
            {t("start.cta")}
          </Button>
          {aside}
        </div>
        {server !== "up" && server !== "checking" ? (
          <p role="status" className={cn("mt-4 inline-flex items-center gap-2.5 rounded-base border-2 border-ink px-3 py-2 text-base font-bold", server === "waking" ? "bg-partial/40" : "bg-paper-2")}>
            {server === "waking" ? <Loader2 className="size-5 animate-spin motion-reduce:animate-none" aria-hidden /> : <CloudOff className="size-5" aria-hidden />}
            {server === "waking" ? t("start.server.waking") : t("start.server.down")}
          </p>
        ) : null}

      </div>

      <ol className="grid gap-3" aria-label={t("start.cta")}>
        {steps.map((s, i) => {
          const v = st[s.k];
          const Icon = s.icon;
          return (
            <li
              key={s.k}
              aria-current={v === "active" ? "step" : undefined}
              className={cn(
                "rounded-base border-2 border-ink p-4 transition-[background-color,opacity] duration-300 sm:p-5",
                v === "done" && "bg-ready/40",
                v === "active" && "bg-card shadow-[6px_6px_0_0_var(--claros)]",
                v === "denied" && "bg-missing/35 shadow-hard",
                v === "warn" && "bg-partial/40 shadow-hard",
                v === "idle" && (started ? "border-dashed bg-transparent opacity-60" : "bg-card"),
              )}
            >
              <div className="flex items-center gap-4">
                <span
                  className={cn(
                    "tnum grid size-12 shrink-0 place-items-center rounded-full border-2 border-ink text-xl font-black",
                    v === "done" ? "bg-ready text-on-fill" : v === "active" ? "bg-claros text-claros-ink" : v === "denied" ? "bg-missing text-on-fill" : v === "warn" ? "bg-partial text-on-fill" : "bg-paper-2",
                  )}
                >
                  {v === "done" ? <Check className="size-6" aria-hidden /> : v === "active" ? <Loader2 className="size-6 animate-spin motion-reduce:animate-none" aria-hidden /> : v === "denied" || v === "warn" ? <TriangleAlert className="size-6" aria-hidden /> : i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 text-xl font-extrabold leading-snug tracking-[-0.01em]">
                    <Icon className="size-5 shrink-0" aria-hidden /> {t(s.label)}
                  </p>
                  <p className="mt-0.5 text-base font-semibold text-ink-2" aria-live="polite">
                    {v === "active" ? t(s.k === "greet" && slow ? "start.state.greet.slow" : s.active) : v === "done" ? t("start.state.done") : (v === "denied" || v === "warn") && why[s.k] ? t(why[s.k]![0]) : null}
                  </p>
                </div>
              </div>
              {v === "warn" ? (
                <div className="mt-3 flex flex-wrap items-center gap-3 pl-16">
                  {/* one tab is fine (a web app in one tab); a whole screen is the risky one */}
                  {why[s.k]?.[0] === "start.share.tab" ? (
                    <>
                      <Button variant="primary" size="sm" onClick={() => { set("share", "done"); void fromMic(); }}>
                        {t("start.share.continue")}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => { onStopShare?.(); void run("share"); }}>
                        <AppWindow aria-hidden /> {t("start.share.window")}
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button variant="primary" size="sm" onClick={() => { onStopShare?.(); void run("share"); }}>
                        <AppWindow aria-hidden /> {t("start.share.repick")}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => { set("share", "done"); void fromMic(); }}>
                        {t("start.share.anyway")}
                      </Button>
                    </>
                  )}
                </div>
              ) : null}
              {v === "denied" ? (
                <div className="mt-3 flex flex-wrap items-center gap-3 pl-16">
                  {why[s.k]?.[1] ? <p className="w-full text-base font-semibold">{t(why[s.k]![1]!)}</p> : null}
                  <Button variant="primary" size="sm" onClick={() => void retry(s.k)}>
                    <RotateCw aria-hidden /> {t("start.retry")}
                  </Button>
                  {s.k === "greet" && onSkipVoice ? (
                    <Button variant="ghost" size="sm" onClick={onSkipVoice}>
                      {t("start.skip")}
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function ClarosIcon({ className }: { className?: string }) {
  return <ClarosDot size={20} className={className} />;
}
