import * as React from "react"

import { cn } from "@/lib/utils"

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-12 w-full rounded-base border-2 border-ink bg-card shadow-hard-sm selection:bg-claros selection:text-claros-ink px-4 py-2 text-lg font-base text-foreground file:border-0 file:bg-transparent file:text-sm file:font-heading placeholder:text-ink-2 focus-visible:outline-hidden focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-claros disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  )
}

export { Input }
