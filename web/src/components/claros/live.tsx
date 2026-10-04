"use client";

// Thin adapter over web-capture's hooks (src/capture, src/voice) so surfaces stay simple.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Mode, User } from "@/lib/contracts";
import { cn } from "@/lib/utils";
import { useScreenCapture } from "@/capture/useScreenCapture";
import { useClaros } from "@/voice/store";
import { openSession, sendControl } from "@/voice/useClarosSession";
import { useClarosVoice } from "@/voice/useClarosVoice";

export { useClaros as useLiveStore, sendControl };

/** Join the WS for a session id once (no-op if the server is down: socket retries quietly). */
export function useJoinSession(sessionId: string | null, mode: Mode, user: User, lang: string, workflowId?: string | null) {
  const joined = useRef<string | null>(null);
  useEffect(() => {
    if (!sessionId || joined.current === sessionId) return;
    joined.current = sessionId;
    try {
      openSession({ session_id: sessionId, mode, user, lang, workflow_id: workflowId ?? null });
    } catch {
      /* offline demo */
    }
  }, [sessionId, mode, user, lang, workflowId]);
}

export function useLive(sessionId: string | null, mode: Mode, lang: string, userName: string) {
  const capture = useScreenCapture();
  const voice = useClarosVoice({ sessionId, mode, lang, userName });
  return { capture, voice };
}

export function useMicCheck() {
  const [state, setState] = useState<"idle" | "checking" | "ok" | "denied">("idle");
  const [level, setLevel] = useState(0);
  const check = useCallback(async () => {
    setState("checking");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const an = ctx.createAnalyser();
      an.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.frequencyBinCount);
      const started = performance.now();
      setState("ok");
      const tick = () => {
        an.getByteFrequencyData(buf);
        setLevel(Math.min(1, buf.reduce((a, b) => a + b, 0) / buf.length / 90));
        if (performance.now() - started < 4000) requestAnimationFrame(tick);
        else {
          stream.getTracks().forEach((t) => t.stop());
          void ctx.close();
          setLevel(0);
        }
      };
      tick();
    } catch {
      setState("denied");
    }
  }, []);
  return { state, level, check };
}

/* ---------------- start-sequence helpers ---------------- */

/** Ask for the microphone once (permission only; the voice session opens its own stream). */
export async function requestMic(): Promise<boolean> {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

/** Resolve when the voice agent is connected (true) or failed / timed out (false). */
export function waitVoice(timeoutMs = 15_000): Promise<boolean> {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v: boolean) => {
      if (done) return;
      done = true;
      unsub();
      clearTimeout(timer);
      resolve(v);
    };
    const check = () => {
      const v = useClaros.getState().voiceStatus;
      if (v === "connected") finish(true);
      else if (v === "error") finish(false);
    };
    const unsub = useClaros.subscribe(check);
    const timer = setTimeout(() => finish(useClaros.getState().voiceStatus === "connected"), timeoutMs);
    check();
  });
}

/* ---------------- the big orb + live captions ---------------- */

/** Big Claros orb driven by real audio levels. Violet = Claros. */
export function LiveOrb({ size = 220, getLevel, speaking, off }: { size?: number; getLevel?: () => number; speaking?: boolean; off?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const ring = useRef<HTMLDivElement>(null);
  const lvl = useRef(getLevel);
  lvl.current = getLevel;
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let smooth = 0;
    const loop = (ts: number) => {
      const v = off ? 0 : lvl.current?.() ?? 0;
      smooth += (v - smooth) * 0.2;
      if (ref.current && !reduce) {
        const breathe = Math.sin(ts / 1100) * 0.02;
        ref.current.style.transform = `scale(${1 + breathe + smooth * 0.22})`;
      }
      if (ring.current) ring.current.style.boxShadow = `0 0 0 ${Math.round(4 + smooth * 22)}px var(--claros-soft)`;
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [off]);
  return (
    <div className="relative grid place-items-center" style={{ width: size * 1.25, height: size * 1.25 }} aria-hidden>
      <div ref={ring} className="absolute rounded-full transition-[box-shadow] duration-100" style={{ width: size, height: size }} />
      <div
        ref={ref}
        className={cn("relative rounded-full border-[3px] border-ink will-change-transform", off ? "bg-paper-2" : "bg-claros", speaking && !off && "shadow-[6px_6px_0_0_var(--ink)]")}
        style={{
          width: size,
          height: size,
          backgroundImage: off
            ? "repeating-linear-gradient(45deg, transparent 0 8px, rgba(0,0,0,.14) 8px 11px)"
            : "radial-gradient(circle at 32% 28%, rgba(255,255,255,.8) 0 9%, transparent 10%)",
        }}
      />
    </div>
  );
}

/** Latest thing Claros said (big) and the latest thing the person said (small). */
export function LiveCaptions({ idle, className }: { idle?: string; className?: string }) {
  const transcript = useClaros((s) => s.transcript);
  const caption = useClaros((s) => s.caption);
  const lastAgent = [...transcript].reverse().find((l) => l.role === "agent");
  const lastUser = [...transcript].reverse().find((l) => l.role === "user");
  const agentText = (lastAgent?.text || caption || "").replace(/⟦[^⟧]*⟧/g, "").trim();
  const userAfter = lastUser && (!lastAgent || lastUser.t > lastAgent.t);
  return (
    <div className={cn("w-full", className)} aria-live="polite">
      <p key={agentText} className={cn("claros-enter text-balance text-3xl font-black leading-[1.12] tracking-[-0.03em] sm:text-4xl 2xl:text-5xl", !agentText && "text-ink-2")}>
        {agentText || idle}
      </p>
      {userAfter ? <p className="mt-4 text-xl font-semibold text-ink-2">“{lastUser!.text}”</p> : null}
    </div>
  );
}
