import type { Metadata } from "next";
// Self-hosted Manrope (variable, вкл. кириллицу) — шрифт не зависит от доступа
// к Google Fonts ни при сборке, ни в рантайме: файлы едут внутри npm-пакета.
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
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
