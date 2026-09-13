import type { Metadata, Viewport } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";
import "@/styles/globals.css";
import { Shell } from "@/components/recorder/shell";

// subsets с cyrillic обязателен: интерфейс русский. Сборка падает, если
// у семейства нет кириллического субсета, — это и есть проверка.
const interTight = Inter_Tight({
  subsets: ["latin", "cyrillic"],
  variable: "--font-inter-tight",
});

// Моноширинный только для времени и номеров сессий: 00:12:34, 0042.
const jetBrainsMono = JetBrains_Mono({
  subsets: ["latin", "cyrillic"],
  weight: ["400", "500"],
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: { default: "Диктофон", template: "%s · Диктофон" },
  description: "Разговоры с диктофона: саммари, задачи, вопросы и поиск по сказанному.",
};

// Тёмный цвет адресной строки мобильного браузера: со светлым по умолчанию
// над тёмной страницей висит белая полоса.
export const viewport: Viewport = { themeColor: "#0d0e11" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${interTight.variable} ${jetBrainsMono.variable}`}>
      <body className="bg-canvas text-text-primary">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
