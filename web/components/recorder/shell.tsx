"use client";

import { useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  CaretRight, CheckSquare, DotsThreeCircle, Folders, House, MagnifyingGlass,
  Pulse, Question, Waveform as WaveformIcon, WarningCircle, X,
} from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react";
import { useApi } from "@/lib/use-api";
import type { DigestBody } from "@/lib/api";
import { isLive, useNow } from "@/lib/use-now";
import { cx } from "@/utils/cx";

type Item = { key: string; label: string; short?: string; href: string; icon: Icon };

const NAV: Item[] = [
  { key: "digest", label: "Сводка", href: "/", icon: House },
  { key: "records", label: "Записи", href: "/records/", icon: WaveformIcon },
  { key: "tasks", label: "Задачи", href: "/tasks/", icon: CheckSquare },
  { key: "questions", label: "Вопросы без ответа", short: "Вопросы", href: "/questions/", icon: Question },
  { key: "projects", label: "Проекты", href: "/projects/", icon: Folders },
  { key: "review", label: "Спорные места", short: "Спорные", href: "/review/", icon: WarningCircle },
  { key: "search", label: "Поиск", href: "/search/", icon: MagnifyingGlass },
  { key: "status", label: "Состояние", href: "/status/", icon: Pulse },
];

// Нижняя панель телефона вмещает пять целей; остальные разделы уходят в лист
// «Ещё» — так же, как это делают почта и заметки на iOS.
const TABS = ["digest", "records", "tasks", "search"];
const SHEET = ["questions", "projects", "review", "status"];

/**
 * Иконка раздела: контур и заливка друг на друге, активная — кроссфейд 120 мс,
 * а не подмена глифа. `pop` — короткий пружинный «клик» при выборе вкладки.
 */
