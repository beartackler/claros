"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  AppWindow,
  BookOpen,
  CheckCircle2,
  CircleDashed,
  CircleHelp,
  Pause,
  Play,
  RotateCw,
  Sparkles,
} from "lucide-react";
import type { Coverage, OnetMatch, Quote, User } from "@/lib/contracts";
import { clipUrl, keyframeUrl, type Result, type Source } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Empty, EmptyContent, EmptyDescription, EmptyMedia } from "@/components/ui/empty";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useT, useUi, type DictKey } from "./i18n";

/* ---------------- data hook ---------------- */

export function useResource<T>(load: () => Promise<Result<T>>, deps: unknown[]) {
  const [state, setState] = useState<{ data?: T; source?: Source; loading: boolean; error?: string }>({ loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState((s) => ({ ...s, loading: true, error: undefined }));
    load()
      .then((r) => alive && setState({ data: r.data, source: r.source, loading: false }))
      .catch((e: unknown) => alive && setState({ loading: false, error: e instanceof Error ? e.message : String(e) }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);
  const retry = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, retry, setData: (d: T) => setState((s) => ({ ...s, data: d })) };
}

export function useMediaQuery(q: string) {
  const [m, setM] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(q);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setM(mq.matches);
    const on = () => setM(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return m;
}

/* ---------------- panels + headers ---------------- */

export function Panel({
  className,
  tone = "card",
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { tone?: "card" | "paper" | "claros" | "ink" | "expert" }) {
  const tones = {
    card: "bg-card",
    paper: "bg-paper-2",
    claros: "bg-claros text-claros-ink",
    ink: "bg-ink text-paper",
    expert: "bg-expert-soft",
  } as const;
  return (
    <div className={cn("rounded-base border-2 border-ink shadow-hard", tones[tone], className)} {...rest}>
      {children}
    </div>
  );
}

/** Section heading: the heading speaks for itself; count + aside sit on the same line. */
export function SectionHeader({
  id,
  title,
  count,
  sub,
  aside,
  level = 2,
  className,
}: {
  id?: string;
  title: React.ReactNode;
  count?: number;
  sub?: React.ReactNode;
  aside?: React.ReactNode;
  level?: 2 | 3;
  className?: string;
}) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className={cn("mb-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-2", className)}>
      <div className="min-w-0">
        <H id={id} className={cn("flex items-center gap-2.5 font-extrabold tracking-[-0.02em]", level === 2 ? "text-xl sm:text-2xl" : "text-lg")}>
          {title}
          {count != null && count > 0 ? (
            <span className="tnum inline-grid h-6 min-w-6 place-items-center rounded-full border-2 border-ink bg-ink px-1.5 text-xs font-black text-paper">{count}</span>
          ) : null}
        </H>
        {sub ? <p className="mt-1 max-w-[68ch] text-sm leading-relaxed text-ink-2">{sub}</p> : null}
      </div>
      {aside}
    </div>
  );
}

/* ---------------- coverage ---------------- */

const COV = {
  ready: { variant: "ready" as const, Icon: CheckCircle2, key: "cov.ready" as DictKey, long: "cov.ready.long" as DictKey },
  partial: { variant: "partial" as const, Icon: CircleDashed, key: "cov.partial" as DictKey, long: "cov.partial.long" as DictKey },
  missing: { variant: "missing" as const, Icon: CircleHelp, key: "cov.missing" as DictKey, long: "cov.missing.long" as DictKey },
};

export function CoverageChip({ status, long, className }: { status: Coverage["status"]; long?: boolean; className?: string }) {
  const t = useT();
  const c = COV[status] ?? COV.missing;
  return (
    <Badge variant={c.variant} className={className} title={long ? undefined : t(c.long)}>
      <c.Icon aria-hidden />
      {long ? t(c.long) : t(c.key)}
    </Badge>
  );
}

export function AppChip({ name }: { name: string }) {
  return (
    <Badge variant="tag">
      <AppWindow aria-hidden />
      {name}
    </Badge>
  );
}

export function OnetTag({ onet, full }: { onet: Pick<OnetMatch, "occupation_code" | "occupation_title" | "task">; full?: boolean }) {
  const t = useT();
  const label = (
    <Badge variant="tag" className={full ? "max-w-full whitespace-normal text-left" : undefined}>
      <BookOpen aria-hidden />
      <span>O*NET {onet.occupation_code}</span>
      {full && onet.task ? <span className="font-sans font-semibold">· {onet.task}</span> : null}
    </Badge>
  );
  if (full) return label;
  return (
    <Tooltip>
      <TooltipTrigger render={<span tabIndex={0} className="inline-flex rounded-[4px]" />}>{label}</TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p className="font-bold">{onet.occupation_title}</p>
        {onet.task ? <p className="mt-1 text-xs">{t("map.task")}: {onet.task}</p> : null}
      </TooltipContent>
    </Tooltip>
  );
}

export function Meter({ value, label, tone = "ink" }: { value: number; label: string; tone?: "ink" | "claros" | "ready" }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const fill = { ink: "bg-ink", claros: "bg-claros", ready: "bg-ready" }[tone];
  return (
    <div>
      <div className="mb-1 flex justify-between gap-2 text-xs font-semibold">
        <span>{label}</span>
        <span className="tnum font-mono">{pct}%</span>
      </div>
      <div className="h-3 overflow-hidden rounded-[3px] border-2 border-ink bg-card" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className={cn("h-full transition-[width] duration-700 ease-out", fill)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ---------------- people ---------------- */

const AVATAR_TONES = ["bg-expert", "bg-partial", "bg-ready", "bg-[#ffb4e1]"];
export function initials(name: string) {
  return name
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
export function ExpertAvatar({ user, size = 28, index = 0, className }: { user: Pick<User, "name" | "id">; size?: number; index?: number; className?: string }) {
  return (
    <span
      title={user.name}
      aria-label={user.name}
      role="img"
      className={cn("inline-grid shrink-0 place-items-center rounded-full border-2 border-ink font-extrabold text-on-fill", AVATAR_TONES[index % AVATAR_TONES.length], className)}
      style={{ width: size, height: size, fontSize: Math.max(9, size * 0.38) }}
    >
      {initials(user.name)}
    </span>
  );
}
export function AvatarStack({ users, size = 28, max = 4 }: { users: Pick<User, "name" | "id">[]; size?: number; max?: number }) {
  const shown = users.slice(0, max);
  return (
    <span className="inline-flex -space-x-2">
      {shown.map((u, i) => (
        <ExpertAvatar key={u.id} user={u} size={size} index={i} />
      ))}
      {users.length > max ? (
        <span className="tnum inline-grid place-items-center rounded-full border-2 border-ink bg-card text-[10px] font-black" style={{ width: size, height: size }}>
          +{users.length - max}
        </span>
      ) : null}
    </span>
  );
}

/* ---------------- screen moments ---------------- */

type Highlight = { label: string; from?: string | null; to?: string | null } | null;

/** Keyframe from the server; falls back to a neutral synthetic app window so the story still reads offline. */
export function ScreenThumb({
  keyframeId,
  title = "",
  highlight,
  className,
  seed = 0,
  alt,
  rounded = true,
}: {
  keyframeId?: string | null;
  title?: string;
  highlight?: Highlight;
  className?: string;
  seed?: number;
  alt?: string;
  rounded?: boolean;
}) {
  const [failed, setFailed] = useState(!keyframeId);
  const [prev, setPrev] = useState(keyframeId);
  if (prev !== keyframeId) {
    setPrev(keyframeId);
    setFailed(!keyframeId);
  }
  const s = (seed + (keyframeId ? [...keyframeId].reduce((a, c) => a + c.charCodeAt(0), 0) : 0)) % 4;
  return (
    <div className={cn("relative aspect-[16/10] overflow-hidden border-2 border-ink bg-[#f7f7f5]", rounded && "rounded-[4px]", className)}>
      {!failed && keyframeId ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={keyframeUrl(keyframeId)} alt={alt ?? title} loading="lazy" className="h-full w-full object-cover object-top" onError={() => setFailed(true)} />
      ) : (
        <svg viewBox="0 0 320 200" className="h-full w-full" role="img" aria-label={alt ?? title}>
          <rect width="320" height="200" fill="#f7f7f5" />
          <rect width="320" height="18" fill="#1f2937" />
          <circle cx="10" cy="9" r="3" fill="#ff8a80" />
          <circle cx="20" cy="9" r="3" fill="#ffc94a" />
          <circle cx="30" cy="9" r="3" fill="#6ee7a0" />
          <rect x="0" y="18" width="54" height="182" fill="#eceae4" />
          {[0, 1, 2, 3, 4].map((i) => (
            <rect key={i} x="8" y={30 + i * 16} width={30 + ((i + s) % 3) * 6} height="6" rx="2" fill={i === s % 5 ? "#6d28d9" : "#c9c5bb"} />
          ))}
          {title ? (
            <text x="66" y="38" fontSize="11" fontWeight="700" fill="#111" fontFamily="system-ui, sans-serif">
              {title.slice(0, 34)}
            </text>
          ) : (
            <rect x="66" y="30" width="110" height="9" rx="2" fill="#a8a49b" />
          )}
          <rect x="250" y="28" width="58" height="14" rx="3" fill="#111" />
          {[0, 1, 2, 3].map((i) => (
            <g key={i}>
              <rect x={66 + (i % 2) * 124} y={58 + Math.floor(i / 2) * 26} width="40" height="5" rx="2" fill="#a8a49b" />
              <rect x={66 + (i % 2) * 124} y={66 + Math.floor(i / 2) * 26} width="112" height="11" rx="2" fill="#fff" stroke="#d6d2c8" />
            </g>
          ))}
          <rect x="66" y="118" width="236" height="70" rx="2" fill="#fff" stroke="#d6d2c8" />
          {[0, 1, 2].map((i) => (
            <rect key={i} x="72" y={128 + i * 17} width={120 + ((i + s) % 2) * 40} height="5" rx="2" fill="#cfcbc1" />
          ))}
          {highlight && (
            <g>
              <rect x="186" y="88" width="120" height="24" rx="3" fill="#ede4ff" stroke="#6d28d9" strokeWidth="2.5" />
              <text x="192" y="98" fontSize="7" fill="#3b1a85" fontFamily="system-ui, sans-serif">
                {highlight.label.slice(0, 22)}
              </text>
              <text x="192" y="108" fontSize="8.5" fontWeight="700" fill="#111" fontFamily="ui-monospace, monospace">
                {highlight.from ? `${highlight.from.slice(0, 10)} → ` : ""}
                {(highlight.to ?? "").slice(0, 12)}
              </text>
            </g>
          )}
        </svg>
      )}
    </div>
  );
}

/** Flip through several keyframes ("what the expert saw"). Respects reduced motion (manual only). */
export function Flipbook({ ids, title, highlight, autoPlay = true }: { ids: string[]; title?: string; highlight?: Highlight; autoPlay?: boolean }) {
  const t = useT();
  const frames = ids.length ? ids : [""];
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(autoPlay && frames.length > 1);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!playing || reduce || frames.length < 2) return;
    const h = setInterval(() => setI((x) => (x + 1) % frames.length), 1100);
    return () => clearInterval(h);
  }, [playing, frames.length]);
  return (
    <div>
      <ScreenThumb keyframeId={frames[i] || null} seed={i} title={title} highlight={i === frames.length - 1 ? highlight : null} />
      {frames.length > 1 ? (
        <div className="mt-2 flex items-center gap-2">
          <Button variant="outline" size="icon-xs" onClick={() => setPlaying((p) => !p)} aria-label={playing ? t("quote.pause") : t("quote.play")}>
            {playing ? <Pause /> : <Play />}
          </Button>
          <div className="flex flex-1 gap-1">
            {frames.map((_, k) => (
              <button
                key={k}
                type="button"
                aria-pressed={k === i}
                aria-label={`${k + 1} / ${frames.length}`}
                onClick={() => {
                  setPlaying(false);
                  setI(k);
                }}
                className={cn("h-3 flex-1 rounded-[2px] border-2 border-ink transition-colors", k === i ? "bg-ink" : "bg-card hover:bg-paper-2")}
              />
            ))}
          </div>
          <span className="tnum font-mono text-[11px] text-ink-2">
            {i + 1}/{frames.length}
          </span>
        </div>
      ) : null}
    </div>
  );
}

/* ---------------- quotes ---------------- */

export function QuoteBlock({ quote, compact }: { quote: Quote; compact?: boolean }) {
  const t = useT();
  const { lang } = useUi();
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const translation = quote.lang !== lang ? quote.translations?.[lang] ?? quote.translations?.en : null;
  const toggle = () => {
    if (!quote.audio_clip) return;
    if (!audioRef.current) {
      audioRef.current = new Audio(clipUrl(quote.audio_clip));
      audioRef.current.onended = () => setPlaying(false);
      audioRef.current.onerror = () => setPlaying(false);
    }
    if (playing) {
      audioRef.current.pause();
      setPlaying(false);
    } else {
      audioRef.current.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
    }
  };
  const srcKey = (["live", "debrief", "doc", "inbox"].includes(quote.source) ? `quote.src.${quote.source}` : "quote.src.live") as DictKey;
  return (
    <figure className={cn("rounded-[4px] border-2 border-ink bg-expert-soft", compact ? "p-2.5" : "p-3.5")}>
      <div className="flex items-start gap-2.5">
        {quote.audio_clip ? (
          <Button variant="outline" size="icon-xs" onClick={toggle} aria-label={playing ? t("quote.pause") : t("quote.play")} className="mt-0.5 rounded-full bg-expert text-on-fill">
            {playing ? <Pause /> : <Play />}
          </Button>
        ) : null}
        <blockquote className="min-w-0 flex-1">
          <p lang={quote.lang} className={cn("font-semibold leading-snug", compact ? "text-sm" : "text-[15px]")}>
            “{quote.text}”
          </p>
          {translation ? (
            <p lang={lang} className="mt-1.5 text-sm leading-snug text-ink-2">
              <span className="mr-1.5 rounded-[3px] border border-ink/40 px-1 font-mono text-[10px] uppercase">
                {quote.lang}→{lang}
              </span>
              {translation}
            </p>
          ) : null}
        </blockquote>
      </div>
      <figcaption className="mt-2 flex items-center gap-1.5 text-xs font-semibold">
        <ExpertAvatar user={{ id: quote.speaker_id, name: quote.speaker }} size={20} />
        {quote.speaker}
        <span className="text-ink-2">· {t(srcKey)}</span>
      </figcaption>
    </figure>
  );
}

/* ---------------- states ---------------- */

export function Loading({ label, rows = 3, className }: { label?: string; rows?: number; className?: string }) {
  const t = useT();
  return (
    <div role="status" aria-live="polite" className={cn("space-y-3", className)}>
      <span className="sr-only">{label ?? t("state.loading")}</span>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-16 border-ink/30 bg-paper-2" />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  const t = useT();
  return (
    <div role="alert" className="flex flex-wrap items-start gap-3 rounded-base border-2 border-ink bg-card p-4 shadow-hard">
      <span className="grid size-9 shrink-0 place-items-center rounded-[4px] border-2 border-ink bg-missing text-on-fill">
        <AlertTriangle className="size-5" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-bold">{t("state.error.title")}</p>
        <p className="text-sm text-ink-2">{t("state.error.sub")}</p>
        {message ? <p className="mt-1 break-all font-mono text-xs text-ink-2">{message}</p> : null}
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          <RotateCw aria-hidden /> {t("state.error.retry")}
        </Button>
      ) : null}
    </div>
  );
}

export function EmptyState({ children, icon, action, className }: { children: React.ReactNode; icon?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <Empty className={cn("hatch items-start border-ink bg-transparent p-5 text-left md:p-6", className)}>
      <EmptyMedia variant="icon" className="mb-0 border-ink bg-card">
        {icon ?? <Sparkles aria-hidden />}
      </EmptyMedia>
      <EmptyDescription className="max-w-[56ch] text-sm font-medium text-ink">{children}</EmptyDescription>
      {action ? <EmptyContent className="items-start">{action}</EmptyContent> : null}
    </Empty>
  );
}

/* ---------------- Claros voice marker ---------------- */

export function ClarosSays({ children, className, speaking }: { children: React.ReactNode; className?: string; speaking?: boolean }) {
  return (
    <div className={cn("flex items-start gap-3", className)}>
      <ClarosDot speaking={speaking} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function ClarosDot({ speaking, size = 28, className }: { speaking?: boolean; size?: number; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block shrink-0 rounded-full border-2 border-ink bg-claros", speaking && "orb-speaking", className)}
      style={{
        width: size,
        height: size,
        backgroundImage: "radial-gradient(circle at 32% 30%, rgba(255,255,255,.85) 0 14%, transparent 15%)",
      }}
    />
  );
}

export function SourceNote({ source }: { source?: Source }) {
  const t = useT();
  if (source !== "mock") return null;
  return (
    <Badge variant="dashed" title={t("data.demo.hint")} className="font-mono text-[11px]">
      {t("data.demo")}
    </Badge>
  );
}
