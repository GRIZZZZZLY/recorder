import type { Metadata } from "next";

// Страница гостя: поисковикам не показывать, ключ из адреса чужим сайтам не
// отдавать. Манифест приложения владельца лежит под паролем — гостю он не нужен.
export const metadata: Metadata = {
  title: "Запись разговора",
  description: "Запись разговора по ссылке",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
  manifest: null,
};

export default function SharedLayout({ children }: { children: React.ReactNode }) {
  return children;
}
