import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"
import { Loader2 } from "lucide-react"

import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Claros button hierarchy (neobrutalism.dev default mechanics):
 *  primary     ink slab, violet shadow — the one main action of a screen
 *  claros      violet — summons Claros (start, answer by voice). Nothing else.
 *  secondary   white card + hard shadow — alternative actions
 *  outline     white card + small shadow — tertiary / toolbar / menus
 *  ghost       borderless until hover — low-emphasis (Later, Cancel)
 *  destructive coral — end / discard
 *  link        inline text action
 * Every slab presses DOWN into its shadow on hover and press (`.press`, globals.css) — never lifts.
 * Open menus / toggled-on buttons stay pressed in. Focus = violet ring. Disabled = flat + 45%.
 */
const buttonVariants = cva(
  [
    "relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-base text-base font-bold",
    "[&_svg]:pointer-events-none [&_svg]:size-5 [&_svg]:shrink-0",
    "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros",
    "disabled:pointer-events-none disabled:opacity-45 disabled:shadow-none data-disabled:pointer-events-none data-disabled:opacity-45 data-disabled:shadow-none",
    "aria-busy:cursor-progress",
  ],
  {
    variants: {
      variant: {
        primary: "press border-2 border-ink bg-ink text-paper shadow-[4px_4px_0_0_var(--claros)]",
        claros: "press border-2 border-ink bg-claros text-claros-ink shadow-hard",
        secondary: "press border-2 border-ink bg-card text-ink shadow-hard",
        outline: "press press-sm border-2 border-ink bg-card text-ink shadow-hard-sm",
        ghost: "border-2 border-transparent text-ink transition-colors hover:border-ink hover:bg-card",
        destructive: "press border-2 border-ink bg-missing text-on-fill shadow-hard",
        link: "h-auto! border-0 px-0! text-ink underline decoration-2 underline-offset-4 hover:decoration-claros",
        // legacy names used by voice/ and /dev (neobrutalism defaults)
        default: "press border-2 border-ink bg-ink text-paper shadow-hard",
        neutral: "press border-2 border-ink bg-card text-ink shadow-hard",
        noShadow: "border-2 border-ink bg-ink text-paper",
        reverse: "press border-2 border-ink bg-ink text-paper shadow-hard",
      },
      size: {
        default: "h-12 px-5",
        xs: "h-9 gap-1.5 px-3 text-sm [&_svg]:size-4",
        sm: "h-10 px-3.5 text-sm [&_svg]:size-4",
        lg: "h-14 px-6 text-lg [&_svg]:size-5",
        xl: "h-16 px-8 text-xl font-black [&_svg]:size-6",
        icon: "size-12",
        "icon-xs": "size-9 [&_svg]:size-4",
        "icon-sm": "size-10 [&_svg]:size-4",
        "icon-lg": "size-14",
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
