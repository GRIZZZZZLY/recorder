"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CaretLeft, CaretRight } from "@phosphor-icons/react/dist/ssr";
import { Waveform } from "@/components/recorder/waveform";
import type { SessionRow } from "@/lib/api";
import {
  formatClock, formatDayShort, formatDuration, formatTime, hoursOfDay, todayIso,
} from "@/lib/format";
import { magnetTarget } from "@/lib/magnet";
import { sessionHref } from "@/lib/session-link";
import { STATUS_TEXT, statusTone } from "@/lib/session-status";
import { cx } from "@/utils/cx";

/**
 * Лента дня как плёнка под головкой.
 *
 * Красная головка стоит на месте, а сутки едут под ней: так работает дека, и
 * так же понятнее на телефоне — тянешь головку или сам холст, и время под
 * головкой меняется. Час занимает 80 px, поэтому получасовое совещание — это
 * 40 px, в которые можно попасть пальцем; прежняя шкала «07–22 на ширину
 * экрана» давала часу 22 px и превращала записи в неразличимые чёрточки.
 *
 * Сутки на плёнке ровно одни: переключение дней — стрелками в шапке, по дням,
 * где записи есть. Бесконечную ленту через все дни сознательно не делаем: она
 * требует подмены scrollLeft на лету, а это гасит инерцию прокрутки на iOS.
 *
 * На телефоне модель одна: палец двигает плёнку, головка стоит. Для грубого
 * указателя головка прозрачна для касаний — иначе полоса в 44 px посреди
 * экрана перехватывала свайп и тянула плёнку без инерции. Мышью головку
 * по-прежнему можно схватить. Когда прокрутка кончилась, головка примагничи-
 * вается к ближайшей записи в радиусе SNAP_PX — сама, мягко, уже после того,
 * как инерция иссякла: подменять scrollLeft во время инерции нельзя. Нативный
 * scroll-snap не годится: у WebKit он гасит инерционный скролл (bug 243582).
 */
const PX_PER_HOUR = 80;
const DAY_PX = 24 * PX_PER_HOUR;
/** Минимум ширины блока: минутная запись остаётся узкой, но видимой. Палец
 * попадает по ней не за счёт того, что блок раздут до чужой длительности, а за
 * счёт невидимой цели 44 px (`hit`) и магнита. */
const MIN_BLOCK_PX = 10;
/** Та же минимальная ширина в секундах: головка над узким блоком — «над записью». */
const MIN_SECONDS = (MIN_BLOCK_PX / PX_PER_HOUR) * 3600;
/** Дальность магнита: 24 px — это 18 минут ленты. */
const SNAP_PX = 24;
/** Подсветка записи по мере приближения головки: видно, что магнит вот-вот
 * поймает, ещё до того, как палец отпущен. Порядок — от «далеко» к «внутри». */
const NEAR_BG = ["", "bg-hairline/40", "bg-hairline/70", "bg-hairline"] as const;
const DAY_SECONDS = 24 * 3600;

/** Цвет волны — состояние записи: готова, ещё считается, упала. */
const WAVE = {
  ok: "bg-fill",
  warn: "bg-waveform",
  accent: "bg-accent",
} as const;

/** Секунды от начала суток, когда пришла первая часть сессии. */
function startSeconds(row: SessionRow): number | null {
  const hours = hoursOfDay(row.first_at);
  return hours === null ? null : hours * 3600;
}

/** Сколько секунд ленты занимает запись — не меньше, чем её блок на экране. */
function spanSeconds(row: SessionRow) {
  return Math.max(row.seconds, MIN_SECONDS);
}

function clamp(value: number, low: number, high: number) {
  return Math.min(Math.max(value, low), high);
}

function label(row: SessionRow) {
  return row.topic || `Сессия ${String(row.session).padStart(4, "0")}`;
}

