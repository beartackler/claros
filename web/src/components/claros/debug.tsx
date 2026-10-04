"use client";

// Hidden demo controls: only with ?debug=1 (sticks for the tab via sessionStorage; ?debug=0 turns it off).
import { useEffect, useSyncExternalStore } from "react";
import { Bug, X } from "lucide-react";
import type { Coverage } from "@/lib/contracts";
import { cn } from "@/lib/utils";
import { useUi } from "./i18n";
import { getSocket } from "@/lib/ws";
import type { ClientMsg } from "@/lib/contracts";
import { Button } from "@/components/ui/button";

export type Force = "auto" | Coverage["status"];

const state: { enabled: boolean; force: Force; open: boolean } = { enabled: false, force: "auto", open: true };
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
let snapshot = { ...state };
const get = () => snapshot;
const SERVER = { enabled: false, force: "auto" as Force, open: true };
function set(p: Partial<typeof state>) {
  Object.assign(state, p);
  snapshot = { ...state };
  try {
    sessionStorage.setItem("claros.debug", JSON.stringify({ enabled: state.enabled, force: state.force }));
  } catch {
    /* private mode */
  }
  emit();
}

function init() {
  try {
    const saved = JSON.parse(sessionStorage.getItem("claros.debug") || "{}");
    const q = new URLSearchParams(window.location.search);
    const flag = q.get("debug");
    const force = q.get("force") as Force | null;
    set({
      enabled: flag === "1" ? true : flag === "0" ? false : Boolean(saved.enabled),
      force: force && ["auto", "ready", "partial", "missing"].includes(force) ? force : saved.force ?? "auto",
    });
  } catch {
    /* ignore */
  }
}

export function useDebug() {
  const s = useSyncExternalStore(subscribe, get, () => SERVER);
  return { ...s, setForce: (force: Force) => set({ force }), setOpen: (open: boolean) => set({ open }) };
}

export function DebugPanel() {
  const { t } = useUi();
  const d = useDebug();
  useEffect(init, []);
  // smoke tests: push fixture messages (keyframes…) through this tab's own session socket
  useEffect(() => {
    if (!d.enabled) return;
    (window as unknown as { __clarosSend?: (m: ClientMsg) => void }).__clarosSend = (m) => getSocket()?.send(m);
  }, [d.enabled]);
  if (!d.enabled) return null;
  if (!d.open)
    return (
      <button
        type="button"
        onClick={() => d.setOpen(true)}
        aria-label={t("debug.title")}
        className="fixed bottom-4 left-4 z-40 grid size-10 place-items-center rounded-full border-2 border-dashed border-ink bg-card text-ink shadow-hard-sm"
      >
        <Bug className="size-4" aria-hidden />
      </button>
    );
  return (
    <aside aria-label={t("debug.title")} className="fixed bottom-4 left-4 z-40 w-[min(300px,calc(100vw-2rem))] rounded-base border-2 border-dashed border-ink bg-card p-3 text-ink shadow-hard">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-sm font-extrabold">
          <Bug className="size-4" aria-hidden /> {t("debug.title")}
        </p>
        <Button variant="ghost" size="icon-xs" onClick={() => d.setOpen(false)} aria-label={t("common.close")}>
          <X aria-hidden />
        </Button>
      </div>
      <fieldset className="mt-2">
        <legend className="mb-1.5 text-xs font-semibold text-ink-2">{t("debug.force")}</legend>
        <div className="flex flex-wrap gap-1.5">
          {(["auto", "ready", "partial", "missing"] as Force[]).map((f) => (
            <Button key={f} variant="outline" size="xs" aria-pressed={d.force === f} onClick={() => d.setForce(f)} className={cn("font-mono", d.force === f && "bg-ink text-paper")}>
              {f === "auto" ? t("debug.auto") : f}
            </Button>
          ))}
        </div>
      </fieldset>
      <p className="mt-2 font-mono text-[11px] text-ink-2">{t("debug.hint")}</p>
    </aside>
  );
}
