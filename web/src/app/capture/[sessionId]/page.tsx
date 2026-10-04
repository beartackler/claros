"use client";

/**
 * Expert capture: one Start (share → mic → Claros says hi), then nothing to read —
 * a big orb, live captions, and the one control that matters (Done → debrief, same session).
 */
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { EyeOff, PictureInPicture2, Square } from "lucide-react";
import { Shell } from "@/components/claros/Shell";
import { LANGS, LANG_NAMES, useUi, type UiLang } from "@/components/claros/i18n";
import { ConnectionBanner, ResumeBar, useOffRecord, useOrbLabels, LiveCaptions, LiveOrb, requestMic, shareWindow, sendControl, useJoinSession, useLive, useLiveStore, waitVoice } from "@/components/claros/live";
import { StartSequence, type ShareResult } from "@/components/claros/StartSequence";
import { ClarosOrb } from "@/voice/ClarosOrb";
import { useOrbState } from "@/voice/ClarosCompanion";
import { PipPortal, usePip } from "@/voice/pip";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useVoiceClips } from "@/components/claros/useVoiceClips";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { endSession, listRequests } from "@/lib/api";
import { EXPERT, MOCK_EVENTS, MOCK_UNKNOWNS } from "@/lib/mock";
import type { ScreenEvent, Unknown, Why } from "@/lib/contracts";
import { LookedUpList, SignalsBar, WhyTag, useLatestWhy, useLookedUp } from "@/components/claros/Evidence";
import { cn } from "@/lib/utils";

const BUDGET = 5;

export default function CapturePage() {
  return (
    <Shell focus={{ label: "focus.capture" }}>
      <Suspense fallback={null}>
        <Capture />
      </Suspense>
    </Shell>
  );
}

function Capture() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const params = useSearchParams();
  const { t, lang } = useUi();
  const [speakLang, setSpeakLang] = useState<UiLang>(lang);
  const [live, setLive] = useState(params.get("demo") === "1");
  const [forWhom, setForWhom] = useState<string | null>(null);
  // follow the UI language until recording starts (prefs hydrate after first render)
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!live) setSpeakLang(lang);
  }, [lang, live]);
  // recording for a learner's request: say whose
  const requestId = params.get("request");
  useEffect(() => {
    if (!requestId) return;
    listRequests().then((r) => {
      const req = r.data.find((x) => x.id === requestId);
      if (req) setForWhom(`${req.requested_by.name.split(" ")[0]} · ${req.workflow_hint}`);
    });
  }, [requestId]);

  // consent is part of `hello`, so the socket joins when Start is pressed (or in the demo)
  const [clips, setClips] = useState(false);
  const [joined, setJoined] = useState(params.get("demo") === "1");
  useJoinSession(joined ? sessionId : null, "capture", EXPERT, speakLang, null, { voice_clips: clips });
  useVoiceClips(sessionId, clips && live);
  const { capture, voice } = useLive(sessionId, "capture", speakLang, EXPERT.name);
  const voiceRef = useRef(voice);
  useEffect(() => {
    voiceRef.current = voice;
  });

  const onShare = (): Promise<ShareResult> => {
    const picking = shareWindow(capture); // straight from the click: the picker needs the gesture
    setJoined(true);
    return picking;
  };

  const onVoice = async () => {
    try {
      await voiceRef.current.start();
    } catch {
      return false;
    }
    return waitVoice();
  };

  if (!live)
    return (
      <StartSequence
        persona="expert"
        title={t("eh.title")}
        sub={forWhom ?? t("eh.sub")}
        privacy="start.privacy.expert"
        onShare={onShare}
        onMic={requestMic}
        onStopShare={capture.stop}
        onVoice={onVoice}
        onDone={() => setLive(true)}
        onSkipVoice={() => setLive(true)}
        aside={
          <>
          <div className="flex items-center gap-2 text-base font-bold">
            <span id="speak-label">{t("start.speak")}</span>
            <Select items={LANGS.map((l) => ({ value: l, label: LANG_NAMES[l] }))} value={speakLang} onValueChange={(v) => v && setSpeakLang(v as UiLang)}>
              <SelectTrigger aria-labelledby="speak-label" className="w-auto min-w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LANGS.map((l) => (
                  <SelectItem key={l} value={l}>
                    {LANG_NAMES[l]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Label className="flex w-full cursor-pointer items-center gap-3 text-base font-bold">
            <Switch checked={clips} onCheckedChange={(v) => setClips(Boolean(v))} />
            {t("clips.consent")}
          </Label>
          </>
        }
      />
    );

  return <LiveCapture sessionId={sessionId} capture={capture} voice={voice} />;
}

/* ---------------- live: orb + captions, nothing to read ---------------- */

