"use client";

import { useRef } from "react";
import { usePathname } from "next/navigation";
import {
  RiAlertLine, RiCheckboxLine, RiCloseLine, RiFolderLine, RiHome4Line,
  RiMenuLine, RiMicLine, RiPulseLine, RiQuestionLine, RiSearchLine,
} from "@remixicon/react";
import { useApi } from "@/lib/use-api";
import type { DigestBody } from "@/lib/api";
import { cx } from "@/utils/cx";

const NAV = [
  { key: "digest", label: "Сводка", icon: RiHome4Line, href: "/" },
  { key: "records", label: "Записи", icon: RiMicLine, href: "/records/" },
  { key: "tasks", label: "Задачи", icon: RiCheckboxLine, href: "/tasks/" },
  { key: "questions", label: "Вопросы", icon: RiQuestionLine, href: "/questions/" },
  { key: "projects", label: "Проекты", icon: RiFolderLine, href: "/projects/" },
  { key: "review", label: "Спорные", icon: RiAlertLine, href: "/review/" },
  { key: "search", label: "Поиск", icon: RiSearchLine, href: "/search/" },
  { key: "status", label: "Состояние", icon: RiPulseLine, href: "/status/" },
] as const;

/** Счётчик у пункта меню. Раньше — Badge из BoardUI, теперь один span. */
function Count({ tone, children }: { tone: "accent" | "neutral"; children: React.ReactNode }) {
  return (
    <span className={cx(
      "nums rounded-full px-2 py-0.5 text-caption-1-medium",
      tone === "accent"
        ? "bg-accent-500 text-text-white"
        : "bg-background-secondary-hover text-text-secondary",
    )}>
      {children}
    </span>
  );
}

/**
 * Своя боковая панель: у диктофона один владелец, команд и настроек нет.
 *
 * Выдвижная панель на узком экране — нативный <dialog> с showModal(). Он сам
 * даёт фокус-ловушку, закрытие по Escape, inert для фона и ::backdrop; ради
 * этого раньше стоял react-aria-components, целая библиотека на одно меню.
 * Состояние держит сам элемент, поэтому здесь нет useState — только ref.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const { data } = useApi<DigestBody>("/digest");
  const menu = useRef<HTMLDialogElement>(null);

  function isSelected(href: string) {
    return href === "/" ? pathname === "/" : pathname.startsWith(href);
  }

  // Заголовок узкого экрана: пока меню закрыто, название раздела — единственный
  // указатель, где владелец находится.
  const current = NAV.find((item) => isSelected(item.href));

  function countFor(key: string) {
    if (!data) return null;
    if (key === "tasks" && data.week.my_open_tasks > 0) {
      return <Count tone="accent">{data.week.my_open_tasks}</Count>;
    }
    if (key === "review" && data.week.review > 0) {
      return <Count tone="neutral">{data.week.review}</Count>;
    }
    return null;
  }

  const nav = (
    <nav className="flex flex-col gap-1 p-3">
      {NAV.map(({ key, label, icon: Icon, href }) => {
        const selected = isSelected(href);
        return (
          <a
            key={key}
            href={href}
            onClick={() => menu.current?.close()}
            aria-current={selected ? "page" : undefined}
            className={cx(
              "recorder-press flex items-center justify-between gap-2 rounded-2lg p-2 text-body-medium",
              selected
                ? "bg-linear-to-b from-accent-500 to-accent-600 text-text-white"
                : "text-text-secondary hover:bg-background-secondary-hover",
            )}
          >
            <span className="flex items-center gap-2">
              <Icon className="size-5 shrink-0" aria-hidden />
              {label}
            </span>
            {countFor(key)}
          </a>
        );
      })}
    </nav>
  );

  return (
    <div className="flex min-h-dvh">
      {/* Первая цель Tab: с клавиатуры иначе приходится проходить все восемь
          пунктов меню на каждой странице. Виден только в фокусе. */}
      <a
        href="#content"
        className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-3 focus-visible:left-3 focus-visible:z-60 focus-visible:rounded-2lg focus-visible:bg-background-secondary-default focus-visible:px-4 focus-visible:py-2 focus-visible:text-body-medium focus-visible:text-text-primary"
      >
        К содержимому
      </a>
      <aside className="hidden w-[260px] shrink-0 border-r border-separator-border bg-background-secondary-default lg:block">
        {nav}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-separator-border p-3 lg:hidden">
          <button
            type="button"
            aria-label="Открыть меню"
            onClick={() => menu.current?.showModal()}
            className="recorder-press rounded-lg p-1.5 text-text-secondary hover:bg-background-secondary-hover"
          >
            <RiMenuLine className="size-5" aria-hidden />
          </button>
          <span className="text-body-medium text-text-primary">{current?.label ?? "Диктофон"}</span>
        </header>
        <main id="content" className="mx-auto w-full max-w-[1200px] px-6 py-8 sm:px-8">
          {children}
        </main>
      </div>
      <dialog
        ref={menu}
        aria-label="Меню"
        // Клик мимо панели приходит на сам элемент dialog (::backdrop — его
        // псевдоэлемент), клик по содержимому — на потомка. Так закрытие по
        // фону обходится без слушателя на документе.
        onClick={(event) => { if (event.target === menu.current) menu.current?.close(); }}
        className="m-0 h-dvh max-h-none w-[260px] max-w-none border-r border-separator-border bg-background-secondary-default p-0 text-text-primary backdrop:bg-black/50 lg:hidden"
      >
        <div className="flex items-center justify-between p-3">
          <span className="text-body-medium text-text-primary">Диктофон</span>
          <button
            type="button"
            aria-label="Закрыть меню"
            onClick={() => menu.current?.close()}
            className="recorder-press rounded-lg p-1.5 text-text-secondary hover:bg-background-secondary-hover"
          >
            <RiCloseLine className="size-5" aria-hidden />
          </button>
        </div>
        {nav}
      </dialog>
    </div>
  );
}
