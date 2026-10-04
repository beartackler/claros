"use client";

/**
 * Expert voice clips (consent-first): with consent on, record the mic and cut ONE clip per final user utterance
 * (same event_id as the `utterance` message), then POST it to /api/sessions/{sid}/clips.
 * Never while off the record; a "strike that" drops whatever is buffered. ≤30 s, ≤1.5 MB per clip.
 */
import { useEffect, useRef } from "react";
import { now } from "@/lib/clock";
import { uploadClip } from "@/lib/api";
import { useLiveStore } from "./live";

const MAX_MS = 30_000;
const MAX_BYTES = 1_500_000;

function pickMime() {
  if (typeof MediaRecorder === "undefined") return null;
  for (const m of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]) if (MediaRecorder.isTypeSupported(m)) return m;
  return null;
}

const toB64 = (b: Blob) =>
  new Promise<string>((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1] ?? "");
    r.onerror = bad;
    r.readAsDataURL(b);
  });

export function useVoiceClips(sessionId: string | null, enabled: boolean) {
  const offRecord = useLiveStore((s) => s.offRecord);
  const transcript = useLiveStore((s) => s.transcript);
  const rec = useRef<{ mr: MediaRecorder; chunks: Blob[]; started: number; mime: string } | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const seen = useRef<string | null>(null);
  const active = enabled && Boolean(sessionId) && !offRecord;

  // (re)start a fresh recorder: every clip is a self-contained file
  const restart = () => {
    const s = stream.current;
    const mime = pickMime();
    if (!s || !mime) return;
    const chunks: Blob[] = [];
    const mr = new MediaRecorder(s, { mimeType: mime, audioBitsPerSecond: 32_000 });
    mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mr.start(1000);
    rec.current = { mr, chunks, started: Date.now(), mime };
  };
  const stop = (keep: boolean): Promise<{ blob: Blob; mime: string; ms: number } | null> =>
    new Promise((ok) => {
      const r = rec.current;
      rec.current = null;
      if (!r || r.mr.state === "inactive") return ok(null);
      r.mr.onstop = () => ok(keep ? { blob: new Blob(r.chunks, { type: r.mime }), mime: r.mime, ms: Date.now() - r.started } : null);
      r.mr.stop();
    });

  // mic stream + recorder while active; off the record → stop and drop
  useEffect(() => {
    if (!active) return;
    let alive = true;
    navigator.mediaDevices
      .getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      .then((s) => {
        if (!alive) return s.getTracks().forEach((t) => t.stop());
        stream.current = s;
        seen.current = useLiveStore.getState().transcript.findLast?.((l) => l.role === "user")?.id ?? null;
        restart();
      })
      .catch(() => {});
    // keep the buffer bounded when nobody speaks
    const roll = setInterval(() => {
      if (rec.current && Date.now() - rec.current.started > MAX_MS) void stop(false).then(restart);
    }, 2000);
    return () => {
      alive = false;
      clearInterval(roll);
      void stop(false);
      stream.current?.getTracks().forEach((t) => t.stop());
      stream.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // strike that (tap) → drop whatever is buffered
  useEffect(() => {
    if (!active) return;
    const on = () => void stop(false).then(restart);
    window.addEventListener("claros:strike", on);
    return () => window.removeEventListener("claros:strike", on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);
  // strike that (voice, echoed by the server) → same
  const log = useLiveStore((s) => s.serverLog);
  const last = log.at(-1);
  useEffect(() => {
    if (active && last?.type === "control" && last.action === "strike_that") void stop(false).then(restart);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last]);

  // one clip per final user utterance
  useEffect(() => {
    if (!active || !sessionId) return;
    const u = transcript.findLast?.((l) => l.role === "user");
    if (!u || u.id === seen.current) return;
    seen.current = u.id;
    const t_end = now();
    void stop(true).then(async (c) => {
      restart();
      if (!c || c.ms > MAX_MS + 1500 || c.blob.size > MAX_BYTES || c.blob.size < 2000) return;
      if (useLiveStore.getState().offRecord) return;
      await uploadClip(sessionId, { event_id: u.id, t_start: u.t, t_end, mime: c.mime.split(";")[0], audio_b64: await toB64(c.blob) });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcript, active, sessionId]);
}
