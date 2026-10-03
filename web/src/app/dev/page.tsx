"use client";
/**
 * /dev — end-to-end harness: session → WS → screen capture → keyframes → voice.
 */
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useScreenCapture } from "@/capture/useScreenCapture";
import { useClaros } from "@/voice/store";
import { useClarosSession } from "@/voice/useClarosSession";
import { useClarosVoice } from "@/voice/useClarosVoice";
import { ClarosCompanion } from "@/voice/ClarosCompanion";
import { clockInfo } from "@/lib/clock";
import { API_BASE, type Mode } from "@/lib/contracts";

const MODES: Mode[] = ["capture", "debrief", "learn", "request"];

export default function DevPage() {
  const [mode, setMode] = useState<Mode>("capture");
  const [lang, setLang] = useState("en");
  const [name, setName] = useState("Dana");
  const [manualId, setManualId] = useState("");
  const [text, setText] = useState("");
  const session = useClarosSession();
  const cap = useScreenCapture();
  const voice = useClarosVoice({ sessionId: session.sessionId, mode, lang, userName: name });
  const s = useClaros();
  const clock = clockInfo();
  const user = { id: name.toLowerCase(), name, role: mode === "learn" || mode === "request" ? ("learner" as const) : ("expert" as const) };

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-4 pb-72 font-base">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="font-heading text-2xl">Claros dev harness</h1>
        <Badge>api {API_BASE}</Badge>
        <Badge>ws {s.wsStatus}</Badge>
        <Badge>voice {s.voiceStatus}</Badge>
        <Badge>clock offset {Number.isFinite(clock.offset) ? clock.offset.toFixed(1) : "–"}ms rtt {Number.isFinite(clock.rtt) ? clock.rtt.toFixed(0) : "–"}ms</Badge>
      </header>

      <Card>
        <CardHeader><CardTitle>1 · Session</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <select className="h-10 rounded-base border-2 border-border bg-secondary-background px-2" value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            {MODES.map((m) => <option key={m}>{m}</option>)}
          </select>
          <select className="h-10 rounded-base border-2 border-border bg-secondary-background px-2" value={lang} onChange={(e) => setLang(e.target.value)}>
            {["en", "de", "fr", "es", "ru"].map((l) => <option key={l}>{l}</option>)}
          </select>
          <Input className="w-32" value={name} onChange={(e) => setName(e.target.value)} placeholder="user name" />
          <Button onClick={() => session.createSession({ mode, user, lang }).catch(() => {})}>Create session</Button>
          <Input className="w-56" value={manualId} onChange={(e) => setManualId(e.target.value)} placeholder="…or join session id" />
          <Button variant="neutral" disabled={!manualId} onClick={() => session.join({ session_id: manualId, mode, user, lang })}>Join</Button>
          <Button variant="neutral" disabled={!session.sessionId} onClick={() => void session.end()}>End session</Button>
          <span className="font-mono text-sm">id: {session.sessionId ?? "–"}</span>
          {session.error && <span className="text-sm text-red-600">{session.error}</span>}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>2 · Screen capture</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-2 text-sm">
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void cap.start()} disabled={cap.active}>Share a window</Button>
              <Button variant="neutral" onClick={cap.stop} disabled={!cap.active}>Stop</Button>
              <Button variant="neutral" onClick={() => cap.snap("boundary")} disabled={!cap.active}>Snap keyframe</Button>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge>surface {cap.surface}</Badge>
              <Badge>pipeline {cap.pipeline ?? "–"}</Badge>
              <Badge>dims {cap.dims.join("×")}</Badge>
              <Badge className="bg-main text-main-foreground">activity {cap.activity}</Badge>
              <Badge>away {String(cap.away)}</Badge>
            </div>
            {cap.stats && (
              <div className="font-mono text-xs">
                fps {cap.stats.fps.toFixed(1)} · tiles {cap.stats.tiles_changed} · dhash {cap.stats.dhash_dist.toFixed(3)} · frame-kind {cap.stats.kind}
              </div>
            )}
            <div className="font-mono text-xs">keyframes sent {cap.keyframes} · dropped {cap.dropped}</div>
            {cap.error && <div className="text-red-600">{cap.error}</div>}
            {cap.lastKeyframe && (
              <figure className="flex flex-col gap-1">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={cap.lastKeyframe.url} alt="last keyframe" className="max-h-56 w-full rounded-base border-2 border-border object-contain" />
                <figcaption className="font-mono text-xs">
                  #{cap.lastKeyframe.seq} {cap.lastKeyframe.reason} · {(cap.lastKeyframe.bytes / 1024).toFixed(0)}KB · {cap.lastKeyframe.tiles} changed rects
                </figcaption>
              </figure>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>3 · Voice</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-2 text-sm">
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void voice.start().catch((e) => alert(e.message))} disabled={!session.sessionId || s.voiceStatus === "connected"}>Start voice</Button>
              <Button variant="neutral" onClick={voice.end}>End voice</Button>
              <Button variant="neutral" onClick={() => voice.setOffRecord(!s.offRecord)}>{s.offRecord ? "On record" : "Off record"}</Button>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge>status {voice.status}</Badge>
              <Badge>agent {s.agentMode}</Badge>
              <Badge>user speaking {String(s.userSpeaking)}</Badge>
              <Badge>muted {String(voice.isMuted)}</Badge>
            </div>
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (text) { voice.sendText(text); setText(""); } }}>
              <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="type to the agent (e.g. ⟦ask:U1⟧)" />
              <Button type="submit" variant="neutral">Send</Button>
            </form>
            <div className="max-h-56 overflow-auto rounded-base border-2 border-border bg-background p-2 font-mono text-xs">
              {s.transcript.length === 0 && <div className="opacity-60">no transcript yet</div>}
              {s.transcript.map((l) => (
                <div key={l.id}><b>{l.role}</b>: {l.text}</div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Server messages ({s.serverLog.length})</CardTitle></CardHeader>
          <CardContent>
            <div className="max-h-72 overflow-auto font-mono text-xs">
              {s.serverLog.slice(-60).reverse().map((m, i) => (
                <div key={i} className="border-b border-border/30 py-0.5">
                  <b>{m.type}</b> {JSON.stringify(m).slice(0, 220)}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Events · ledger · tools</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-2 text-xs">
            <div>ledger: {s.ledger ? `open ${s.ledger.open}, saved ${s.ledger.saved_for_later}${s.ledger.top ? ` · top: ${s.ledger.top.spoken_question ?? s.ledger.top.id}` : ""}` : "–"}</div>
            <div className="max-h-40 overflow-auto font-mono">
              {s.events.slice(-30).reverse().map((e) => <div key={e.id}>[{e.kind}] {e.summary}</div>)}
            </div>
            <div className="font-mono">
              tools: {s.toolLog.slice(-8).map((t) => `${t.name}(${JSON.stringify(t.params)})`).join(" · ") || "–"}
            </div>
            <div>highlight {s.highlightedStepId ?? "–"} · open_map {s.openMapId ?? "–"} · moment {s.moment ? s.moment.keyframe_ids.join(",") || s.moment.t : "–"}</div>
          </CardContent>
        </Card>
      </div>

      <ClarosCompanion voice={voice} />
    </main>
  );
}
