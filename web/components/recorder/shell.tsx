"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { visited } from "@/lib/nav-history";
import {
  CaretRight, CheckSquare, DotsThreeCircle, Folders, House, MagnifyingGlass,
  Pulse, Question, Waveform as WaveformIcon, WarningCircle, X,
} from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react";
import { useApi } from "@/lib/use-api";
import type { DigestBody } from "@/lib/api";
import { formatAgo } from "@/lib/format";
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
  // Для «назад» в шапке страницы: знать, из какого раздела пришли.
  useEffect(() => visited(pathname), [pathname]);
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

  const lastAt = data?.recent?.[0]?.last_at ?? null;
  const live = isLive(lastAt, now);

  // Прибор включается один раз за открытие сайта. Пока на html нет
  // data-entered, содержимое входит с движением (правила в styles/studio.css);
  // как только атрибут поставлен, всё, что появится позже — смена раздела,
  // приход новых данных — возникает без движения. Оболочка живёт в разметке и
  // при переходах не пересоздаётся, поэтому эффект срабатывает ровно один раз.
  //
  // Момент выбирается не таймером от загрузки: содержимое появляется после
  // ответа сервера, и на медленном телефоне оно опаздывало бы к сроку, а вход
  // не состоялся бы вовсе. Считаем вход законченным через 400 мс после
  // последней его анимации; запас в 6 с закрывает случай, когда не пришло ни
  // одной — пустая страница или запрет движения в системе.
  useEffect(() => {
    let timer = window.setTimeout(finish, 6000);

    function finish() {
      document.documentElement.dataset.entered = "1";
      window.removeEventListener("animationend", onEnd, true);
    }

    function onEnd(event: AnimationEvent) {
      if (!event.animationName.startsWith("studio-")) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(finish, 400);
    }

    window.addEventListener("animationend", onEnd, true);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("animationend", onEnd, true);
    };
  }, []);

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

      <aside className="recorder-sidebar hidden w-[220px] shrink-0 flex-col gap-0.5 border-r border-hairline px-3 py-5 lg:flex">
        <div className="flex items-center gap-2.5 px-2.5 pt-1.5 pb-5">
          {/* Тот же светодиод, что на кромке нижней панели, и по тому же
              правилу: в тишине его нет. Место под него не держим — «Диктофон»
              просто стоит слева, пока запись не началась. */}
          {live && (
            <span className="live-dot size-2 rounded-chip bg-accent shadow-lens"
                  title="диктофон пишет" />
          )}
          <span className="text-row font-semibold">Диктофон</span>
          <span className="studio-model" aria-hidden>STUDIO / 01</span>
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
        {/* Отступ снизу на телефоне — под плавающую панель разделов и жестовую
            полосу iPhone: последняя строка длинной страницы должна доезжать
            до глаз, а не прятаться под панелью. */}
        <main
          id="content"
          className="page-in mx-auto flex w-full max-w-[1250px] flex-col gap-[23px] px-[15px] pt-[23px] pb-[calc(8rem+env(safe-area-inset-bottom))] min-[361px]:px-[21px] sm:px-8 lg:gap-[30px] lg:px-12 lg:pt-[30px] lg:pb-14"
        >
          {/* Шильдик прибора: имя, модель и состояние диктофона словами. Виден
              с любой страницы и на телефоне тоже — десктопный aside не считается. */}
          <div className="studio-brandline">
            <span className="studio-wordmark">
              <span className={cx("studio-brand-disc", live && "is-live")} aria-hidden />
              Диктофон
              <span className="studio-model" aria-hidden>STUDIO / 01</span>
            </span>
            <Link href="/status/" className="studio-device-state hit relative">
              <span className={cx("studio-led", live && "is-live")} aria-hidden />
              {/* Про тишину диктофона известно одно — когда он выгружался последний
                  раз. «В ожидании» было догадкой, выдававшей себя за состояние. */}
              {!data ? "" : live ? "Идёт запись" : lastAt ? `выгрузка ${formatAgo(lastAt, now)}` : "выгрузок не было"}
            </Link>
          </div>
          {children}
        </main>
      </div>

      {/* Нижняя панель телефона. */}
      <nav aria-label="Разделы"
           className="recorder-tabs fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-hairline bg-surface/90 px-2 pt-2.5 pb-[max(1rem,env(safe-area-inset-bottom))] backdrop-blur-xl lg:hidden">
        {/* Светодиод записи на кромке панели: виден с любой страницы, пока
            диктофон пишет. В тишине его нет вовсе — точка, которая тлеет
            круглосуточно и ничего не сообщает, это шум. Появление на пустом
            месте заметнее смены яркости, а состояние словами живёт в плашке
            «Диктофон» на сводке и на странице «Состояние». */}
        {live && (
          <span aria-hidden
                className="live-dot absolute -top-1 right-5 size-2 rounded-chip bg-accent shadow-lens" />
        )}
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
