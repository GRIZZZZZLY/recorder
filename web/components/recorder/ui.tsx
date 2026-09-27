"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { Play } from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react";
import { usePlayhead, useSeek } from "@/components/recorder/player";
import { apiPost, type Tape } from "@/lib/api";
import { Waveform } from "@/components/recorder/waveform";
import { formatDayShort, normalizeClock } from "@/lib/format";
import { revealUtterance } from "@/lib/reveal";
import { cx } from "@/utils/cx";

/** На сервере раскладку не измерить, а предупреждение React читать незачем. */
const useMeasureEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * Примитивы макета «Студия»: кнопка, поле, чип, сегментированный контрол,
 * точка источника, кнопка «основание».
 *
 * Все состояния взяты из артборда «15 Состояния»; кольцо фокуса рисует общее
 * правило в styles/globals.css, поэтому здесь его нет.
 */

const BUTTON_VARIANTS = {
  // Красная кнопка — редкость: только «запись · внимание · сейчас». Подпись
  // графитовая: белым по #ff453a выходило 3,13:1, графитом — 5,67:1.
  primary: "bg-accent text-canvas hover:bg-accent-hover hover:shadow-glow-accent active:bg-accent-pressed",
  secondary: "bg-hairline text-text-primary hover:bg-hairline-strong disabled:text-text-disabled",
  ghost: "text-text-secondary hover:bg-white/6 hover:text-text-primary",
} as const;

export function Button({
  variant = "secondary",
  icon: IconGlyph,
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof BUTTON_VARIANTS;
  icon?: Icon;
}) {
  return (
    <button
      {...props}
      className={cx(
        // `hit`: кнопка 34 px на вид, 44 под пальцем.
        "press hit relative inline-flex items-center gap-2 rounded-inner px-4 py-2 text-note",
        "disabled:cursor-not-allowed disabled:opacity-40",
        BUTTON_VARIANTS[variant],
        className,
      )}
    >
      {IconGlyph ? <IconGlyph size={16} aria-hidden /> : null}
      {children}
    </button>
  );
}

/**
 * Поле ввода с меткой сверху и ошибкой снизу. Рамка при ошибке не краснеет —
 * красным светится только текст ошибки: рамка уже занята кольцом фокуса.
 */
export function Input({
  label,
  error,
  className,
  onChange,
  ...props
}: Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange"> & {
  label?: string;
  error?: string | null;
  onChange?: (value: string) => void;
}) {
  return (
    <label className={cx("flex flex-col gap-1.5", className)}>
      {label ? <span className="text-micro text-text-secondary">{label}</span> : null}
      <input
        {...props}
        onChange={(event) => onChange?.(event.target.value)}
        className={cx(
          "min-h-11 rounded-inner border border-control bg-surface px-3 py-2.5 text-row text-text-primary",
          "transition-colors placeholder:text-text-tertiary",
          "disabled:border-hairline disabled:text-text-disabled",
        )}
      />
      {error ? <span className="rise-in text-micro text-accent-text">{error}</span> : null}
    </label>
  );
}

export function Chip({
  selected,
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean }) {
  return (
    <button
      {...props}
      aria-pressed={selected}
      className={cx(
        "press hit relative rounded-chip px-3 py-1.5 text-note",
        selected
          ? "bg-fill text-canvas"
          : "border border-control text-text-secondary hover:border-white/30 hover:text-text-primary",
        className,
      )}
    >
      {children}
    </button>
  );
}

/** Метка темы. Со ссылкой ведёт в ленту проекта, без неё — просто подпись. */
export function Tag({ href, children }: { href?: string; children: React.ReactNode }) {
  // `hit`: тег 32 px на вид, 44 под пальцем.
  const className =
    "hit relative rounded-chip border border-hairline-strong px-3 py-1.5 text-note text-text-secondary";
  if (!href) return <span className={className}>{children}</span>;
  return (
    <Link href={href} className={cx(className, "press hover:border-white/30 hover:text-text-primary")}>
      {children}
    </Link>
  );
}

