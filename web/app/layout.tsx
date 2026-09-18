import type { Metadata, Viewport } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";
import "@/styles/globals.css";
import "@/styles/studio.css";
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
  // Значок на домашнем экране. В этом режиме Safari не показывает окно
  // Basic-авторизации, поэтому вход идёт через форму на сервере (server/login.py),
  // а метка в cookie живёт месяц.
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon-192.png", apple: "/apple-touch-icon.png" },
  appleWebApp: { capable: true, title: "Диктофон", statusBarStyle: "black" },
};

// Тёмный цвет адресной строки мобильного браузера: со светлым по умолчанию
// над тёмной страницей висит белая полоса. Значение — текущий канвас.
export const viewport: Viewport = { themeColor: "#191a1c" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${interTight.variable} ${jetBrainsMono.variable}`}>
      {/* Фон у body не задаётся: канвас рисует html, а фактуру корпуса — слой
          body::before под содержимым (styles/globals.css). Заливка body легла
          бы поверх фактуры и спрятала её. */}
      <body className="text-text-primary">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
