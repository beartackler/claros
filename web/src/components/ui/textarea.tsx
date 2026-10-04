import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-[80px] w-full rounded-base border-2 border-border bg-secondary-background selection:bg-claros selection:text-claros-ink px-3 py-2 text-sm font-base text-foreground placeholder:text-ink-2 focus-visible:outline-none focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-red-500",
        className,
      )}
      {...props}
    />
  )
}

export { Textarea }
