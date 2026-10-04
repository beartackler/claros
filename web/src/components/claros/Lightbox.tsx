"use client";

/**
 * Screenshots are evidence: every screen moment is a ZoomShot. Click → it grows into a lightbox
 * (shared-element `layoutId`), Esc / backdrop / × closes, ← → walk the frames, swipe on touch.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, LayoutGroup, MotionConfig, motion } from "motion/react";
import { ChevronLeft, ChevronRight, Maximize2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "./i18n";
import { ScreenThumb, type Highlight } from "./primitives";

export type Frame = { id: string; keyframeId?: string | null; title?: string; caption?: React.ReactNode; highlight?: Highlight; seed?: number };

type Open = { group: string; frames: Frame[]; index: number; navigated: boolean };
type Ctx = { open: (group: string, frames: Frame[], index: number) => void; current: Open | null };

const LightboxCtx = createContext<Ctx | null>(null);
const EASE = [0.16, 1, 0.3, 1] as const;
const lid = (group: string, f: Frame) => `shot:${group}:${f.id}`;

export function LightboxProvider({ children }: { children: React.ReactNode }) {
  const [cur, setCur] = useState<Open | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const open = useCallback((group: string, frames: Frame[], index: number) => {
    returnFocus.current = document.activeElement as HTMLElement | null;
    setCur({ group, frames, index, navigated: false });
  }, []);
  const close = useCallback(() => {
    setCur(null);
    requestAnimationFrame(() => returnFocus.current?.focus?.({ preventScroll: true }));
  }, []);
  const go = useCallback((d: number) => setCur((c) => (c ? { ...c, index: (c.index + d + c.frames.length) % c.frames.length, navigated: true } : c)), []);
  const value = useMemo(() => ({ open, current: cur }), [open, cur]);
  return (
    <MotionConfig reducedMotion="user" transition={{ duration: 0.42, ease: EASE }}>
      <LayoutGroup>
        <LightboxCtx.Provider value={value}>
          {children}
          <Overlay cur={cur} onClose={close} onGo={go} />
        </LightboxCtx.Provider>
      </LayoutGroup>
    </MotionConfig>
  );
}

function Overlay({ cur, onClose, onGo }: { cur: Open | null; onClose: () => void; onGo: (d: number) => void }) {
  const t = useT();
  const closeRef = useRef<HTMLButtonElement>(null);
  const [mounted, setMounted] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!cur) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") onGo(1);
      else if (e.key === "ArrowLeft") onGo(-1);
      else if (e.key === "Tab") {
        // keep focus inside the lightbox
        const root = document.getElementById("claros-lightbox");
        const items = root ? Array.from(root.querySelectorAll<HTMLElement>("button")) : [];
        if (!items.length) return;
        const i = items.indexOf(document.activeElement as HTMLElement);
        if (e.shiftKey && i <= 0) {
          e.preventDefault();
          items[items.length - 1].focus();
        } else if (!e.shiftKey && i === items.length - 1) {
          e.preventDefault();
          items[0].focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [cur, onClose, onGo]);

  if (!mounted) return null;
  const f = cur ? cur.frames[cur.index] : null;
  const many = (cur?.frames.length ?? 0) > 1;

  return createPortal(
    <AnimatePresence>
      {cur && f ? (
        <motion.div
          key="lightbox"
          id="claros-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={f.title || t("shot.zoom")}
          className="fixed inset-0 z-[70] flex flex-col"
          initial={{ opacity: 1 }}
          exit={{ opacity: 1 }}
        >
          <motion.button
            type="button"
            aria-label={t("common.close")}
            tabIndex={-1}
            onClick={onClose}
            className="absolute inset-0 cursor-zoom-out bg-ink/85"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
          />
          <div className="pointer-events-none relative z-[1] flex items-center justify-between gap-3 p-3 text-paper sm:p-5">
            <motion.p
              className="tnum pointer-events-auto font-mono text-sm font-bold"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              {many ? `${cur.index + 1} / ${cur.frames.length}` : ""}
            </motion.p>
            <motion.button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label={t("common.close")}
              className="press pointer-events-auto grid size-12 place-items-center rounded-base border-2 border-paper bg-card text-ink shadow-[4px_4px_0_0_var(--claros)]"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <X className="size-6" aria-hidden />
            </motion.button>
          </div>

          <div className="pointer-events-none relative z-[1] flex min-h-0 flex-1 items-center justify-center px-3 pb-3 sm:px-20 sm:pb-6">
            <motion.figure
              key={cur.navigated ? `nav-${cur.index}` : "first"}
              layoutId={cur.navigated ? undefined : lid(cur.group, f)}
              initial={cur.navigated ? { opacity: 0, x: 24 } : undefined}
              animate={cur.navigated ? { opacity: 1, x: 0 } : undefined}
              exit={cur.navigated ? { opacity: 0 } : undefined}
              drag={many ? "x" : false}
              dragConstraints={{ left: 0, right: 0 }}
              dragElastic={0.25}
              onDragEnd={(_, info) => {
                if (info.offset.x < -60) onGo(1);
                else if (info.offset.x > 60) onGo(-1);
              }}
              className="pointer-events-auto w-[min(100%,calc((100dvh-11rem)*1.6))] overflow-hidden rounded-base border-2 border-paper bg-card shadow-[8px_8px_0_0_var(--claros)]"
              style={{ borderRadius: 6 }}
            >
              <ScreenThumb keyframeId={f.keyframeId} title={f.title} highlight={f.highlight} seed={f.seed} rounded={false} className="border-0" eager />
            </motion.figure>
          </div>

          {f.caption ? (
            <motion.div
              className="relative z-[1] mx-auto mb-4 w-[min(100%-1.5rem,72rem)] text-center text-lg font-semibold text-paper sm:mb-6"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
            >
              {f.caption}
            </motion.div>
          ) : null}

          {many ? (
            <>
              <NavBtn side="left" label={t("shot.prev")} onClick={() => onGo(-1)} />
              <NavBtn side="right" label={t("shot.next")} onClick={() => onGo(1)} />
            </>
          ) : null}
        </motion.div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}

function NavBtn({ side, label, onClick }: { side: "left" | "right"; label: string; onClick: () => void }) {
  const Icon = side === "left" ? ChevronLeft : ChevronRight;
  return (
    <motion.button
      type="button"
      onClick={onClick}
      aria-label={label}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className={cn(
        "press absolute bottom-4 z-[2] grid size-12 place-items-center rounded-base border-2 border-paper bg-card text-ink shadow-[4px_4px_0_0_var(--claros)] sm:top-1/2 sm:bottom-auto sm:-mt-6",
        side === "left" ? "left-4" : "right-4",
      )}
    >
      <Icon className="size-6" aria-hidden />
    </motion.button>
  );
}

/** A screenshot that zooms. `frames` + `index` let ← → walk the neighbours inside the lightbox. */
export function ZoomShot({
  group,
  frames,
  index = 0,
  className,
  label,
  priority,
}: {
  group: string;
  frames: Frame[];
  index?: number;
  className?: string;
  label?: string;
  priority?: boolean;
}) {
  const ctx = useContext(LightboxCtx);
  const t = useT();
  const f = frames[index] ?? frames[0];
  if (!f) return null;
  return (
    <motion.button
      type="button"
      layoutId={lid(group, f)}
      onClick={() => ctx?.open(group, frames, index)}
      aria-label={label ?? `${t("shot.zoom")}${f.title ? `: ${f.title}` : ""}`}
      className={cn(
        "group/shot relative block w-full cursor-zoom-in overflow-hidden rounded-base border-2 border-ink bg-card text-left focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros",
        className,
      )}
      style={{ borderRadius: 6 }}
    >
      <ScreenThumb keyframeId={f.keyframeId} title={f.title} highlight={f.highlight} seed={f.seed} rounded={false} className="border-0" eager={priority} />
      <span
        aria-hidden
        className="absolute right-2 bottom-2 grid size-10 place-items-center rounded-base border-2 border-ink bg-card text-ink shadow-hard-sm transition-opacity duration-150 sm:opacity-0 sm:group-hover/shot:opacity-100 sm:group-focus-visible/shot:opacity-100"
      >
        <Maximize2 className="size-5" />
      </span>
    </motion.button>
  );
}

/** Main frame + a row of thumbnails (several frames of one moment). */
export function ShotStrip({ group, frames, className }: { group: string; frames: Frame[]; className?: string }) {
  const t = useT();
  const [i, setI] = useState(0);
  const safe = Math.min(i, Math.max(0, frames.length - 1));
  if (!frames.length) return null;
  return (
    <div className={className}>
      <ZoomShot group={group} frames={frames} index={safe} />
      {frames.length > 1 ? (
        <ol className="mt-3 flex gap-2 overflow-x-auto pb-1" aria-label={t("shot.frames")}>
          {frames.map((f, k) => (
            <li key={f.id} className="w-24 shrink-0 sm:w-28">
              <button
                type="button"
                onClick={() => setI(k)}
                aria-pressed={k === safe}
                aria-label={`${k + 1} / ${frames.length}`}
                className={cn("press press-sm block w-full overflow-hidden rounded-[4px] border-2 border-ink shadow-hard-sm", k === safe && "outline-3 outline-offset-1 outline-claros")}
              >
                <ScreenThumb keyframeId={f.keyframeId} seed={f.seed} rounded={false} className="border-0" />
              </button>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
