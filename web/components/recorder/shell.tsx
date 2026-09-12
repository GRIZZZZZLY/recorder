"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import {
  RiAlertLine, RiCheckboxLine, RiCloseLine, RiFolderLine, RiHome4Line,
  RiMenuLine, RiMicLine, RiPulseLine, RiQuestionLine, RiSearchLine,
} from "@remixicon/react";
import { Badge } from "@/components/base/badges/badge";
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

/**
 * Своя боковая панель вместо DashboardSidebar из BoardUI: тот безусловно
 * тянет меню команды, меню пользователя и модалку настроек (свойства их не
 * отключают) — у диктофона один владелец, команд и настроек нет.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const { data } = useApi<DigestBody>("/digest");
  const [open, setOpen] = useState(false);

  function badgeFor(key: string) {
    if (!data) return null;
    if (key === "tasks" && data.week.my_open_tasks > 0) {
      return <Badge color="primary">{data.week.my_open_tasks}</Badge>;
    }
    if (key === "review" && data.week.review > 0) {
      return <Badge color="neutral">{data.week.review}</Badge>;
    }
    return null;
  }

  const nav = (
    <nav className="flex flex-col gap-1 p-3">
      {NAV.map(({ key, label, icon: Icon, href }) => {
        const selected = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <a
            key={key}
            href={href}
            onClick={() => setOpen(false)}
            aria-current={selected ? "page" : undefined}
            className={cx(
              "flex items-center justify-between gap-2 rounded-2lg p-2 text-body-medium",
              selected
                ? "bg-linear-to-b from-accent-500 to-accent-600 text-text-white"
                : "text-text-secondary hover:bg-background-secondary-hover",
            )}
          >
            <span className="flex items-center gap-2">
              <Icon className="size-5 shrink-0" aria-hidden />
              {label}
            </span>
            {badgeFor(key)}
          </a>
        );
      })}
    </nav>
  );

  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-[260px] shrink-0 border-r border-separator-border bg-background-secondary-default lg:block">
        {nav}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-separator-border p-3 lg:hidden">
          <button
            type="button"
            aria-label="Открыть меню"
            onClick={() => setOpen(true)}
            className="rounded-lg p-1.5 text-text-secondary hover:bg-background-secondary-hover"
          >
            <RiMenuLine className="size-5" aria-hidden />
          </button>
          <span className="text-body-medium text-text-primary">Диктофон</span>
        </header>
        <main className="mx-auto w-full max-w-[1200px] px-6 py-8 sm:px-8">{children}</main>
      </div>
      {open && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <div className="relative flex w-[260px] flex-col border-r border-separator-border bg-background-secondary-default">
            <div className="flex items-center justify-between p-3">
              <span className="text-body-medium text-text-primary">Диктофон</span>
              <button type="button" aria-label="Закрыть меню" onClick={() => setOpen(false)}>
                <RiCloseLine className="size-5 text-text-secondary" aria-hidden />
              </button>
            </div>
            {nav}
          </div>
        </div>
      )}
    </div>
  );
}
