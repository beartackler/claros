"use client";

/**
 * ONE primary "Start" → a guided, visible sequence:
 *   1 Share your app window  →  2 Allow microphone  →  3 Claros greets you (voice)
 * Each step shows waiting / in progress / done / blocked-with-the-fix. Typing is a small fallback.
 */
import { useState } from "react";
import { AppWindow, ArrowRight, Check, Keyboard, Loader2, Mic, RotateCw, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useUi, type DictKey } from "./i18n";
import { ClarosDot } from "./primitives";

type Key = "share" | "mic" | "greet";
type S = "idle" | "active" | "done" | "denied";
export type ShareResult = "ok" | "monitor" | "cancelled";

export function StartSequence({
  title,
  sub,
  persona,
  onShare,
  onMic,
  onVoice,
  onDone,
  onType,
  onSkipVoice,
  aside,
  privacy,
  className,
}: {
  title: React.ReactNode;
  sub?: React.ReactNode;
  persona: "expert" | "learner";
  onShare: () => Promise<ShareResult>;
  onMic: () => Promise<boolean>;
  onVoice: () => Promise<boolean>;
  onDone: () => void;
  /** learner fallback: skip voice, type what you're working on */
  onType?: (text: string) => void;
  /** expert fallback when voice can't connect: keep going silently */
  onSkipVoice?: () => void;
  aside?: React.ReactNode;
  privacy: DictKey;
  className?: string;
}) {
  const { t } = useUi();
  const [st, setSt] = useState<Record<Key, S>>({ share: "idle", mic: "idle", greet: "idle" });
  const [monitor, setMonitor] = useState(false);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const set = (k: Key, v: S) => setSt((s) => ({ ...s, [k]: v }));
  const started = Object.values(st).some((v) => v !== "idle");
  const busy = Object.values(st).some((v) => v === "active");

  // must be called straight from the click: the window picker needs the user gesture
  const run = async (from: Key) => {
    if (from === "share") {
      set("share", "active");
      const r = await onShare();
      if (r === "cancelled") return set("share", "denied");
      setMonitor(r === "monitor");
      set("share", "done");
    }
    if (from === "share" || from === "mic") {
      set("mic", "active");
      if (!(await onMic())) return set("mic", "denied");
      set("mic", "done");
    }
    set("greet", "active");
    if (!(await onVoice())) return set("greet", "denied");
    set("greet", "done");
    onDone();
  };

  const steps: { k: Key; icon: React.ComponentType<{ className?: string }>; label: DictKey; active: DictKey; denied: DictKey; fix: DictKey }[] = [
    { k: "share", icon: AppWindow, label: "start.step.share", active: "start.state.share.active", denied: "start.share.denied", fix: "start.share.fix" },
    { k: "mic", icon: Mic, label: "start.step.mic", active: "start.state.mic.active", denied: "start.mic.denied", fix: "start.mic.fix" },
    {
      k: "greet",
      icon: ClarosIcon,
      label: persona === "learner" ? "start.step.greet.learner" : "start.step.greet",
      active: "start.state.greet.active",
      denied: "start.voice.denied",
      fix: "start.voice.fix",
    },
  ];

  return (
    <section className={cn("grid items-start gap-10 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] xl:gap-16", className)}>
      <div className="min-w-0">
        <h1 className="max-w-[16ch] text-5xl font-black leading-[0.98] tracking-[-0.045em] sm:text-6xl 2xl:text-7xl">{title}</h1>
        {sub ? <p className="mt-5 max-w-[44ch] text-xl text-ink-2 sm:text-2xl">{sub}</p> : null}

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
        <p className="mt-5 text-base font-semibold text-ink-2">{t(privacy)}</p>

        {onType ? (
          <div className="mt-8">
            {typing ? (
              <form
                className="flex max-w-xl flex-col gap-3 sm:flex-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (text.trim()) onType(text.trim());
                }}
              >
                <label className="sr-only" htmlFor="type-instead">
                  {t("start.type.ph")}
                </label>
                <input
                  id="type-instead"
                  autoFocus
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={t("start.type.ph")}
                  className="h-12 min-w-0 flex-1 rounded-base border-2 border-ink bg-card px-4 text-lg placeholder:text-ink-2 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros"
                />
                <Button type="submit" variant="secondary" disabled={!text.trim()}>
                  {t("start.type.go")} <ArrowRight aria-hidden />
                </Button>
              </form>
            ) : (
              <Button variant="link" onClick={() => setTyping(true)} className="text-lg text-ink-2">
                <Keyboard aria-hidden /> {t("start.type")}
              </Button>
            )}
          </div>
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
                v === "idle" && (started ? "border-dashed bg-transparent opacity-60" : "bg-card"),
              )}
            >
              <div className="flex items-center gap-4">
                <span
                  className={cn(
                    "tnum grid size-12 shrink-0 place-items-center rounded-full border-2 border-ink text-xl font-black",
                    v === "done" ? "bg-ready text-on-fill" : v === "active" ? "bg-claros text-claros-ink" : v === "denied" ? "bg-missing text-on-fill" : "bg-paper-2",
                  )}
                >
                  {v === "done" ? <Check className="size-6" aria-hidden /> : v === "active" ? <Loader2 className="size-6 animate-spin motion-reduce:animate-none" aria-hidden /> : v === "denied" ? <TriangleAlert className="size-6" aria-hidden /> : i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 text-xl font-extrabold leading-snug tracking-[-0.01em]">
                    <Icon className="size-5 shrink-0" aria-hidden /> {t(s.label)}
                  </p>
                  <p className="mt-0.5 text-base font-semibold text-ink-2" aria-live="polite">
                    {v === "active" ? t(s.active) : v === "done" ? (s.k === "share" && monitor ? t("start.share.monitor") : t("start.state.done")) : v === "denied" ? t(s.denied) : null}
                  </p>
                </div>
              </div>
              {v === "denied" ? (
                <div className="mt-3 flex flex-wrap items-center gap-3 pl-16">
                  <p className="w-full text-base font-semibold">{t(s.fix)}</p>
                  <Button variant="primary" size="sm" onClick={() => run(s.k)}>
                    <RotateCw aria-hidden /> {t("start.retry")}
                  </Button>
                  {s.k === "greet" && onSkipVoice ? (
                    <Button variant="ghost" size="sm" onClick={onSkipVoice}>
                      {t("start.skip")}
                    </Button>
                  ) : null}
                  {s.k === "greet" && onType && !typing ? (
                    <Button variant="ghost" size="sm" onClick={() => setTyping(true)}>
                      <Keyboard aria-hidden /> {t("start.type")}
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
