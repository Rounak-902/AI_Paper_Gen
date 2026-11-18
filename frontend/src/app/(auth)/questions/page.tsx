import { redirect } from "next/navigation";

export default function LegacyQuestionNotificationDestination() {
  redirect("/question-bank?status=draft");
}
