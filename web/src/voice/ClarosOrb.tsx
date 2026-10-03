"use client";
/**
 * <ClarosOrb> — the companion's face. Neobrutalist card with an orb driven by
 * real audio levels (getLevel), captions always on, off-record + not-now buttons.
 * Works inside a Document PiP window (uses the element's own window for rAF/matchMedia).
 */
import { useEffect, useRef, useState } from "react";
import { EyeOff, Mic, MicOff, PauseCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type ClarosOrbState =
  | "listening" | "noticing" | "asking" | "speaking" | "off_record" | "away" | "reconnecting";

export interface ClarosOrbProps {
  state: ClarosOrbState;
  curiousCount?: number;
  caption?: string;
  onOffRecord?: () => void;
  onNotNow?: () => void;
  /** 0..1 audio level; called every animation frame */
  getLevel?: () => number;
  compact?: boolean;
  className?: string;
}

const LABEL: Record<ClarosOrbState, string> = {
  listening: "Listening",
  noticing: "Noticing",
  asking: "Has a question",
  speaking: "Speaking",
  off_record: "Off the record",
  away: "Paused — window hidden",
  reconnecting: "Reconnecting…",
};

// fill colours per state (neobrutalist flat fills, black border)
const FILL: Record<ClarosOrbState, string> = {
  listening: "#88aaee",
  noticing: "#fde047",
  asking: "#fb923c",
  speaking: "#a3e635",
  off_record: "#d4d4d4",
  away: "#e5e5e5",
  reconnecting: "#fca5a5",
};

export function ClarosOrb({
  state, curiousCount = 0, caption = "", onOffRecord, onNotNow, getLevel, compact, className,
}: ClarosOrbProps) {
  const orbRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  const [reduced, setReduced] = useState(false);
  const getLevelRef = useRef(getLevel);
  getLevelRef.current = getLevel;
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const win = orbRef.current?.ownerDocument.defaultView ?? window;
    const mq = win.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  useEffect(() => {
    const el = orbRef.current;
    const ring = ringRef.current;
    if (!el) return;
    const win = el.ownerDocument.defaultView ?? window;
    let raf = 0;
    let smooth = 0;
    const loop = (ts: number) => {
      const st = stateRef.current;
      let lvl = getLevelRef.current?.() ?? 0;
      if (st === "off_record" || st === "away") lvl = 0;
      smooth += (lvl - smooth) * 0.25;
      if (reduced) {
        // no motion: express level as ring thickness only
        if (ring) ring.style.boxShadow = `0 0 0 ${Math.round(smooth * 6)}px #000`;
        el.style.transform = "none";
      } else {
        const breathe = st === "listening" ? Math.sin(ts / 900) * 0.03 : 0;
        const pulse = st === "noticing" || st === "asking" ? Math.abs(Math.sin(ts / 260)) * 0.06 : 0;
        el.style.transform = `scale(${1 + breathe + pulse + smooth * 0.35})`;
        if (ring) ring.style.boxShadow = `0 0 0 ${Math.round(smooth * 10)}px rgba(0,0,0,.85)`;
      }
      raf = win.requestAnimationFrame(loop);
    };
    raf = win.requestAnimationFrame(loop);
    return () => win.cancelAnimationFrame(raf);
  }, [reduced]);

  const size = compact ? 56 : 88;

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-base border-2 border-border bg-secondary-background p-3 shadow-shadow text-foreground font-base",
        className,
      )}
      data-state={state}
    >
      <div className="flex items-center gap-3">
        <div className="relative grid place-items-center" style={{ width: size + 16, height: size + 16 }}>
          <div
            ref={ringRef}
            className="absolute rounded-full"
            style={{ width: size, height: size, transition: reduced ? undefined : "box-shadow 80ms linear" }}
          />
          <div
            ref={orbRef}
            role="img"
            aria-label={`Claros: ${LABEL[state]}`}
            className="rounded-full border-2 border-border will-change-transform"
            style={{
              width: size, height: size, background: FILL[state],
              backgroundImage: state === "off_record"
                ? "repeating-linear-gradient(45deg, transparent 0 6px, rgba(0,0,0,.18) 6px 9px)" : undefined,
            }}
          />
          {curiousCount > 0 && (
            <span
              className="absolute -top-1 -right-1 grid min-w-6 h-6 place-items-center rounded-full border-2 border-border bg-main px-1 text-xs font-heading text-main-foreground"
              aria-label={`${curiousCount} things Claros is curious about`}
            >
              {curiousCount > 99 ? "99+" : curiousCount}
            </span>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="font-heading text-base">Claros</span>
            <Badge variant={state === "off_record" ? "neutral" : "default"} className="gap-1">
              {state === "off_record" ? <MicOff className="size-3" /> : state === "away" ? <EyeOff className="size-3" /> : <Mic className="size-3" />}
              {LABEL[state]}
            </Badge>
          </div>
          {!compact && (
            <div className="flex gap-2">
              {onOffRecord && (
                <Button size="xs" variant={state === "off_record" ? "default" : "neutral"} onClick={onOffRecord}>
                  {state === "off_record" ? <Mic /> : <MicOff />}
                  {state === "off_record" ? "Back on record" : "Off the record"}
                </Button>
              )}
              {onNotNow && (
                <Button size="xs" variant="neutral" onClick={onNotNow}>
                  <PauseCircle /> Not now
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
      <p
        aria-live="polite"
        className="min-h-[2.5rem] rounded-base border-2 border-border bg-background px-2 py-1 text-sm leading-snug"
      >
        {caption || <span className="opacity-60">{LABEL[state]}</span>}
      </p>
    </div>
  );
}
