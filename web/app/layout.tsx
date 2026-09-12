import type { Metadata } from "next";
import { Inter, Inter_Tight, Literata } from "next/font/google";
import "@/styles/globals.css";
import "@/styles/recorder-theme.css";
import { Shell } from "@/components/recorder/shell";

// subsets с cyrillic обязателен: интерфейс русский. Сборка падает, если
// у семейства нет кириллического субсета, — это и есть проверка.
const inter = Inter({ subsets: ["latin", "cyrillic"], variable: "--font-inter" });
const interTight = Inter_Tight({
  subsets: ["latin", "cyrillic"],
  weight: ["500"],
  variable: "--font-inter-tight",
});
const literata = Literata({
  subsets: ["latin", "cyrillic"],
  weight: ["400"],
  style: ["normal", "italic"],
  variable: "--font-literata",
});

export const metadata: Metadata = { title: "Диктофон" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`dark ${inter.variable} ${interTight.variable} ${literata.variable}`}>
      <body className="bg-background-full text-text-primary">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
