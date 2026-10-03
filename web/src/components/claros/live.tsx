"use client";

// Thin adapter over web-capture's hooks (src/capture, src/voice) so surfaces stay simple.
import { useCallback, useEffect, useRef, useState } from "react";
import type { Mode, User } from "@/lib/contracts";
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