export function DayTimeline({ rows, now }: {
  rows: SessionRow[];
  now: Date;
}) {
  const router = useRouter();
  const today = todayIso();
  const [day, setDay] = useState(today);
  const [head, setHead] = useState(0);
  // Поля по половине видимой ширины с каждой стороны: и полночь, и конец суток
  // должны доезжать до головки, которая стоит по центру.
  const [pad, setPad] = useState(0);
  // Тот же отступ для расчётов: center и sync живут в useCallback без
  // зависимостей, состояние в их замыкании было бы устаревшим.
  const padRef = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);
  const frame = useRef(0);
  const drag = useRef<{ pointer: number; x: number; left: number } | null>(null);
  // Магнит: `held` — палец на плёнке или мышь на головке, снап ждёт; `armed` —
  // прокрутку начал пользователь, а не код (клавиши, смена дня, «сейчас»), —
  // только такую и притягиваем; `settle` — таймер тишины для Safari без scrollend.
  const held = useRef(false);
  const armed = useRef(false);
  const settle = useRef(0);

  const nowSeconds = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();

  // Дни, между которыми ходят стрелки: те, где есть записи, плюс сегодня —
  // на пустой день попасть можно, но пролистывать месяц пустоты незачем.
  const days = useMemo(() => {
    const set = new Set<string>([today]);
    for (const row of rows) if (row.day) set.add(row.day);
    return [...set].sort();
  }, [rows, today]);

  const dayRows = useMemo(
    () => rows.filter((row) => row.day === day && startSeconds(row) !== null)
             .sort((a, b) => startSeconds(a)! - startSeconds(b)!),
    [rows, day],
  );

  const index = days.indexOf(day);

  /** Время под головкой = центр видимой части плёнки за вычетом поля слева. */
  const sync = useCallback(() => {
    const box = scroller.current;
    if (!box) return;
    const x = box.scrollLeft + box.clientWidth / 2 - padRef.current;
    setHead(clamp((x / PX_PER_HOUR) * 3600, 0, DAY_SECONDS));
  }, []);

  const center = useCallback((seconds: number, smooth = false) => {
    const box = scroller.current;
    if (!box) return;
    box.scrollTo({
      left: padRef.current + (seconds / 3600) * PX_PER_HOUR - box.clientWidth / 2,
      behavior: smooth && !window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "smooth" : "auto",
    });
  }, []);

  /** Прокрутка кончилась: если головка встала рядом с записью — доехать до неё. */
  const snap = useCallback(() => {
    const box = scroller.current;
    if (!box || held.current || !armed.current) return;
    armed.current = false;
    const x = box.scrollLeft + box.clientWidth / 2 - padRef.current;
    const spans = dayRows.map((row) => {
      const start = (startSeconds(row)! / 3600) * PX_PER_HOUR;
      return { start, end: start + (spanSeconds(row) / 3600) * PX_PER_HOUR };
    });
    const target = magnetTarget(x, spans, SNAP_PX);
    if (target !== null) center((target / PX_PER_HOUR) * 3600, true);
  }, [dayRows, center]);

  /** Конец прокрутки без scrollend: 150 мс без событий scroll. */
  const settleLater = useCallback(() => {
    window.clearTimeout(settle.current);
    settle.current = window.setTimeout(snap, 150);
  }, [snap]);

  useEffect(() => () => window.clearTimeout(settle.current), []);

  useEffect(() => {
    const box = scroller.current;
    if (!box) return;
    const observer = new ResizeObserver(() => {
      padRef.current = box.clientWidth / 2;
      setPad(padRef.current);
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  // При открытии и при смене дня головка встаёт на последнюю запись дня — за
  // ней обычно и приходят. В дне без записей: сегодня — «сейчас», в прошлом —
  // середина рабочего дня. dayRows отсортированы по началу, последняя — свежая.
  useEffect(() => {
    const latest = dayRows.length ? startSeconds(dayRows[dayRows.length - 1])! : null;
    center(latest ?? (day === today ? nowSeconds : 12 * 3600));
    sync();
    // nowSeconds меняется каждую минуту — перематывать плёнку из-за этого нельзя,
    // поэтому его в зависимостях нет намеренно. pad — есть: до первого замера
    // ширины середина плёнки считается неверно.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day, pad, center, sync]);

  function onPointerDown(event: React.PointerEvent<HTMLElement>) {
    const box = scroller.current;
    if (!box) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointer: event.pointerId, x: event.clientX, left: box.scrollLeft };
    held.current = true;
    armed.current = true;
  }

  function onPointerMove(event: React.PointerEvent<HTMLElement>) {
    const box = scroller.current;
    const state = drag.current;
    if (!box || !state || state.pointer !== event.pointerId) return;
    // Головка едет вправо — плёнка уезжает влево, время растёт.
    box.scrollLeft = state.left + (event.clientX - state.x);
  }

  function onPointerUp(event: React.PointerEvent<HTMLElement>) {
    if (drag.current?.pointer !== event.pointerId) return;
    drag.current = null;
    held.current = false;
    settleLater();
  }

  // Палец на плёнке: пока держит — магнит ждёт; отпустил — ждём конца инерции.
  function onTouchStart() {
    held.current = true;
    armed.current = true;
  }

  function onTouchEnd() {
    held.current = false;
    settleLater();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    const step = event.shiftKey ? 3600 : 900;
    const moves: Record<string, number> = {
      ArrowLeft: -step, ArrowRight: step, PageUp: -3600 * 3, PageDown: 3600 * 3,
    };
    if (event.key in moves) {
      event.preventDefault();
      center(clamp(head + moves[event.key], 0, DAY_SECONDS), true);
    } else if (event.key === "Home") {
      event.preventDefault();
      center(0, true);
    } else if (event.key === "End") {
      event.preventDefault();
      center(DAY_SECONDS, true);
    }
  }

  // Запись под головкой: её имя видно в подписи, и по нажатию открывается
  // сессия ровно на этой секунде.
  const under = dayRows.find((row) => {
    const start = startSeconds(row)!;
    return head >= start && head <= start + spanSeconds(row);
  });

  function openUnderHead() {
    if (!under) return;
    // Блок короткой записи шире её самой: секунда не дальше её конца.
    const offset = clamp(Math.round(head - startSeconds(under)!), 0, under.seconds);
    router.push(`${sessionHref(under.device_id, under.session)}&t=${offset}`);
  }

  const headPx = (head / 3600) * PX_PER_HOUR;
  const headLabel = `${String(Math.floor(head / 3600)).padStart(2, "0")}:${String(Math.floor((head % 3600) / 60)).padStart(2, "0")}`;
  const awayFromNow = day !== today || Math.abs(head - nowSeconds) > 1800;

  return (
    <div className="recorder-timeline flex flex-col gap-2.5 rounded-card bg-surface px-5 pt-4 pb-3.5 shadow-plate">
      <div className="flex items-center justify-between gap-3 text-note text-text-secondary">
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Предыдущий день" disabled={index <= 0}
                  onClick={() => setDay(days[index - 1])}
                  className="press hit relative -ml-1.5 rounded-inner p-1.5 hover:text-text-primary disabled:opacity-30">
            <CaretLeft size={14} aria-hidden />
          </button>
          <span className="min-w-[92px] text-center text-text-primary">
            {day === today ? "сегодня" : formatDayShort(day)}
          </span>
          <button type="button" aria-label="Следующий день" disabled={index >= days.length - 1}
                  onClick={() => setDay(days[index + 1])}
                  className="press hit relative rounded-inner p-1.5 hover:text-text-primary disabled:opacity-30">
            <CaretRight size={14} aria-hidden />
          </button>
        </div>

        <div className="flex items-center gap-3">
          {/* Счётчика «N на шкале» здесь нет: под головкой уже написано, что
              под ней, а «записей нет» повторялось бы дважды на одном экране. */}
          {awayFromNow && (
            <button type="button"
                    onClick={() => { setDay(today); center(nowSeconds, day === today); }}
                    className="press hit relative rounded-chip border border-control px-2 py-0.5 text-micro hover:text-text-primary">
              сейчас
            </button>
          )}
        </div>
      </div>

      {/* Плёнка лежит в нише: тень у верхней кромки, освещённая нижняя, холоднее
          корпуса. Головка — слой управления поверх ниши, не скроллится с ней. */}
      <div className="relative -mx-2 rounded-inner bg-well px-2 shadow-well">
        <div
          ref={scroller}
          onScroll={() => {
            settleLater();
            if (frame.current) return;
            frame.current = requestAnimationFrame(() => { frame.current = 0; sync(); });
          }}
          // Где scrollend есть (Safari 26.2+, Chrome 114+), конец прокрутки
          // точный; таймер тишины выше — запас для старых Safari.
          onScrollEnd={() => { window.clearTimeout(settle.current); snap(); }}
          onTouchStart={onTouchStart}
          onTouchEnd={onTouchEnd}
          onTouchCancel={onTouchEnd}
          onWheel={() => { armed.current = true; }}
          className="no-scrollbar overflow-x-auto overscroll-x-contain"
        >
          {/* Поля по половине видимой ширины: полночь и конец суток тоже должны
              доезжать до головки в центре. Ширина задана явно — хвостовое поле
              в контейнере прокрутки иначе теряется. */}
          <div className="flex flex-col gap-1.5"
               style={{ width: DAY_PX + pad * 2, paddingInline: pad }}>
          <div className="relative h-[72px]" style={{ width: DAY_PX }}>
            <div aria-hidden className="absolute inset-0">
              {Array.from({ length: 24 }, (_, hour) => (
                <div key={hour} style={{ left: hour * PX_PER_HOUR, width: PX_PER_HOUR }}
                     className="absolute top-0 bottom-0 border-l border-hairline-strong" />
              ))}
              {/* Гравировка четвертей часа по верхней кромке: 20 px — 15 минут. */}
              <div className="absolute inset-x-0 top-0 h-1"
                   style={{ backgroundImage: "repeating-linear-gradient(90deg, var(--color-hairline-strong) 0 1px, transparent 1px 20px)" }} />
            </div>

            {day === today && (
              <span aria-hidden
                    className="absolute -top-1 -bottom-1 w-px bg-hairline-strong"
                    style={{ left: (nowSeconds / 3600) * PX_PER_HOUR }}>
                <span className="absolute -top-4 -translate-x-1/2 font-mono text-[11px] text-text-tertiary">
                  сейчас
                </span>
              </span>
            )}

            {dayRows.map((row) => {
              const start = startSeconds(row)!;
              const left = (start / 3600) * PX_PER_HOUR;
              const width = Math.max((row.seconds / 3600) * PX_PER_HOUR, MIN_BLOCK_PX);
              const tone = statusTone(row);
              // Насколько головка близка: 0 — она внутри записи. Границы блока
              // те же, по которым считает магнит, поэтому «внутри» здесь и
              // «запись под головкой» — одно и то же.
              const gap = Math.max(left - headPx, headPx - (left + width), 0);
              const near = gap === 0 ? 3 : gap <= SNAP_PX / 2 ? 2 : gap <= SNAP_PX ? 1 : 0;
              return (
                <Fragment key={`${row.device_id}-${row.session}`}>
                  {/* Запись — это её громкость, и больше ничего. Время читается
                      по линейке, имя стоит под головкой; повторять их в блоке
                      незачем. Столбик на два пикселя: 45 секунд звука. */}
                  <Link
                    href={sessionHref(row.device_id, row.session)}
                    aria-label={`${label(row)}, ${formatTime(row.first_at)}, ${formatDuration(row.seconds)}, ${STATUS_TEXT[tone]}`}
                    title={`${label(row)} · ${formatDuration(row.seconds)} · ${STATUS_TEXT[tone]}`}
                    className={cx(
                      "press-row hit absolute top-4 flex h-10 items-center rounded-inner px-px hover:bg-hairline",
                      NEAR_BG[near],
                      // Запись под головкой — чуть ярче и резче соседей.
                      near === 3 && "brightness-110",
                    )}
                    style={{ left, width }}
                  >
                    <Waveform values={row.envelope} bar={2} gap={1}
                              height={32} tone={WAVE[tone]} className="w-full" />
                  </Link>

                  {/* Метки владельца — треугольники над волной. */}
                  {row.marks?.map((mark) => (
                    <span key={mark} aria-hidden
                          title="отмечено кнопкой"
                          style={{ left: left + clamp((mark / 3600) * PX_PER_HOUR, 0, width) }}
                          className="absolute top-1.5 -translate-x-1/2 border-x-[3px] border-t-[5px] border-x-transparent border-t-mark" />
                  ))}
                </Fragment>
              );
            })}
          </div>

          {/* Часы подписаны под плёнкой и едут вместе с ней. */}
          <div aria-hidden className="relative h-4" style={{ width: DAY_PX }}>
            {Array.from({ length: 24 }, (_, hour) => (
              <span key={hour} style={{ left: hour * PX_PER_HOUR }}
                    className="absolute pl-1 font-mono text-[11px] text-text-tertiary">
                {String(hour).padStart(2, "0")}
              </span>
            ))}
          </div>
          </div>
        </div>

        {/* Головка: тянется мышью, ходит стрелками с клавиатуры. Для пальца
            прозрачна — свайп идёт плёнке, открыть запись можно подписью ниже. */}
        <button
          type="button"
          role="slider"
          aria-label="Время на ленте дня"
          aria-valuemin={0}
          aria-valuemax={DAY_SECONDS}
          aria-valuenow={Math.round(head)}
          aria-valuetext={under ? `${headLabel}, ${label(under)}` : headLabel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
          onClick={openUnderHead}
          className="absolute top-0 bottom-0 left-1/2 flex w-11 -translate-x-1/2 cursor-grab touch-none items-start justify-center active:cursor-grabbing pointer-coarse:pointer-events-none"
        >
          <span aria-hidden className="absolute top-0 bottom-0 w-px bg-accent shadow-glow-accent-soft" />
          <span aria-hidden className="absolute -top-1 size-2 rounded-chip bg-accent shadow-lens" />
        </button>
      </div>

      {/* Подпись под головкой: время, а если под ней запись — её имя. */}
      <div className="flex items-baseline justify-center gap-2 text-note">
        {/* Показание прибора: одно крупное число на экране. */}
        <span className="font-mono text-[22px] leading-none text-accent-text">{headLabel}</span>
        {under ? (
          <button type="button" onClick={openUnderHead}
                  // Цель 44 px за счёт полей, а не `hit`: truncate режет всё, что
                  // выходит за рамку, невидимый слой тоже. Отрицательные поля
                  // возвращают строке прежнюю высоту.
                  className="press -my-3.5 min-w-0 truncate py-3.5 text-text-primary hover:underline">
            {label(under)} · {formatClock(Math.max(0, head - startSeconds(under)!))}
          </button>
        ) : (
          <span className="text-text-tertiary">записи нет</span>
        )}
      </div>
    </div>
  );
}
