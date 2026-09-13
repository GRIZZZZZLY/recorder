import Link from "next/link";
import type { Metadata } from "next";
import { PageTitle } from "@/components/recorder/eyebrow";

// Без этого файла статический экспорт кладёт в out/404.html стандартную
// страницу Next: белый фон, английский текст и ни одной ссылки обратно.
// Отдаёт её server/static_site.py, когда путь не нашёлся.
export const metadata: Metadata = { title: "Страница не найдена" };

const WAYS = [
  { href: "/", title: "Сводка дня", hint: "Записи, задачи и спорные места за сегодня" },
  { href: "/records/", title: "Все записи", hint: "Список сессий с диктофона" },
  { href: "/search/", title: "Поиск", hint: "Найти по сказанному" },
];

export default function NotFound() {
  return (
    <>
      <PageTitle eyebrow="ДИКТОФОН · 404" plain="Такой страницы" italic="нет" />
      <p className="max-w-[65ch] text-body-regular text-text-secondary">
        Ссылка ведёт в никуда: адрес набран с ошибкой либо страница переехала.
        Записи и задачи на месте — начните с одного из разделов.
      </p>
      <ul className="mt-6 flex flex-col gap-3">
        {WAYS.map((way) => (
          <li key={way.href}>
            <Link
              href={way.href}
              className="recorder-press block rounded-2xl border border-separator-border bg-background-primary-default p-6 hover:bg-background-primary-hover"
            >
              <p className="text-headline-medium text-text-primary">{way.title}</p>
              <p className="mt-1 text-body-2-regular text-text-tertiary">{way.hint}</p>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