function NavGlyph({ icon: Glyph, size, active, pop }: {
  icon: Icon;
  size: number;
  active: boolean;
  pop?: boolean;
}) {
  return (
    <span aria-hidden className={cx("relative flex shrink-0 items-center justify-center", active && pop && "tab-pop")}
          style={{ width: size, height: size }}>
      <Glyph size={size} className={cx("absolute transition-opacity duration-120", active ? "opacity-0" : "opacity-100")} />
      <Glyph size={size} weight="fill" className={cx("absolute transition-opacity duration-120", active ? "opacity-100" : "opacity-0")} />
    </span>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const { data } = useApi<DigestBody>("/digest");
  const now = useNow();
  const sheet = useRef<HTMLDialogElement>(null);

  function selected(href: string) {
    return href === "/" ? pathname === "/" : pathname.startsWith(href);
  }

  /** Число у пункта меню; у «Состояния» вместо числа — точка светофора. */
  function mark(key: string) {
    if (!data) return null;
    const counts = {
      tasks: data.week.my_open_tasks,
      questions: data.week.open_questions,
      review: data.week.review,
    }[key];
    if (counts !== undefined) {
      return counts > 0 ? <span className="text-micro text-text-tertiary">{counts}</span> : null;
    }
    if (key === "status") {
      const broken = data.week.failed_parts + data.week.failed_summaries;
      if (broken > 0) return <span className="size-1.5 rounded-chip bg-accent shadow-glow-accent-soft" />;
      if (data.week.sessions > 0 && data.day.sessions === 0) return null;
      return null;
    }
    return null;
  }

  const live = isLive(data?.recent?.[0]?.last_at, now);

  return (
    <div className="flex min-h-dvh">
      {/* Первая цель Tab: иначе с клавиатуры каждый раз приходится проходить
          всё меню. Виден только в фокусе. */}
      <a
        href="#content"
        className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:top-3 focus-visible:left-3 focus-visible:z-60 focus-visible:rounded-inner focus-visible:bg-surface-elevated focus-visible:px-4 focus-visible:py-2 focus-visible:text-row"
      >
        К содержимому
      </a>

      <aside className="hidden w-[220px] shrink-0 flex-col gap-0.5 border-r border-hairline px-3 py-5 lg:flex">
        <div className="flex items-center gap-2.5 px-2.5 pt-1.5 pb-5">
          {/* Светодиод записи: горит и дышит, пока диктофон пишет; иначе погашен,
              а не горит ровно — ровный красный читался бы как «пишет». */}
          <span className={cx("size-2 rounded-chip shadow-lens", live ? "bg-accent live-dot" : "bg-accent/35")}
                title={live ? "диктофон пишет" : "диктофон молчит"} />
          <span className="text-row font-semibold">Диктофон</span>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ key, label, short, href, icon: Glyph }) => {
            const current = selected(href);
            return (
              <Link
                key={key}
                href={href}
                aria-current={current ? "page" : undefined}
                className={cx(
                  "press flex items-center gap-2.5 rounded-inner px-2.5 py-2 text-row",
                  current
                    ? "bg-hairline text-text-primary"
                    : "text-text-secondary hover:bg-white/4 hover:text-text-primary",
                )}
              >
                <NavGlyph icon={Glyph} size={20} active={current} />
                <span className="flex-1">{short ?? label}</span>
                {mark(key)}
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <main
          id="content"
          className="mx-auto flex w-full max-w-[1200px] flex-col gap-7 px-4 pt-8 pb-28 sm:px-8 lg:px-12 lg:pt-10 lg:pb-14"
        >
          {children}
        </main>
      </div>

      {/* Нижняя панель телефона. */}
      <nav aria-label="Разделы"
           className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-hairline bg-surface/90 px-2 pt-2.5 pb-[max(1rem,env(safe-area-inset-bottom))] backdrop-blur-xl lg:hidden">
        {/* Светодиод записи на кромке панели — как на корпусе прибора: виден с
            любой страницы. Тот же, что у слова «Диктофон» в боковой панели
            десктопа; состояние словами — в плашке «Диктофон» на сводке. */}
        <span aria-hidden
              className={cx("absolute -top-1 right-5 size-2 rounded-chip shadow-lens",
                            live ? "bg-accent live-dot" : "bg-accent/35")} />
        {TABS.map((key) => {
          const item = NAV.find((entry) => entry.key === key)!;
          const current = selected(item.href);
          const Glyph = item.icon;
          return (
            <Link
              key={key}
              href={item.href}
              aria-current={current ? "page" : undefined}
              className={cx(
                "press flex flex-col items-center gap-1 rounded-inner py-1 text-[11px]",
                current ? "text-text-primary" : "text-text-tertiary",
              )}
            >
              <NavGlyph icon={Glyph} size={22} active={current} pop />
              {item.short ?? item.label}
            </Link>
          );
        })}
        <button
          type="button"
          onClick={() => sheet.current?.showModal()}
          className={cx(
            "press flex flex-col items-center gap-1 rounded-inner py-1 text-[11px]",
            SHEET.some((key) => selected(NAV.find((entry) => entry.key === key)!.href))
              ? "text-text-primary"
              : "text-text-tertiary",
          )}
        >
          <DotsThreeCircle size={22} aria-hidden />
          Ещё
        </button>
      </nav>

      {/* Лист «Ещё»: нативный <dialog> даёт фокус-ловушку, Escape, inert для
          фона и ::backdrop без единой строки состояния. Выезд снизу и затемнение
          фона описаны в styles/globals.css — открытие 260 мс, закрытие 200 мс. */}
      <dialog
        ref={sheet}
        aria-label="Ещё"
        onClick={(event) => { if (event.target === sheet.current) sheet.current?.close(); }}
        // Решётка микрофона осталась только на плеере: лист «Ещё» — меню, не динамик.
        className="mt-auto mb-0 w-full max-w-none rounded-t-card border-0 bg-surface-elevated p-0 text-text-primary shadow-sheet lg:hidden"
      >
        <div className="flex flex-col gap-3 px-4 pt-2 pb-[max(2rem,env(safe-area-inset-bottom))]">
          <span aria-hidden className="mx-auto h-1 w-9 rounded-sm bg-hairline-strong" />
          <div className="flex items-center justify-between px-0.5 py-1">
            <span className="text-section">Ещё</span>
            <button type="button" aria-label="Закрыть" onClick={() => sheet.current?.close()}
                    className="press hit relative rounded-inner p-1 text-text-secondary hover:text-text-primary">
              <X size={18} aria-hidden />
            </button>
          </div>
          <div className="overflow-hidden rounded-card bg-surface shadow-plate">
            {SHEET.map((key) => {
              const item = NAV.find((entry) => entry.key === key)!;
              const Glyph = item.icon;
              return (
                <Link
                  key={key}
                  href={item.href}
                  onClick={() => sheet.current?.close()}
                  className="press-row flex items-center gap-3 px-3.5 py-3.5 text-[15px] not-first:border-t not-first:border-hairline hover:bg-surface-elevated"
                >
                  <Glyph size={20} className="text-text-secondary" aria-hidden />
                  <span className="flex-1">{item.label}</span>
                  {mark(key)}
                  <CaretRight size={14} className="text-text-tertiary" aria-hidden />
                </Link>
              );
            })}
          </div>
        </div>
      </dialog>
    </div>
  );
}
