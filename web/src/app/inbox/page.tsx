import { redirect } from "next/navigation";

/** The learner-request queue now lives on the expert's home. */
export default function InboxPage() {
  redirect("/");
}
