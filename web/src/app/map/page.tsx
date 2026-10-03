"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Shell } from "@/components/claros/Shell";
import { Loading } from "@/components/claros/primitives";
import { latestWorkflowId } from "@/lib/api";

/** /map → the most recent workflow's Work Map. */
export default function MapIndex() {
  const router = useRouter();
  useEffect(() => {
    let alive = true;
    latestWorkflowId().then((id) => alive && router.replace(`/map/${encodeURIComponent(id)}`));
    return () => {
      alive = false;
    };
  }, [router]);
  return (
    <Shell>
      <Loading rows={2} />
    </Shell>
  );
}
