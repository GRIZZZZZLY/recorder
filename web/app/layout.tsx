import type { Metadata, Viewport } from "next";
import { Inter, Literata } from "next/font/google";
import "@/styles/globals.css";
import { Shell } from "@/components/recorder/shell";

// subsets с cyrillic обязателен: интерфейс русский. Сборка падает, если
// у семейства нет кириллического субсета, — это и есть проверка.
const inter = Inter({ subsets: ["latin", "cyrillic"], variable: "--font-inter" });
const literata = Literata({
  subsets: ["latin", "cyrillic"],
  weight: ["400"],
  style: ["normal", "italic"],
  variable: "--font-literata",
});

export const metadata: Metadata = {
  title: { default: "Диктофон", template: "%s · Диктофон" },
  description: "Разговоры с диктофона: саммари, задачи, вопросы и поиск по сказанному.",
};

// Тёмный цвет адресной строки мобильного браузера: со светлым по умолчанию
// над тёмной страницей висит белая полоса.
export const viewport: Viewport = { themeColor: "#080e33" };

// Класса dark на <html> нет: тема одна, значения стоят прямо в @theme
// (styles/globals.css). Классом её переключал BoardUI, у которого была ещё и
// светлая.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${inter.variable} ${literata.variable}`}>
      <body className="bg-background-full text-text-primary">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
