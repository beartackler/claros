"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  CircleHelp,
  Loader2,
  Pause,
  Play,
  RotateCw,
  Sparkles,
} from "lucide-react";
import type { Coverage, Quote, User } from "@/lib/contracts";
import { clipUrl, keyframeUrl, type Result, type Source } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useT, useUi, type DictKey } from "./i18n";

/* ---------------- data hook ---------------- */

export function useResource<T>(load: () => Promise<Result<T>>, deps: unknown[]) {
  const [state, setState] = useState<{ data?: T; source?: Source; loading: boolean; error?: string }>({ loading: true });
  const [nonce, setNonce] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    let alive = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState((s) => ({ ...s, loading: true, error: undefined }));
    loadRef.current()
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

/* ---------------- panels ---------------- */

export function Panel({
  className,
  tone = "card",
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { tone?: "card" | "paper" | "claros" | "ink" | "expert" }) {
  const tones = {
    card: "bg-[var(--card-bg)]",
    paper: "bg-[var(--paper-2)]",
    claros: "bg-[var(--claros)] text-[var(--claros-ink)]",
    ink: "bg-[var(--ink)] text-[var(--paper)]",
    expert: "bg-[var(--expert-soft)]",
  } as const;
  return (
    <div className={cn("rounded-[6px] border-2 border-[var(--ink)] shadow-[var(--hard)]", tones[tone], className)} {...rest}>
      {children}
    </div>
  );
}

export function SectionTitle({ children, aside, className }: { children: React.ReactNode; aside?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("mb-3 flex items-end justify-between gap-3", className)}>
      <h2 className="text-lg font-extrabold tracking-[-0.02em] text-balance">{children}</h2>
      {aside}
    </div>
  );
}

/* ---------------- coverage ---------------- */

const COV = {
  ready: { bg: "bg-[var(--ready)]", Icon: CheckCircle2, key: "cov.ready" as DictKey, long: "cov.ready.long" as DictKey },
  partial: { bg: "hatch-partial", Icon: CircleDashed, key: "cov.partial" as DictKey, long: "cov.partial.long" as DictKey },
  missing: { bg: "bg-[var(--missing)]", Icon: CircleHelp, key: "cov.missing" as DictKey, long: "cov.missing.long" as DictKey },
};

export function CoverageChip({ status, long, className }: { status: Coverage["status"]; long?: boolean; className?: string }) {
  const t = useT();
  const c = COV[status] ?? COV.missing;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] px-2 py-0.5 text-xs font-bold whitespace-nowrap",
        c.bg,
        className,
      )}
      title={t(c.long)}
    >
      <c.Icon className="size-3.5" aria-hidden />
      {long ? t(c.long) : t(c.key)}
    </span>
  );
}

