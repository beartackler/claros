"use client";
/**
 * <ClarosCompanion voice={useClarosVoice(...)} /> — floating companion.
 * "Pop out" opens a Document PiP window (always-on-top over the shared app);
 * otherwise / unsupported it renders as a docked panel bottom-right.
 */
import { useEffect, useRef, useState } from "react";
import { PictureInPicture2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ClarosOrb, type ClarosOrbState } from "./ClarosOrb";
import { PipPortal, usePip } from "./pip";
import { useClaros } from "./store";
import { sendControl } from "./useClarosSession";
import type { ClarosVoice } from "./useClarosVoice";

/** Derive the orb state from live session state. */
export function useOrbState(): { state: ClarosOrbState; curious: number } {
  const s = useClaros();
  const [noticing, setNoticing] = useState(false);
  const lastCurious = useRef(s.curiousCount);
  const lastAskT = useRef(0);
  const lastAsk = s.serverLog.findLast?.((m) => m.type === "ask" || m.type === "intervene");
  if (lastAsk && s.serverLog[s.serverLog.length - 1] === lastAsk) lastAskT.current = Date.now();

  useEffect(() => {
    if (s.curiousCount > lastCurious.current) {
      lastCurious.current = s.curiousCount;
      setNoticing(true);
      const t = setTimeout(() => setNoticing(false), 1800);
      return () => clearTimeout(t);
    }
  }, [s.curiousCount]);

  let state: ClarosOrbState = "listening";
  if (s.offRecord) state = "off_record";
  else if (s.activity === "away") state = "away";
  else if (s.wsStatus === "closed" || s.wsStatus === "connecting" || s.voiceStatus === "connecting") state = "reconnecting";
  else if (s.agentMode === "speaking") state = Date.now() - lastAskT.current < 15_000 ? "asking" : "speaking";
  else if (noticing) state = "noticing";
  return { state, curious: s.ledger?.open ?? 0 };
}

export function ClarosCompanion({ voice, className, defaultDocked = true }: { voice?: ClarosVoice; className?: string; defaultDocked?: boolean }) {
  const { state, curious } = useOrbState();
  const caption = useClaros((s) => s.caption);
  const offRecord = useClaros((s) => s.offRecord);
  const setStore = useClaros((s) => s.set);
  const pip = usePip();
  const [docked, setDocked] = useState(defaultDocked);

  const onOffRecord = () => {
    if (voice) voice.setOffRecord(!offRecord);
    else {
      setStore({ offRecord: !offRecord });
      sendControl(offRecord ? "off_record_off" : "off_record_on");
    }
  };
  const onNotNow = () => sendControl("not_now");

  const orb = (compact = false) => (
    <ClarosOrb
      state={state}
      curiousCount={curious}
      caption={caption}
      onOffRecord={onOffRecord}
      onNotNow={onNotNow}
      getLevel={voice ? () => Math.max(voice.getOutputLevel(), voice.getInputLevel() * 0.5) : undefined}
      compact={compact}
    />
  );

  return (
    <>
      <PipPortal pipWindow={pip.pipWindow}>{orb()}</PipPortal>
      {!pip.pipWindow && docked && (
        <div className={cn("fixed right-4 bottom-4 z-40 w-[340px]", className)}>
          <div className="mb-2 flex justify-end gap-2">
            {pip.supported && (
              <Button size="xs" variant="neutral" onClick={() => void pip.open()}>
                <PictureInPicture2 /> Pop out
              </Button>
            )}
            <Button size="icon-xs" variant="neutral" aria-label="Hide companion" onClick={() => setDocked(false)}>
              <X />
            </Button>
          </div>
          {orb()}
        </div>
      )}
      {!pip.pipWindow && !docked && (
        <Button className="fixed right-4 bottom-4 z-40" onClick={() => setDocked(true)}>
          Claros
        </Button>
      )}
    </>
  );
}
