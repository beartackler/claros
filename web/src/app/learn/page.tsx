"use client";

import { Shell } from "@/components/claros/Shell";
import { LearnerSession } from "@/components/claros/LearnerSession";

/** Focused live session (no nav). Home renders the same session with the learner's status below. */
export default function LearnPage() {
  return (
    <Shell focus={{ label: "nav.session" }}>
      <LearnerSession />
    </Shell>
  );
}
