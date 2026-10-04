"use client";

// Thin adapter over web-capture's hooks (src/capture, src/voice) so surfaces stay simple.
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, type Mode, type User } from "@/lib/contracts";
import { useUi } from "./i18n";
import { cn } from "@/lib/utils";
import { useScreenCapture } from "@/capture/useScreenCapture";
import { useClaros } from "@/voice/store";
import { openSession, sendControl } from "@/voice/useClarosSession";
import { prefetchVoiceToken, useClarosVoice, voiceMark } from "@/voice/useClarosVoice";

export { useClaros as useLiveStore, sendControl };

/** Join the WS for a session id once (no-op if the server is down: socket retries quietly). */
export function useJoinSession(sessionId: string | null, mode: Mode, user: User, lang: string, workflowId?: string | null, consent?: { voice_clips: boolean }) {
  const joined = useRef<string | null>(null);
  useEffect(() => {
    if (!sessionId || joined.current === sessionId) return;
    joined.current = sessionId;
    try {
      openSession({ session_id: sessionId, mode, user, lang, workflow_id: workflowId ?? null, ...(consent ? { consent } : {}) });
    } catch {
      /* offline demo */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
export async function requestMic(): Promise<boolean | "missing"> {
  try {
    voiceMark("mic.ask");
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    voiceMark("mic.ok");
    return true;
  } catch (e) {
    const n = (e as DOMException)?.name;
    return n === "NotFoundError" || n === "OverconstrainedError" ? "missing" : false;
  }
}

/**
 * Open the window picker via the capture hook and classify the outcome
 * (the hook swallows the error, so we observe getDisplayMedia around the call).
 */
export async function shareWindow(capture: ReturnType<typeof useScreenCapture>): Promise<"ok" | "monitor" | "tab" | "cancelled" | "system"> {
  voiceMark("start");
  prefetchVoiceToken(); // the voice token loads while the picker is open
  const md = navigator.mediaDevices;
  const orig = md.getDisplayMedia.bind(md);
  let err: DOMException | null = null;
  md.getDisplayMedia = async (c?: DisplayMediaStreamOptions) => {
    try {
      return await orig(c);
    } catch (e) {
      err = e as DOMException;
      throw e;
    }
  };
  try {
    await capture.start();
  } finally {
    md.getDisplayMedia = orig;
  }
  const stream = capture.stream.current;
  if (!stream) {
    const e = err as DOMException | null;
    // macOS without Screen Recording permission: Chrome reports "Permission denied by system"
    return e && (/system/i.test(e.message) || e.name === "NotReadableError") ? "system" : "cancelled";
  }
  const surface = (stream.getVideoTracks()[0]?.getSettings() as { displaySurface?: string })?.displaySurface;
  voiceMark("share.ok");
  return surface === "monitor" ? "monitor" : surface === "browser" ? "tab" : "ok";
}

/** Is the Claros server up? "waking" when it takes more than a moment (free hosts sleep). */
export function useServerStatus() {
  const [s, setS] = useState<"checking" | "waking" | "up" | "down">("checking");
  useEffect(() => {
    let alive = true;
    const slow = setTimeout(() => alive && setS((x) => (x === "checking" ? "waking" : x)), 1500);
    const ctl = new AbortController();
    const give = setTimeout(() => ctl.abort(), 45_000);
    fetch(`${API_BASE}/healthz`, { signal: ctl.signal })
      .then((r) => alive && setS(r.ok ? "up" : "down"))
      .catch(() => alive && setS("down"))
      .finally(() => {
        clearTimeout(slow);
        clearTimeout(give);
      });
    return () => {
      alive = false;
      clearTimeout(slow);
      clearTimeout(give);
      ctl.abort();
    };
  }, []);
  return s;
}

/** "Reconnecting…" once a live socket or voice session drops; nothing while healthy. */
export function ConnectionBanner({ className }: { className?: string }) {
  const ws = useClaros((s) => s.wsStatus);
  const voice = useClaros((s) => s.voiceStatus);
  const [had, setHad] = useState({ ws: false, voice: false });
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (ws === "open" && !had.ws) setHad((h) => ({ ...h, ws: true }));
    if (voice === "connected" && !had.voice) setHad((h) => ({ ...h, voice: true }));
  }, [ws, voice, had]);
  const lost = (had.ws && ws !== "open") || (had.voice && (voice === "connecting" || voice === "error"));
  if (!lost) return null;
  return (
    <p role="status" className={cn("inline-flex items-center gap-2.5 rounded-base border-2 border-ink bg-partial/50 px-3 py-1.5 text-base font-bold", className)}>
      <span className="size-3 animate-pulse rounded-full border-2 border-ink bg-partial motion-reduce:animate-none" aria-hidden />
      <ReconnectLabel />
    </p>
  );
}
function ReconnectLabel() {
  const { t } = useUi();
  return <>{t("live.reconnecting")}</>;
}

/** Resolve when the voice agent is connected (true) or failed / timed out (false). */
export function waitVoice(timeoutMs = 25_000): Promise<boolean> {
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
  useEffect(() => {
    lvl.current = getLevel;
  });
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
        className={cn("relative rounded-full border-[3px] border-ink will-change-transform", off ? "bg-ink" : "bg-claros", speaking && !off && "shadow-[6px_6px_0_0_var(--ink)]")}
        style={{
          width: size,
          height: size,
          backgroundImage: off
            ? "repeating-linear-gradient(45deg, transparent 0 8px, rgba(255,255,255,.14) 8px 11px)"
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

/* ---------------- off the record (honest): off by voice or tap, back only by tapping Resume ---------------- */

export function useOffRecord(voice: ReturnType<typeof useClarosVoice>) {
  const off = useClaros((s) => s.offRecord);
  const vRef = useRef(voice);
  useEffect(() => {
    vRef.current = voice;
  });
  const setOff = useCallback((on: boolean) => {
    const v = vRef.current;
    if (v.status === "connected") v.setOffRecord(on);
    else {
      useClaros.getState().set({ offRecord: on });
      sendControl(on ? "off_record_on" : "off_record_off");
    }
  }, []);
  const strike = useCallback(() => {
    sendControl("strike_that");
    window.dispatchEvent(new Event("claros:strike"));
  }, []);
  // R resumes (page focus); the PiP orb handles R inside its own window
  useEffect(() => {
    if (!off) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest?.("input,textarea,select")) return;
      if (e.key.toLowerCase() === "r" && !e.metaKey && !e.ctrlKey) setOff(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [off, setOff]);
  return { off, goOff: () => setOff(true), resume: () => setOff(false), strike };
}

/** Localized labels for the voice/ ClarosOrb (PiP companion). */
export function useOrbLabels() {
  const { t } = useUi();
  return {
    resume: t("off.resume"),
    off: t("cap.off"),
    notNow: t("off.notNow"),
    strike: t("off.strike"),
    states: {
      listening: t("live.listening"),
      speaking: t("live.speaking"),
      off_record: t("cap.off"),
      reconnecting: t("live.reconnecting"),
      asking: t("off.asking"),
      noticing: t("off.noticing"),
      away: t("off.away"),
    },
  };
}

/** Big, unmissable Resume — the only way back on the record. */
export function ResumeBar({ onResume, className }: { onResume: () => void; className?: string }) {
  const { t } = useUi();
  return (
    <div role="status" className={cn("claros-enter flex flex-wrap items-center gap-4 rounded-[10px] border-[3px] border-ink bg-ink p-4 text-paper sm:p-5", className)}>
      <p className="min-w-0 flex-1 text-2xl font-black tracking-[-0.02em]">{t("off.title")}</p>
      <ResumeButton onResume={onResume} />
    </div>
  );
}
function ResumeButton({ onResume }: { onResume: () => void }) {
  const { t } = useUi();
  return (
    <button
      type="button"
      onClick={onResume}
      autoFocus
      className="press inline-flex h-14 items-center gap-3 rounded-base border-2 border-paper bg-claros px-6 text-xl font-black text-claros-ink shadow-[4px_4px_0_0_var(--paper)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros"
    >
      {t("off.resume")} <kbd className="rounded-[3px] border-2 border-claros-ink/60 px-1.5 font-mono text-sm">R</kbd>
    </button>
  );
}
