import Link from "next/link";
import type { Metadata } from "next";
import { House, MagnifyingGlass, Waveform } from "@phosphor-icons/react/dist/ssr";

// Без этого файла статический экспорт кладёт в out/404.html стандартную
// страницу Next: белый фон, английский текст и ни одной ссылки обратно.
// Отдаёт её server/static_site.py, когда путь не нашёлся.
export const metadata: Metadata = { title: "Страница не найдена" };

const WAYS = [
  { href: "/", title: "Сводка", icon: House },
  { href: "/records/", title: "Записи", icon: Waveform },
  { href: "/search/", title: "Поиск", icon: MagnifyingGlass },
];

export default function NotFound() {
  return (
    <div className="flex min-h-[60dvh] items-center">
      <div className="flex max-w-[460px] flex-col gap-5">
        <span className="font-mono text-note text-text-tertiary">404</span>
        <h1 className="text-page-sm text-text-primary sm:text-page">Такой страницы нет</h1>
        <p className="text-[15px] leading-normal text-text-secondary">
          Сессии с таким номером не было, или ссылка устарела. Записи никуда не делись.
        </p>
        <div className="flex flex-wrap gap-2 pt-1">
          {WAYS.map(({ href, title, icon: Glyph }) => (
            <Link
              key={href}
              href={href}
              className="press flex items-center gap-2 rounded-inner bg-hairline px-3.5 py-2 text-row hover:bg-hairline-strong"
            >
              <Glyph size={16} aria-hidden />
              {title}
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
