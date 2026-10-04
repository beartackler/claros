"use client";

// How an open question reads: conflicts are neutral unless the viewer is one of the experts in it.
import type { User, WorkMap } from "@/lib/contracts";
import { Badge } from "@/components/ui/badge";
import { useUi } from "./i18n";
import { expertById, type OpenQuestion } from "./mapUtils";

/** Server copy is authored in English; flag it when the UI is in another language. */
const looksEnglish = (s: string) => /[a-z]{3}/i.test(s) && !/[Ѐ-ӿ]/.test(s);

export function questionText(map: WorkMap, q: OpenQuestion, viewer: User | null, neutral: (positions: string) => string) {
  if (q.kind === "unknown") return q.unknown.spoken_question ?? q.unknown.hypothesis ?? "";
  const mine = viewer && q.step.experts.includes(viewer.id);
  if (mine) {
    // the server phrases each side as "<other> does X, you do Y" — pick the one addressed to the viewer
    const addressed = q.unknowns.find((u) => u.spoken_question && !u.spoken_question.startsWith(viewer.name));
    if (addressed?.spoken_question) return addressed.spoken_question;
  }
  return neutral(q.positions.map((p) => `${expertById(map, p.expert_id).name}: ${p.description}`).join("; "));
}

export function QuestionLine({ map, q, viewer, className }: { map: WorkMap; q: OpenQuestion; viewer: User | null; className?: string }) {
  const { t, lang } = useUi();
  const text = questionText(map, q, viewer, (positions) => t("map.conflict.neutral", { positions }));
  // the neutral template is translated; the data inside it (and server questions) may not be
  const data = q.kind === "unknown" ? text : q.positions.map((p) => p.description).join(" ");
  return (
    <p className={className}>
      {text}
      {lang !== "en" && looksEnglish(data) ? (
        <Badge variant="dashed" className="ml-2 align-middle font-mono text-[10px]" title={t("q.original.hint")}>
          {t("q.original")} · EN
        </Badge>
      ) : null}
    </p>
  );
}
