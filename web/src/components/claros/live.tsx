"use client";

// Thin adapter over web-capture's hooks (src/capture, src/voice) so surfaces stay simple.
import { useCallback, useEffect, useRef, useState } from "react";
import { PictureInPicture2 } from "lucide-react";
import type { Mode, User } from "@/lib/contracts";
import { useScreenCapture } from "@/capture/useScreenCapture";
import { useClaros } from "@/voice/store";
import { openSession, sendControl } from "@/voice/useClarosSession";
import { useClarosVoice } from "@/voice/useClarosVoice";
import { cn } from "@/lib/utils";

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

/** Pops a floating Claros orb using Document Picture-in-Picture (Chrome); falls back to a small popup window. */
export function PipLauncher({ label, sub, className }: { label: string; sub?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const launch = async () => {
    const dpip = (window as unknown as { documentPictureInPicture?: { requestWindow: (o: { width: number; height: number }) => Promise<Window> } }).documentPictureInPicture;
    let w: Window | null = null;
    try {
      w = dpip ? await dpip.requestWindow({ width: 220, height: 220 }) : window.open("", "claros-orb", "width=220,height=220");
    } catch {
      w = null;
    }
    if (!w) return;
    w.document.title = "Claros";
    w.document.body.style.cssText = "margin:0;display:grid;place-items:center;height:100vh;background:#fbf6ea;font-family:system-ui,sans-serif";
    w.document.body.innerHTML = `<style>@keyframes b{0%,100%{transform:scale(1)}50%{transform:scale(1.08)}}@media (prefers-reduced-motion:reduce){div{animation:none!important}}</style>
      <div style="width:110px;height:110px;border-radius:50%;border:3px solid #0b0b0f;background:#6d28d9 radial-gradient(circle at 32% 30%,rgba(255,255,255,.85) 0 14%,transparent 15%);box-shadow:5px 5px 0 #0b0b0f;animation:b 1.8s ease-in-out infinite"></div>`;
    setOpen(true);
    w.addEventListener("pagehide", () => setOpen(false));
  };
  return (
    <button
      type="button"
      onClick={launch}
      className={cn(
        "flex w-full items-center gap-3 rounded-[6px] border-2 border-[var(--ink)] bg-white p-3 text-left shadow-[var(--hard)] transition-[transform,box-shadow] hover:translate-x-1 hover:translate-y-1 hover:shadow-none",
        open && "bg-[var(--claros-soft)]",
        className,
      )}
    >
      <span className="grid size-10 shrink-0 place-items-center rounded-full border-2 border-[var(--ink)] bg-[var(--claros)] text-white">
        <PictureInPicture2 className="size-5" aria-hidden />
      </span>
      <span>
        <span className="block font-extrabold">{label}</span>
        {sub ? <span className="block text-xs text-[var(--ink-2)]">{sub}</span> : null}
      </span>
    </button>
  );
}
