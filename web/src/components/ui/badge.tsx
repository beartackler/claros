"use client"

import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva, type VariantProps } from "class-variance-authority"

import * as React from "react"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden whitespace-nowrap rounded-[4px] border-2 border-ink px-2 py-0.5 text-xs font-bold [&>svg]:pointer-events-none [&>svg]:size-3.5 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros",
  {
    variants: {
      variant: {
        default: "bg-paper text-ink",
        neutral: "bg-card text-ink",
        ink: "bg-ink text-paper",
        claros: "bg-claros text-claros-ink",
        ready: "bg-ready text-on-fill",
        partial: "hatch-partial text-ink",
        missing: "bg-missing text-on-fill",
        expert: "bg-expert text-on-fill",
        /** app / O*NET tags: data, so mono */
        tag: "bg-paper-2 font-mono text-[11px] text-ink",
        dashed: "border-dashed bg-card text-ink",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
)

function Badge({
  className,
  variant,
  render,
  ...props
}: useRender.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return useRender({
    defaultTagName: "span",
    render,
    props: mergeProps<"span">(
      {
        className: cn(badgeVariants({ variant }), className),
      },
      props,
    ),
    state: {
      slot: "badge",
      variant: variant ?? "default",
    },
  })
}

export { Badge, badgeVariants }
