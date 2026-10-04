"use client";

// One icon + one color per kind of knowledge, everywhere (map cards, detail panel, legend, filters).
import { BookOpen, CircleDashed, OctagonAlert, Scale, Shuffle, Split } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DictKey } from "./i18n";

export type ChipKind = "judgment" | "guardrail" | "conflict" | "unconfirmed" | "anyOrder" | "notes";

export const KIND: Record<ChipKind, { Icon: typeof Scale; cls: string; label: DictKey; legend?: DictKey }> = {
  judgment: { Icon: Scale, cls: "bg-expert text-on-fill", label: "chip.judgment", legend: "legend.judgment" },
  guardrail: { Icon: OctagonAlert, cls: "bg-ink text-paper [&_svg]:text-missing", label: "chip.guardrail", legend: "legend.guardrail" },
  conflict: { Icon: Split, cls: "bg-partial text-on-fill", label: "chip.conflict", legend: "legend.conflict" },
  unconfirmed: { Icon: CircleDashed, cls: "border-dashed bg-card text-ink", label: "chip.unconfirmed", legend: "legend.unconfirmed" },
  anyOrder: { Icon: Shuffle, cls: "bg-paper-2 text-ink", label: "map.anyOrder", legend: "legend.anyOrder" },
  notes: { Icon: BookOpen, cls: "bg-card text-ink", label: "chip.notes" },
};

/** Typed chip. With onClick it's a real button (opens the detail panel at that item). */
export function KindChip({
  kind,
  children,
  onClick,
  title,
  className,
  size = "md",
}: {
  kind: ChipKind;
  children?: React.ReactNode;
  onClick?: () => void;
  title?: string;
  className?: string;
  size?: "sm" | "md";
}) {
  const k = KIND[kind];
  const cls = cn(
    "inline-flex max-w-full items-center gap-1.5 rounded-[4px] border-2 border-ink font-bold",
    size === "sm" ? "px-1.5 py-0 text-[11px] [&_svg]:size-3" : "px-2 py-1 text-xs [&_svg]:size-3.5",
    k.cls,
    className,
  );
  const inner = (
    <>
      <k.Icon className="shrink-0" aria-hidden />
      {children ? <span className="min-w-0 truncate">{children}</span> : null}
    </>
  );
  if (!onClick)
    return (
      <span className={cls} title={title}>
        {inner}
      </span>
    );
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={title}
      className={cn(cls, "pointer-events-auto relative z-[2] shadow-hard-sm transition-[transform,box-shadow] duration-150 hover:-translate-x-px hover:-translate-y-px hover:shadow-[3px_3px_0_0_var(--ink)] active:translate-x-0.5 active:translate-y-0.5 active:shadow-none")}
    >
      {inner}
    </button>
  );
}
