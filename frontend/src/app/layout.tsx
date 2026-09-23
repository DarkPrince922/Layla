import { ConfirmHost } from "@/components/ConfirmDialog";
import type { Metadata } from "next";
import "@fontsource-variable/manrope";
import "../styles/globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Layla",
  description: "Мультидоменная ИИ-рабочая станция — Код · Пентест · OSINT · Дизайн",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" suppressHydrationWarning>
      <body>
        <Providers>{children}<ConfirmHost /></Providers>
      </body>
    </html>
  );
}