type Live = ReturnType<typeof useLive>;

function useDemoDrip(enabled: boolean) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const h = setInterval(() => setN((x) => (x >= MOCK_EVENTS.length ? x : x + 1)), 2600);
    return () => clearInterval(h);
  }, [enabled]);
  return n;
}

function LiveCapture({ sessionId, capture, voice }: { sessionId: string; capture: Live["capture"]; voice: Live["voice"] }) {
  const { t } = useUi();
  const router = useRouter();
  const liveEvents = useLiveStore((s) => s.events);
  const ledger = useLiveStore((s) => s.ledger);
  const offRecord = useLiveStore((s) => s.offRecord);
  const phase = useLiveStore((s) => s.phase);
  const caption = useLiveStore((s) => s.caption);
  const demo = useSearchParams().get("demo") === "1" && liveEvents.length === 0; // only an explicit review flag, never on a dropped socket
  const orb = useOrbState();
  const pip = usePip();

  // Server moves the session to debrief (voice "I'm done", end_task, or POST end) → follow it.
  useEffect(() => {
    if (phase === "debrief") {
      capture.stop(); // the debrief needs the voice, not the screen
      router.push(`/debrief/${encodeURIComponent(sessionId)}`);
    }
  }, [phase, router, sessionId, capture]);
  // leaving capture by any route (Exit, back button, server phase change) ends the screen share
  // (deferred: React dev mounts → unmounts → remounts once; only a real unmount may stop the share)
  const stopShare = capture.stop;
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      setTimeout(() => { if (!mounted.current) stopShare(); }, 0);
    };
  }, [stopShare]);
  const drip = useDemoDrip(demo && !offRecord);

  const [seen, setSeen] = useState<Record<string, Unknown>>({});
  useEffect(() => {
    const top = ledger?.top;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (top) setSeen((s) => ({ ...s, [top.id]: top }));
  }, [ledger?.top]);
  const events: ScreenEvent[] = demo ? MOCK_EVENTS.slice(0, drip) : liveEvents;
  const unknowns: Unknown[] = demo ? MOCK_UNKNOWNS.filter((u) => u.about_event_ids.some((id) => events.some((e) => e.id === id))) : Object.values(seen);
  const asked = unknowns.filter((u) => u.status === "asked" || u.status === "answered").length;
  const latestWhy = useLatestWhy();
  const demoWhy: Why | null = demo ? { when: t("ev.demo.when"), what: t("ev.demo.what"), scope: "company" } : null;
  const lookedUp = useLookedUp(
    demo
      ? MOCK_UNKNOWNS.filter((u) => u.status === "resolved" && events.some((e) => u.about_event_ids.includes(e.id))).map((u) => ({
          type: "looked_up" as const,
          unknown_summary: u.entity ?? u.type,
          answer: u.resolution ?? "",
          source: u.resolution_source?.startsWith("app_docs:")
            ? { kind: "app_docs" as const, title: new URL(u.resolution_source.slice(9)).hostname.replace(/^www\./, ""), url: u.resolution_source.slice(9) }
            : { kind: "general" as const, title: "LLM" },
        }))
      : [],
  );
  const demoCaption = demo ? MOCK_UNKNOWNS.find((u) => u.status === "asked" && u.about_event_ids.some((id) => events.some((e) => e.id === id)))?.spoken_question : undefined;

  const [elapsed, setElapsed] = useState(0);
  const [startAt] = useState(() => Date.now());
  useEffect(() => {
    const h = setInterval(() => setElapsed(Date.now() - startAt), 1000);
    return () => clearInterval(h);
  }, [startAt]);
  const mmss = `${String(Math.floor(elapsed / 60000)).padStart(2, "0")}:${String(Math.floor(elapsed / 1000) % 60).padStart(2, "0")}`;

  const rec = useOffRecord(voice);
  const orbLabels = useOrbLabels();
  const toggleOff = () => (offRecord ? rec.resume() : rec.goOff());


  const [ending, setEnding] = useState(false);
  const finish = async () => {
    // keep the voice session alive: the same conversation continues into the debrief
    setEnding(true);
    capture.stop();
    await endSession(sessionId);
    router.push(`/debrief/${sessionId}`);
  };

  return (
    <div>
      {/* status row */}
      <div className={cn("flex flex-wrap items-center gap-3 rounded-base border-2 border-ink p-3 sm:p-4", offRecord ? "bg-ink text-paper" : "bg-card shadow-hard")}>
        <span className="inline-flex items-center gap-2.5 text-lg font-extrabold">
          <span className={cn("size-3.5 rounded-full border-2", offRecord ? "border-paper" : "border-ink bg-missing motion-safe:animate-pulse")} aria-hidden />
          {offRecord ? t("cap.off") : t("cap.live")}
          <span className="tnum font-mono text-base font-bold opacity-80">{mmss}</span>
        </span>
        <span className="inline-flex items-center gap-2 text-base font-bold" aria-label={t("cap.asked", { n: asked, max: BUDGET })}>
          <span className="flex gap-1" aria-hidden>
            {Array.from({ length: BUDGET }).map((_, i) => (
              <span key={i} className={cn("size-3 rounded-full border-2", offRecord ? "border-paper" : "border-ink", i < asked ? "bg-claros" : "bg-transparent")} />
            ))}
          </span>
          <span className="hidden sm:inline">{t("cap.asked", { n: asked, max: BUDGET })}</span>
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          {pip.supported ? (
            <Button variant="outline" onClick={() => void pip.open({ width: 400, height: 260 })} className={cn(offRecord && "border-paper")}>
              <PictureInPicture2 aria-hidden /> <span className="hidden md:inline">{t("cap.popout")}</span>
            </Button>
          ) : null}
          {!offRecord ? (
            <>
              <Button variant="ghost" onClick={rec.strike} title={t("off.struck")}>
                {t("off.strike")}
              </Button>
              <Button variant="outline" onClick={rec.goOff}>
                <EyeOff aria-hidden /> {t("cap.off")}
              </Button>
            </>
          ) : null}
          <Button variant="primary" onClick={finish} loading={ending} className={cn(offRecord && "border-paper")}>
            {!ending ? <Square aria-hidden /> : null} {t("cap.end")}
          </Button>
        </div>
      </div>

      {offRecord ? <ResumeBar onResume={rec.resume} className="mt-4" /> : null}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <ConnectionBanner />
        <SignalsBar />
      </div>

      {/* orb + captions */}
      <div className="grid min-h-[52vh] items-center gap-10 py-12 lg:grid-cols-[auto_minmax(0,1fr)] lg:gap-20 lg:py-16">
        <div className="justify-self-center">
          <LiveOrb size={260} getLevel={() => Math.max(voice.getOutputLevel(), voice.getInputLevel() * 0.5)} speaking={voice.isSpeaking} off={offRecord} />
        </div>
        <div className="min-w-0">
          {offRecord ? <p className="text-4xl font-black tracking-[-0.03em]">{t("cap.off")}</p> : demoCaption && !caption ? <DemoCaption text={demoCaption} /> : <LiveCaptions idle={t("cap.idle")} />}
          {!offRecord ? <WhyTag why={latestWhy ?? (demoCaption ? demoWhy : null)} className="mt-6" /> : null}
        </div>
      </div>

      {/* what Claros noticed — one line each, newest first */}
      {events.length && !offRecord ? (
        <section aria-labelledby="noticed" className="border-t-2 border-ink pt-6">
          <h2 id="noticed" className="text-base font-extrabold text-ink-2">
            {t("cap.noticed")}
          </h2>
          <ol className="mt-3 space-y-2" aria-live="polite">
            {[...events]
              .reverse()
              .slice(0, 3)
              .map((e, i) => (
                <li key={e.id} className={cn("claros-enter flex items-baseline gap-3 text-xl font-semibold leading-snug", i > 0 && "text-ink-2")}>
                  <span className="tnum w-14 shrink-0 font-mono text-base">{fmtT(e.t > 1e11 ? e.t - startAt : e.t)}</span>
                  <span className="min-w-0">
                    {e.summary}
                    {e.old || e.new ? (
                      <span className="ml-2 font-mono text-base">
                        <span className="line-through decoration-2 opacity-70">{e.old}</span> → <span className="rounded-[2px] bg-claros-soft px-1 font-bold text-ink">{e.new}</span>
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
          </ol>
        </section>
      ) : null}

      <LookedUpList items={lookedUp} className="mt-10 border-t-2 border-ink pt-6" />

      <PipPortal pipWindow={pip.pipWindow}>
        <ClarosOrb
          state={orb.state}
          curiousCount={orb.curious}
          caption={caption}
          onOffRecord={toggleOff}
          onNotNow={() => sendControl("not_now")}
          onStrike={rec.strike}
          labels={orbLabels}
          getLevel={() => Math.max(voice.getOutputLevel(), voice.getInputLevel() * 0.5)}
        />
      </PipPortal>
    </div>
  );
}

function DemoCaption({ text }: { text: string }) {
  return (
    <p key={text} className="claros-enter text-balance text-3xl font-black leading-[1.12] tracking-[-0.03em] sm:text-4xl 2xl:text-5xl" aria-live="polite">
      {text}
    </p>
  );
}

/** Time since Start (live events carry server epoch ms; demo events are already relative). */
function fmtT(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
