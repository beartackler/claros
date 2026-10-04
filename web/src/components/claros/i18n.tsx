"use client";

// Dictionary i18n + UI prefs (language, role, theme). Dictionaries live in ./dict.ts.
import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import { PLURALS, de, en, es, fr, ru, type Dict, type DictKey, type PluralKey } from "./dict";

export type { DictKey };
export const LANGS = ["en", "de", "fr", "es", "ru"] as const;
export type UiLang = (typeof LANGS)[number];
export const LANG_NAMES: Record<UiLang, string> = { en: "English", de: "Deutsch", fr: "Français", es: "Español", ru: "Русский" };

const DICTS: Record<UiLang, Dict> = { en, de, fr, es, ru };

export type Role = "learner" | "expert";

type Ctx = {
  lang: UiLang;
  setLang: (l: UiLang) => void;
  role: Role;
  setRole: (r: Role) => void;
  /** judge / demo mode: show "How Claros decided" evidence (off by default) */
  evidence: boolean;
  setEvidence: (on: boolean) => void;
  t: (k: DictKey, vars?: Record<string, string | number>) => string;
  /** plural-aware count label ("1 frame", "5 кадров") */
  tn: (k: PluralKey, n: number) => string;
};

const UiCtx = createContext<Ctx | null>(null);

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = window.localStorage.getItem(key);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, v: string) {
  try {
    window.localStorage.setItem(key, v);
  } catch {
    /* storage blocked: preference lives for this page only */
  }
}

export function translate(lang: UiLang, k: DictKey, vars?: Record<string, string | number>) {
  let s = DICTS[lang][k] ?? en[k] ?? k;
  if (vars) for (const [name, v] of Object.entries(vars)) s = s.replaceAll(`{${name}}`, String(v));
  return s;
}

// Tiny external store so prefs survive client navigation without flicker and hydrate safely.
const listeners = new Set<() => void>();
const prefs: { lang: UiLang; role: Role; evidence: "on" | "off"; loaded: boolean } = { lang: "en", role: "learner", evidence: "off", loaded: false };
function loadPrefs() {
  if (prefs.loaded || typeof window === "undefined") return;
  prefs.loaded = true;
  prefs.lang = read("claros.lang", LANGS, "en");
  prefs.role = read("claros.role", ["learner", "expert"] as const, "learner");
  prefs.evidence = read("claros.evidence", ["on", "off"] as const, "off");
  try {
    const j = new URLSearchParams(window.location.search).get("judge");
    if (j === "1" || j === "0") {
      prefs.evidence = j === "1" ? "on" : "off";
      write("claros.evidence", prefs.evidence);
    }
  } catch {
    /* no window */
  }
}
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const emit = () => listeners.forEach((l) => l());
const getLang = () => (loadPrefs(), prefs.lang);
const getRole = () => (loadPrefs(), prefs.role);
const getEvidence = () => (loadPrefs(), prefs.evidence);

export function UiProvider({ children }: { children: React.ReactNode }) {
  const lang = useSyncExternalStore(subscribe, getLang, () => "en" as UiLang);
  const role = useSyncExternalStore(subscribe, getRole, () => "learner" as Role);
  const evidence = useSyncExternalStore(subscribe, getEvidence, () => "off" as const) === "on";

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((l: UiLang) => {
    prefs.lang = l;
    write("claros.lang", l);
    emit();
  }, []);
  const setRole = useCallback((r: Role) => {
    prefs.role = r;
    write("claros.role", r);
    emit();
  }, []);
  const setEvidence = useCallback((on: boolean) => {
    prefs.evidence = on ? "on" : "off";
    write("claros.evidence", prefs.evidence);
    emit();
  }, []);
  const t = useCallback((k: DictKey, vars?: Record<string, string | number>) => translate(lang, k, vars), [lang]);

  const tn = useCallback((k: PluralKey, n: number) => {
    const cat = new Intl.PluralRules(lang).select(n);
    const forms = PLURALS[lang]?.[k] ?? PLURALS.en[k]!;
    return (forms[cat] ?? forms.other ?? PLURALS.en[k]!.other!).replaceAll("{n}", String(n));
  }, [lang]);

  const value = useMemo(() => ({ lang, setLang, role, setRole, evidence, setEvidence, t, tn }), [lang, setLang, role, setRole, evidence, setEvidence, t, tn]);
  return <UiCtx.Provider value={value}>{children}</UiCtx.Provider>;
}

export function useUi() {
  const c = useContext(UiCtx);
  if (!c) throw new Error("useUi must be used inside <UiProvider>");
  return c;
}
export const useT = () => useUi().t;

export function timeAgo(t: (k: DictKey, v?: Record<string, string | number>) => string, ts: number) {
  if (ts < 1e12) ts *= 1000; // server sends epoch seconds
  const d = Math.max(0, Date.now() - ts) / 60_000;
  if (d < 1) return t("time.justNow");
  if (d < 60) return t("time.min", { n: Math.round(d) });
  if (d < 60 * 24) return t("time.hour", { n: Math.round(d / 60) });
  return t("time.day", { n: Math.round(d / 1440) });
}
