"use client";
import { ConversationProvider } from "@elevenlabs/react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toast";

/** App-wide providers. ElevenLabs React 1.x requires <ConversationProvider>. */
export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ConversationProvider>
      <TooltipProvider>
        <Toaster>{children}</Toaster>
      </TooltipProvider>
    </ConversationProvider>
  );
}