/**
 * Сегментированный контрол. Ползунок — отдельный слой под кнопками: у
 * сегментов разная ширина и они переносятся на вторую строку, поэтому его
 * раскладка приходит замером выбранной кнопки, а не расчётом долей. Переезд —
 * 200 мс ease-spring, как предписывает артборд «16 Движение»; цвет текста
 * меняется за 150 мс. Стрелками переключается нативно: это обычные кнопки в
 * одной группе.
 */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: { key: T; title: string; count?: number; alarm?: boolean }[];
  onChange: (key: T) => void;
  label: string;
}) {
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const [slider, setSlider] = useState<React.CSSProperties | null>(null);
  // Массив options приходит новым при каждом рендере, поэтому зависимость —
  // строка из подписей и чисел: иначе эффект перезапускал бы сам себя.
  const layout = options.map((option) => `${option.key}:${option.title}:${option.count ?? ""}`).join("|");

  // Замер — в layout-эффекте: иначе первый кадр рисуется с ползунком в нуле,
  // и он приезжает на место при открытии страницы.
  useMeasureEffect(() => {
    function measure() {
      const element = buttons.current.get(value);
      if (!element) return setSlider(null);
      setSlider({
        translate: `${element.offsetLeft}px ${element.offsetTop}px`,
        width: element.offsetWidth,
        height: element.offsetHeight,
      });
    }
    measure();
    // Перенос сегментов на вторую строку меняет и высоту, и смещения —
    // ResizeObserver ловит это надёжнее, чем слушатель resize у окна.
    const element = buttons.current.get(value);
    const box = element?.parentElement;
    if (!box) return;
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => observer.disconnect();
  }, [value, layout]);

  return (
    <div role="group" aria-label={label}
         className="relative inline-flex flex-wrap gap-0.5 rounded-inner bg-surface p-0.5 text-note">
      {slider ? (
        <span aria-hidden style={slider}
              className="segment-slide absolute top-0 left-0 rounded-md bg-surface-segment" />
      ) : null}
      {options.map((option) => {
        const selected = option.key === value;
        return (
          <button
            key={option.key}
            ref={(element) => {
              if (element) buttons.current.set(option.key, element);
              else buttons.current.delete(option.key);
            }}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(option.key)}
            className={cx(
              "press hit relative rounded-md px-3.5 py-1.5",
              selected ? "text-text-primary" : "text-text-secondary hover:text-text-primary",
            )}
          >
            {option.title}
            {/* Ноль не показываем: «Мои 0» ничего не сообщает, а место на
                телефоне съедает — четвёртый сегмент уезжал на вторую строку. */}
            {option.count !== undefined && option.count > 0 && (
              <span className={cx(
                "ml-1.5",
                option.alarm && option.count > 0 ? "text-accent-text"
                  : selected ? "text-text-secondary" : "text-text-tertiary",
              )}>
                {option.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// Цвет текста дублирует заливку: вспышка при появлении светится currentColor.
const DOT_TONES = {
  ok: "bg-ok text-ok",
  warn: "bg-warn text-warn",
  accent: "bg-accent text-accent",
  // «Источник не найден» — не цвет, а пустой контур: нечего подсвечивать.
  missing: "border border-text-tertiary",
  muted: "bg-surface-elevated",
} as const;

/** Точка светофора. Размер 8 px в строке, 6 px в подписи. */
export function Dot({ tone, live, size = 8, title }: {
  tone: keyof typeof DOT_TONES;
  live?: boolean;
  size?: 6 | 8;
  title?: string;
}) {
  return (
    <span
      role={title ? "img" : undefined}
      aria-label={title}
      title={title}
      className={cx(
        "inline-block shrink-0 rounded-chip",
        size === 8 ? "size-2" : "size-1.5",
        DOT_TONES[tone],
        // Линза светодиода; у пустого контура «не найден» подсвечивать нечего.
        tone !== "missing" && "shadow-lens",
        live && "live-dot",
        // Красная точка без пульса — ошибка: светится ровно, её надо видеть.
        tone === "accent" && !live && "shadow-glow-accent-soft",
      )}
    />
  );
}

/** Точка источника: жёлтая — цитата не подтвердилась, пустой контур — её нет.
 * Подтверждённый пункт точки не получает («всё в порядке» не показываем;
 * зелёная точка у открытой задачи ещё и читалась как «сделано»), но место
 * держит — строки не должны прыгать. */
export function SourceDot({ source }: { source: { confirmed: boolean } | null }) {
  if (!source) return <Dot tone="missing" title="источник не найден" />;
  if (source.confirmed) return <span aria-hidden className="inline-block size-2 shrink-0" />;
  return <Dot tone="warn" title="источник не подтверждён" />;
}

// Действия — клавиши, а не голый текст: «Готово» и «Убрать» стояли текстом в
// 12 px друг от друга, и промахнуться было проще, чем попасть.
export const ROW_ACTION =
  "press hit relative rounded-chip bg-hairline px-3 py-1.5 text-note text-text-secondary shadow-plate hover:bg-hairline-strong hover:text-text-primary active:translate-y-px active:shadow-none disabled:opacity-40";
/** Второе действие строки («Убрать»): без подложки — видно, но не зовёт. */
export const ROW_ACTION_QUIET =
  "press hit relative rounded-chip px-2 py-1.5 text-note text-text-tertiary hover:text-text-primary disabled:opacity-40";

/**
 * Место пункта на плёнке его записи: утопленная ниша с настоящей волной и
 * засечкой-головкой там, где пункт прозвучал — та же головка, что на ленте
 * дня. Рядом — время-кнопка основания. Волна и время измерены, не декор:
 * задача, вопрос, спорное место — это место на записи, а не строка списка дел.
 */
export function TapeLine({ tape, at, children }: {
  tape?: Tape | null;
  /** Секунды разговора: одна засечка или несколько (все пункты записи). */
  at?: number | number[];
  children?: React.ReactNode;
}) {
  const ticks = (Array.isArray(at) ? at : at === undefined ? [] : [at]).filter(Number.isFinite);
  if (!tape || !tape.seconds) return children ? <div className="flex">{children}</div> : null;
  return (
    <div className="flex items-center gap-2.5">
      {children}
      <div aria-hidden className="relative flex h-7 min-w-0 flex-1 items-center rounded-inner bg-well px-1.5 shadow-well">
        <Waveform values={tape.envelope} height={16} bar={2} gap={1} className="w-full" />
        {ticks.map((tick, index) => (
          <span key={index} className="absolute inset-y-1 w-px bg-fill"
                style={{ left: `calc(0.375rem + (100% - 0.75rem) * ${Math.min(1, Math.max(0, tick / tape.seconds))})` }}>
            <span className="absolute -top-1 left-1/2 size-1.5 -translate-x-1/2 rounded-chip bg-fill shadow-lens" />
          </span>
        ))}
      </div>
    </div>
  );
}

/** Откуда пункт: тема разговора и день — строкой под текстом. */
export function SourceOf({ topic, day }: { topic: string | null; day: string | null }) {
  const parts = [topic, day ? formatDayShort(day) : null].filter(Boolean);
  return parts.length ? <span className="block text-note text-text-tertiary">{parts.join(" · ")}</span> : null;
}

/**
 * Та же пометка словами, строкой под текстом пункта. Цвет точки на телефоне
 * без подсказки не читается, а неподтверждённый вывод обязан быть помечен
 * так, чтобы это видел и собеседник у экрана. Подтверждённый молчит: «всё
 * в порядке» не показываем.
 */
export function SourceNote({ source }: { source?: { confirmed: boolean } | null }) {
  if (source?.confirmed) return null;
  return (
    <details className="text-note">
      <summary className={cx("hit relative inline-flex cursor-pointer list-none items-baseline gap-1.5 [&::-webkit-details-marker]:hidden",
                             source ? "text-warn" : "text-text-tertiary")}>
        {source ? "источник не подтверждён" : "источник не найден"}
        <span className="text-text-secondary underline decoration-dotted underline-offset-4">что это?</span>
      </summary>
      <span className="block text-text-secondary">
        {source
          ? "Модель сослалась на слова, которых нет в записи рядом с этим временем. Послушайте основание и решите сами."
          : "Модель не указала, откуда это взяла: проверить по звуку нельзя."}
      </span>
    </details>
  );
}

// `hit`: видимая кнопка 30 px, цель для пальца — 44.
const EVIDENCE =
  "press hit relative group inline-flex shrink-0 items-center gap-1.5 rounded-inner px-2.5 py-1.5 text-note shadow-plate active:translate-y-px active:shadow-none";

/**
 * Глиф play: контур и заливка лежат друг на друге, смена — кроссфейд 120 мс,
 * как у кнопки плеера. Заливка — когда играет или под курсором (`group`).
 */
function PlayGlyph({ filled }: { filled?: boolean }) {
  return (
    <span aria-hidden className="relative flex size-3 shrink-0 items-center justify-center">
      <Play size={12} className={cx("absolute transition-opacity duration-120",
                                    filled ? "opacity-0" : "opacity-100 group-hover:opacity-0")} />
      <Play size={12} weight="fill" className={cx("absolute transition-opacity duration-120",
                                                  filled ? "opacity-100" : "opacity-0 group-hover:opacity-100")} />
    </span>
  );
}

/**
 * Время пункта — оно же кнопка «основание»: «▷ 0:27». Отдельная пилюля
 * «основание» у каждого пункта давала восемь одинаковых кнопок на минуту
 * разговора; время и так было рядом и значило то же место записи. Нажатие
 * перематывает плеер и показывает реплику в транскрипте. Пока звучит именно
 * этот пункт — клавиша светлая и светится. Без источника — просто время.
 */
export function EvidenceTime({ time, label, context, source }: {
  time: string;
  /** Подпись в плеере; по ней клавиша узнаёт, что играет её пункт. */
  label: string;
  /** Что это за пункт — для читалки: таких кнопок на странице много. */
  context: string;
  source: { part: number; idx: number; start: number } | null | undefined;
}) {
  const seek = useSeek();
  const playhead = usePlayhead();
  const clock = normalizeClock(time);
  if (!source) {
    return (
      <span className="shrink-0 px-2.5 py-1.5 font-mono text-note text-text-tertiary" title="источник не найден">
        {clock}
      </span>
    );
  }
  const playing = playhead.playing && playhead.label === label;
  return (
    <button
      type="button"
      onClick={(event) => {
        seek(source.part, source.start, label);
        revealUtterance(source.part, source.idx, event.currentTarget);
      }}
      aria-label={`${playing ? "Играет" : "Слушать основание"}, ${clock}: ${context}`}
      className={cx(
        EVIDENCE, "font-mono",
        playing ? "bg-fill text-canvas shadow-glow-fill"
          : "bg-hairline text-text-secondary hover:bg-hairline-strong hover:text-text-primary",
      )}
    >
      <PlayGlyph filled={playing} />
      {clock}
    </button>
  );
}

/**
 * То же «основание», но на странице без плеера: ведёт на реплику внутри
 * сессии. Без источника — не ссылка, а погашенная подпись: вести некуда.
 */
export function EvidenceLink({ href, time, context }: {
  href: string | null;
  /** Время реплики-основания — тот же вид, что у кнопки на странице записи. */
  time: string;
  context?: string;
}) {
  const clock = normalizeClock(time);
  if (!href) {
    return (
      <span className="shrink-0 px-2.5 py-1.5 font-mono text-note text-text-tertiary" title="источник не найден">
        {clock}
      </span>
    );
  }
  return (
    <Link href={href}
          className={cx(EVIDENCE, "font-mono bg-hairline text-text-secondary hover:bg-hairline-strong hover:text-text-primary")}
          aria-label={`Открыть основание, ${clock}${context ? `: ${context}` : ""}`}>
      <PlayGlyph />
      {clock}
    </Link>
  );
}

/**
 * «Повторить» у части или саммари, которое упало.
 *
 * Ошибки бывают временные: Deepgram лёг, сеть моргнула. До этой кнопки
 * единственным способом вернуть часть в очередь был поход на сервер руками.
 * Нажатие сразу гасит кнопку и пишет результат словами: молчащая кнопка
 * оставляет владельца гадать, сработало или нет.
 */
export function Retry({ path, onDone }: { path: string; onDone?: () => void }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "failed">("idle");

  if (state === "done") {
    return <span className="shrink-0 text-note text-text-secondary">в очереди</span>;
  }

  return (
    <button
      type="button"
      disabled={state === "busy"}
      title={state === "failed" ? "не получилось, попробуйте ещё раз" : undefined}
      onClick={async () => {
        setState("busy");
        try {
          await apiPost(path, {});
          setState("done");
          onDone?.();
        } catch {
          setState("failed");
        }
      }}
      className={cx(
        "press hit relative shrink-0 rounded-chip border px-3 py-1 text-note",
        "disabled:text-text-disabled",
        state === "failed"
          ? "border-accent-text text-accent-text"
          : "border-control text-text-secondary hover:border-white/30 hover:text-text-primary",
      )}
    >
      {state === "busy" ? "…" : state === "failed" ? "Не вышло — ещё раз" : "Повторить"}
    </button>
  );
}
