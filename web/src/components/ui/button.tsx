import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"
import { Loader2 } from "lucide-react"

import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Claros button hierarchy (neobrutalism.dev base, extended):
 *  primary     ink slab — the one main action of a section
 *  claros      violet — summons Claros (walk me through, answer by voice). Nothing else.
 *  secondary   white card + hard shadow — alternative actions
 *  outline     bordered, flat — tertiary / toolbar
 *  ghost       borderless until hover — low-emphasis (Later, Cancel)
 *  destructive coral — end / discard
 *  link        inline text action
 * Press = the slab sinks into its shadow. Focus = violet ring. Disabled = flat + 45%.
 */
const buttonVariants = cva(
  [
    "relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-base text-sm font-bold",
    "transition-[transform,box-shadow,background-color,color] duration-150 ease-out",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
    "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros",
    "disabled:pointer-events-none disabled:opacity-45 disabled:shadow-none data-disabled:pointer-events-none data-disabled:opacity-45 data-disabled:shadow-none",
    "aria-busy:cursor-progress",
  ],
  {
    variants: {
      variant: {
        primary:
          "border-2 border-ink bg-ink text-paper shadow-[3px_3px_0_0_var(--claros)] hover:-translate-x-px hover:-translate-y-px hover:shadow-[4px_4px_0_0_var(--claros)] active:translate-x-[3px] active:translate-y-[3px] active:shadow-none",
        claros:
          "border-2 border-ink bg-claros text-claros-ink shadow-hard hover:-translate-x-px hover:-translate-y-px hover:shadow-[5px_5px_0_0_var(--ink)] active:translate-x-1 active:translate-y-1 active:shadow-none",
        secondary:
          "border-2 border-ink bg-card text-ink shadow-hard hover:-translate-x-px hover:-translate-y-px hover:shadow-[5px_5px_0_0_var(--ink)] active:translate-x-1 active:translate-y-1 active:shadow-none",
        outline:
          "border-2 border-ink bg-card text-ink hover:bg-paper-2 active:translate-y-px",
        ghost:
          "border-2 border-transparent text-ink hover:border-ink hover:bg-card active:translate-y-px",
        destructive:
          "border-2 border-ink bg-missing text-on-fill shadow-hard hover:-translate-x-px hover:-translate-y-px hover:shadow-[5px_5px_0_0_var(--ink)] active:translate-x-1 active:translate-y-1 active:shadow-none",
        link: "h-auto! border-0 px-0! text-ink underline decoration-2 underline-offset-4 hover:decoration-claros",
        // legacy names used by voice/ and /dev (neobrutalism defaults)
        default:
          "border-2 border-ink bg-ink text-paper shadow-hard hover:translate-x-boxShadowX hover:translate-y-boxShadowY hover:shadow-none",
        neutral:
          "border-2 border-ink bg-card text-ink shadow-hard hover:translate-x-boxShadowX hover:translate-y-boxShadowY hover:shadow-none",
        noShadow: "border-2 border-ink bg-ink text-paper",
        reverse:
          "border-2 border-ink bg-ink text-paper hover:translate-x-reverseBoxShadowX hover:translate-y-reverseBoxShadowY hover:shadow-shadow",
      },
      size: {
        default: "h-10 px-4",
        xs: "h-8 gap-1.5 px-2.5 text-xs [&_svg]:size-3.5",
        sm: "h-9 px-3",
        lg: "h-12 px-6 text-base [&_svg]:size-5",
        xl: "h-14 px-7 text-lg font-black [&_svg]:size-5",
        icon: "size-10",
        "icon-xs": "size-8 [&_svg]:size-3.5",
        "icon-sm": "size-9",
        "icon-lg": "size-11",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "default",
    },
  },
)

type ButtonProps = React.ComponentProps<typeof ButtonPrimitive> &
  VariantProps<typeof buttonVariants> & {
    /** shows a spinner, keeps the width, blocks clicks */
    loading?: boolean
  }

function Button({ className, variant, size, loading, disabled, children, ...props }: ButtonProps) {
  return (
    <ButtonPrimitive
      data-slot="button"
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    >
      {loading ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
      {children}
    </ButtonPrimitive>
  )
}

export { Button, buttonVariants }
