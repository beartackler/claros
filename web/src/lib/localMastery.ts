// Learner's own runs, kept in this browser so "Continue learning" works even when the server has none yet.
import type { MasteryNode } from "@/lib/contracts";

type Levels = Record<string, MasteryNode["level"]>;
const key = (wf: string) => `claros.mastery.${wf}`;

function readLocalMastery(wf: string): Levels {
  try {
    return JSON.parse(localStorage.getItem(key(wf)) || "{}") as Levels;
  } catch {
    return {};
  }
}

export function writeLocalMastery(wf: string, levels: Levels) {
  try {
    const prev = readLocalMastery(wf);
    localStorage.setItem(key(wf), JSON.stringify({ ...prev, ...levels }));
  } catch {
    /* storage blocked */
  }
}

/** Server nodes win when present; otherwise this browser's runs. */
