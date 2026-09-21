import { redirect } from "next/navigation";

export default function Home() {
  // Default domain landing.
  redirect("/code");
}
