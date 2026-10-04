// The learner's last live session summary, kept in this browser for "Your last session" on Home.
export type LastSession = { workflow_id: string | null; name: string; own: string[]; practice: string[]; at: number };

const KEY = "claros.lastSession";

export function saveLastSession(s: LastSession) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked: the summary still shows on screen */
  }
}

export function readLastSession(): LastSession | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "null") as LastSession | null;
    return v && Array.isArray(v.own) && Array.isArray(v.practice) ? v : null;
  } catch {
    return null;
  }
}