export function Meter({ value, label, tone = "ink" }: { value: number; label: string; tone?: "ink" | "claros" | "ready" }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const fill = { ink: "bg-[var(--ink)]", claros: "bg-[var(--claros)]", ready: "bg-[var(--ready)]" }[tone];
  return (
    <div>
      <div className="mb-1 flex justify-between gap-2 text-xs font-semibold">
        <span>{label}</span>
        <span className="tnum font-mono">{pct}%</span>
      </div>
      <div className="h-3 rounded-[3px] border-2 border-[var(--ink)] bg-white" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className={cn("h-full transition-[width] duration-700 ease-out", fill)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ---------------- people ---------------- */

const AVATAR_TONES = ["bg-[var(--expert)]", "bg-[var(--partial)]", "bg-[var(--ready)]", "bg-[#ffb4e1]"];
export function initials(name: string) {
  return name
    .split(/\s+/)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
export function ExpertAvatar({ user, size = 28, index = 0 }: { user: Pick<User, "name" | "id">; size?: number; index?: number }) {
  return (
    <span
      title={user.name}
      aria-label={user.name}
      className={cn(
        "inline-grid shrink-0 place-items-center rounded-full border-2 border-[var(--ink)] font-extrabold",
        AVATAR_TONES[index % AVATAR_TONES.length],
      )}
      style={{ width: size, height: size, fontSize: size * 0.38 }}
    >
      {initials(user.name)}
    </span>
  );
}
export function AvatarStack({ users, size = 28 }: { users: Pick<User, "name" | "id">[]; size?: number }) {
  return (
    <span className="inline-flex -space-x-2">
      {users.map((u, i) => (
        <ExpertAvatar key={u.id} user={u} size={size} index={i} />
      ))}
    </span>
  );
}

/* ---------------- screen moments ---------------- */

/** Keyframe from the server; falls back to a synthetic ERP-like screen so the story still reads offline. */
export function ScreenThumb({
  keyframeId,
  title = "Purchase Invoice",
  highlight,
  className,
  seed = 0,
  alt,
}: {
  keyframeId?: string | null;
  title?: string;
  highlight?: { label: string; from?: string | null; to?: string | null } | null;
  className?: string;
  seed?: number;
  alt?: string;
}) {
  const [failed, setFailed] = useState(!keyframeId);
  const s = (seed + (keyframeId ? [...keyframeId].reduce((a, c) => a + c.charCodeAt(0), 0) : 0)) % 4;
  return (
    <div className={cn("relative aspect-[16/10] overflow-hidden rounded-[4px] border-2 border-[var(--ink)] bg-white", className)}>
      {!failed && keyframeId ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={keyframeUrl(keyframeId)} alt={alt ?? title} className="h-full w-full object-cover object-top" onError={() => setFailed(true)} />
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
          <text x="66" y="38" fontSize="11" fontWeight="700" fill="#111" fontFamily="system-ui, sans-serif">
            {title.slice(0, 34)}
          </text>
          <rect x="250" y="28" width="58" height="14" rx="3" fill="#111" />
          <rect x="66" y="48" width="44" height="10" rx="2" fill="#ffe8a3" />
          {[0, 1, 2, 3].map((i) => (
            <g key={i}>
              <rect x={66 + (i % 2) * 124} y={68 + Math.floor(i / 2) * 26} width="40" height="5" rx="2" fill="#a8a49b" />
              <rect x={66 + (i % 2) * 124} y={76 + Math.floor(i / 2) * 26} width="112" height="11" rx="2" fill="#fff" stroke="#d6d2c8" />
            </g>
          ))}
          <rect x="66" y="126" width="236" height="62" rx="2" fill="#fff" stroke="#d6d2c8" />
          {[0, 1, 2].map((i) => (
            <rect key={i} x="72" y={134 + i * 17} width={120 + ((i + s) % 2) * 40} height="5" rx="2" fill="#cfcbc1" />
          ))}
          {highlight && (
            <g>
              <rect x="186" y="98" width="120" height="24" rx="3" fill="#ede4ff" stroke="#6d28d9" strokeWidth="2.5" />
              <text x="192" y="108" fontSize="7" fill="#3b1a85" fontFamily="system-ui, sans-serif">
                {highlight.label.slice(0, 22)}
              </text>
              <text x="192" y="118" fontSize="8.5" fontWeight="700" fill="#111" fontFamily="ui-monospace, monospace">
                {highlight.from ? `${highlight.from} → ` : ""}
                {highlight.to ?? ""}
              </text>
            </g>
          )}
        </svg>
      )}
    </div>
  );
}

/** Flip through several keyframes ("what the expert saw"). Respects reduced motion (manual only). */
export function Flipbook({ ids, title, highlight }: { ids: string[]; title?: string; highlight?: { label: string; from?: string | null; to?: string | null } | null }) {
  const frames = ids.length ? ids : ["", "", ""];
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);
  useEffect(() => {
    const reduce = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!playing || reduce || frames.length < 2) return;
    const h = setInterval(() => setI((x) => (x + 1) % frames.length), 900);
    return () => clearInterval(h);
  }, [playing, frames.length]);
  return (
    <div>
      <ScreenThumb keyframeId={frames[i] || null} seed={i} title={title} highlight={i === frames.length - 1 ? highlight : null} />
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setPlaying((p) => !p)}
          className="grid size-8 place-items-center rounded-[4px] border-2 border-[var(--ink)] bg-white"
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </button>
        <div className="flex flex-1 gap-1" role="tablist">
          {frames.map((_, k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={k === i}
              aria-label={`Frame ${k + 1}`}
              onClick={() => {
                setPlaying(false);
                setI(k);
              }}
              className={cn("h-2.5 flex-1 rounded-[2px] border-2 border-[var(--ink)]", k === i ? "bg-[var(--ink)]" : "bg-white")}
            />
          ))}
        </div>
      </div>
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
  return (
    <figure className={cn("rounded-[4px] border-2 border-[var(--ink)] bg-[var(--expert-soft)]", compact ? "p-2.5" : "p-3.5")}>
      <div className="flex items-start gap-2.5">
        {quote.audio_clip ? (
          <button
            type="button"
            onClick={toggle}
            aria-label={t("quote.play")}
            title={t("quote.play")}
            className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-full border-2 border-[var(--ink)] bg-[var(--expert)] transition-transform hover:-translate-y-0.5"
          >
            {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5 translate-x-px" />}
          </button>
        ) : null}
        <blockquote className="min-w-0 flex-1">
          <p lang={quote.lang} className={cn("font-semibold leading-snug", compact ? "text-sm" : "text-[15px]")}>
            “{quote.text}”
          </p>
          {translation ? (
            <p lang={lang} className="mt-1.5 text-sm leading-snug text-[var(--ink-2)]">
              <span className="mr-1 font-mono text-[11px] uppercase">{quote.lang}→{lang}</span>
              {translation}
            </p>
          ) : null}
        </blockquote>
      </div>
      <figcaption className="mt-2 flex items-center gap-1.5 text-xs font-semibold">
        <ExpertAvatar user={{ id: quote.speaker_id, name: quote.speaker }} size={20} />
        {quote.speaker}
        <span className="font-mono text-[11px] text-[var(--ink-2)]">· {quote.source}</span>
      </figcaption>
    </figure>
  );
}

/* ---------------- states ---------------- */

export function Loading({ label, rows = 3 }: { label?: string; rows?: number }) {
  const t = useT();
  return (
    <div role="status" aria-live="polite" className="space-y-3">
      <span className="inline-flex items-center gap-2 text-sm font-semibold">
        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
        {label ?? t("state.loading")}
      </span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="hatch h-14 rounded-[6px] border-2 border-dashed border-[var(--ink)]/40" />
      ))}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  const t = useT();
  return (
    <Panel tone="card" className="flex items-start gap-3 bg-[var(--missing)]/30 p-4" role="alert">
      <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="flex-1">
        <p className="font-bold">{t("state.error.title")}</p>
        {message ? <p className="mt-0.5 font-mono text-xs">{message}</p> : null}
      </div>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="inline-flex items-center gap-1.5 rounded-[4px] border-2 border-[var(--ink)] bg-white px-3 py-1.5 text-sm font-bold shadow-[var(--hard)] hover:translate-x-1 hover:translate-y-1 hover:shadow-none">
          <RotateCw className="size-4" aria-hidden />
          {t("state.error.retry")}
        </button>
      ) : null}
    </Panel>
  );
}

export function EmptyState({ children, icon, action }: { children: React.ReactNode; icon?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="hatch flex flex-col items-start gap-3 rounded-[6px] border-2 border-dashed border-[var(--ink)] p-5">
      {icon ?? <Sparkles className="size-5" aria-hidden />}
      <p className="max-w-[52ch] text-sm font-medium leading-relaxed">{children}</p>
      {action}
    </div>
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

export function ClarosDot({ speaking, size = 28 }: { speaking?: boolean; size?: number }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block shrink-0 rounded-full border-2 border-[var(--ink)] bg-[var(--claros)]", speaking && "orb-speaking")}
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
    <span title={t("data.demo.hint")} className="inline-flex items-center gap-1 rounded-[4px] border-2 border-dashed border-[var(--ink)] bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold">
      {t("data.demo")}
    </span>
  );
}
