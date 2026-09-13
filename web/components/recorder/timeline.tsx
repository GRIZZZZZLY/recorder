"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CaretLeft, CaretRight } from "@phosphor-icons/react/dist/ssr";
import { thin, Waveform } from "@/components/recorder/waveform";
import type { SessionRow } from "@/lib/api";
import {
  formatClock, formatDayShort, formatDuration, formatTime, hoursOfDay, todayIso,
} from "@/lib/format";
import { at } from "@/lib/motion";
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
 */
const PX_PER_HOUR = 80;
const DAY_PX = 24 * PX_PER_HOUR;
/** Минимум ширины блока: минутная запись остаётся узкой, но видимой. Палец
 * попадает по ней за счёт высоты 28 px и подписи рядом, а не за счёт того, что
 * блок раздут до чужой длительности. */
const MIN_BLOCK_PX = 10;
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

function clamp(value: number, low: number, high: number) {
  return Math.min(Math.max(value, low), high);
}

function label(row: SessionRow) {
  return row.topic || `Сессия ${String(row.session).padStart(4, "0")}`;
}

export function DayTimeline({ rows, now, at: tick = 0 }: {
  rows: SessionRow[];
  now: Date;
  at?: number;
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

  // При открытии и при смене дня головка встаёт на осмысленное место: сегодня
  // это «сейчас», в прошлом дне — первая запись, в пустом — середина рабочего дня.
  useEffect(() => {
    const first = dayRows.length ? Math.min(...dayRows.map((row) => startSeconds(row)!)) : null;
    center(day === today ? nowSeconds : first ?? 12 * 3600);
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
  }

  function onPointerMove(event: React.PointerEvent<HTMLElement>) {
    const box = scroller.current;
    const state = drag.current;
    if (!box || !state || state.pointer !== event.pointerId) return;
    // Головка едет вправо — плёнка уезжает влево, время растёт.
    box.scrollLeft = state.left + (event.clientX - state.x);
  }

  function onPointerUp(event: React.PointerEvent<HTMLElement>) {
    if (drag.current?.pointer === event.pointerId) drag.current = null;
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
    return head >= start && head <= start + Math.max(row.seconds, 1);
  });

  function openUnderHead() {
    if (!under) return;
    const offset = Math.max(0, Math.round(head - startSeconds(under)!));
    router.push(`${sessionHref(under.device_id, under.session)}&t=${offset}`);
  }

  const headLabel = `${String(Math.floor(head / 3600)).padStart(2, "0")}:${String(Math.floor((head % 3600) / 60)).padStart(2, "0")}`;
  const awayFromNow = day !== today || Math.abs(head - nowSeconds) > 1800;

  return (
    <div className="enter flex flex-col gap-2.5 rounded-card bg-surface px-5 pt-4 pb-3.5" style={at(tick)}>
      <div className="flex items-center justify-between gap-3 text-note text-text-secondary">
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Предыдущий день" disabled={index <= 0}
                  onClick={() => setDay(days[index - 1])}
                  className="press -ml-1.5 rounded-inner p-1.5 hover:text-text-primary disabled:opacity-30">
            <CaretLeft size={14} aria-hidden />
          </button>
          <span className="min-w-[92px] text-center text-text-primary">
            {day === today ? "сегодня" : formatDayShort(day)}
          </span>
          <button type="button" aria-label="Следующий день" disabled={index >= days.length - 1}
                  onClick={() => setDay(days[index + 1])}
                  className="press rounded-inner p-1.5 hover:text-text-primary disabled:opacity-30">
            <CaretRight size={14} aria-hidden />
          </button>
        </div>

        <div className="flex items-center gap-3">
          <span>{dayRows.length ? `${dayRows.length} на шкале` : "записей нет"}</span>
          {awayFromNow && (
            <button type="button"
                    onClick={() => { setDay(today); center(nowSeconds, day === today); }}
                    className="press rounded-chip border border-control px-2 py-0.5 text-micro hover:text-text-primary">
              сейчас
            </button>
          )}
        </div>
      </div>

      {/* Плёнка. Головка лежит поверх и не скроллится вместе с ней. */}
      <div className="relative">
        <div
          ref={scroller}
          onScroll={() => {
            if (frame.current) return;
            frame.current = requestAnimationFrame(() => { frame.current = 0; sync(); });
          }}
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
                <div key={hour} style={{ left: hour * PX_PER_HOUR, width: PX_PER_HOUR, ...at(tick, hour) }}
                     className="ruler-line absolute top-0 bottom-0 border-l border-hairline" />
              ))}
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

            {dayRows.map((row, position) => {
              const start = startSeconds(row)!;
              const left = (start / 3600) * PX_PER_HOUR;
              const width = Math.max((row.seconds / 3600) * PX_PER_HOUR, MIN_BLOCK_PX);
              const tone = statusTone(row);
              const active = under?.device_id === row.device_id && under?.session === row.session;
              const delay = `calc(var(--i, 0) * var(--stagger) + 375ms + ${Math.min(position, 12)} * var(--stagger))`;
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
                      "cascade-block press-row absolute top-4 flex h-10 items-center rounded-inner px-px hover:bg-hairline",
                      active && "bg-hairline",
                    )}
                    style={{ left, width, animationDelay: delay }}
                  >
                    <Waveform values={thin(row.envelope, Math.max(2, Math.floor(width / 2)))}
                              height={32} tone={WAVE[tone]} className="w-full"
                              style={{ animationDelay: delay }} />
                  </Link>

                  {/* Метки владельца — треугольники над волной. */}
                  {row.marks?.map((mark) => (
                    <span key={mark} aria-hidden
                          title="отмечено кнопкой"
                          style={{
                            left: left + clamp((mark / 3600) * PX_PER_HOUR, 0, width),
                            animationDelay: delay,
                          }}
                          className="cascade-block absolute top-1.5 -translate-x-1/2 border-x-[3px] border-t-[5px] border-x-transparent border-t-fill" />
                  ))}
                </Fragment>
              );
            })}
          </div>

          {/* Часы подписаны под плёнкой и едут вместе с ней. */}
          <div aria-hidden className="relative h-4" style={{ width: DAY_PX }}>
            {Array.from({ length: 24 }, (_, hour) => (
              <span key={hour} style={{ left: hour * PX_PER_HOUR, ...at(tick, hour) }}
                    className="ruler-label absolute pl-1 font-mono text-[11px] text-text-tertiary">
                {String(hour).padStart(2, "0")}
              </span>
            ))}
          </div>
          </div>
        </div>

        {/* Головка: тянется мышью и пальцем, ходит стрелками с клавиатуры. */}
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
          className="absolute top-0 bottom-0 left-1/2 flex w-11 -translate-x-1/2 cursor-grab touch-none items-start justify-center active:cursor-grabbing"
        >
          <span aria-hidden className="absolute top-0 bottom-0 w-px bg-accent shadow-glow-accent-soft" />
          <span aria-hidden className="absolute -top-1 size-2 rounded-chip bg-accent" />
        </button>
      </div>

      {/* Подпись под головкой: время, а если под ней запись — её имя. */}
      <div className="flex items-baseline justify-center gap-2 text-note">
        <span className="font-mono text-accent-text">{headLabel}</span>
        {under ? (
          <button type="button" onClick={openUnderHead}
                  className="press min-w-0 truncate text-text-primary hover:underline">
            {label(under)} · {formatClock(Math.max(0, head - startSeconds(under)!))}
          </button>
        ) : (
          <span className="text-text-tertiary">записи нет</span>
        )}
      </div>
    </div>
  );
}
