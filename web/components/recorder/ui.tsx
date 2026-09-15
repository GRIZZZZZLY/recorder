"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { Play } from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react";
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
        "press inline-flex items-center gap-2 rounded-inner px-4 py-2 text-note",
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
          "rounded-inner border border-control bg-surface px-3 py-2.5 text-row text-text-primary",
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
  const className =
    "rounded-chip border border-hairline-strong px-3 py-1.5 text-note text-text-secondary";
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
              "press relative rounded-md px-3.5 py-1.5",
              selected ? "text-text-primary" : "text-text-secondary hover:text-text-primary",
            )}
          >
            {option.title}
            {option.count !== undefined && (
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
export function Dot({ tone, live, flash, size = 8, title }: {
  tone: keyof typeof DOT_TONES;
  live?: boolean;
  /** Загореться при появлении — для плашек состояния на сводке. */
  flash?: boolean;
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
        live && "live-dot",
        flash && tone !== "missing" && tone !== "muted" && "dot-flash",
        // Красная точка без пульса — ошибка: светится ровно, её надо видеть.
        tone === "accent" && !live && "shadow-glow-accent-soft",
      )}
    />
  );
}

/** Точка источника по данным саммари: зелёная, жёлтая или пустой контур. */
export function SourceDot({ source, flash }: { source: { confirmed: boolean } | null; flash?: boolean }) {
  if (!source) return <Dot tone="missing" title="источник не найден" />;
  if (source.confirmed) return <Dot tone="ok" title="цитата найдена в записи" flash={flash} />;
  return <Dot tone="warn" title="источник не подтверждён" flash={flash} />;
}

const EVIDENCE =
  "press group inline-flex shrink-0 items-center gap-1.5 rounded-inner px-2.5 py-1.5 text-note";

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
 * Кнопка «основание»: перематывает плеер на реплику, из которой саммари взяло
 * пункт. Пока играет — инвертируется. Без источника выключена.
 */
export function Evidence({ playing, className, ...props }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { playing?: boolean }) {
  return (
    <button
      type="button"
      {...props}
      className={cx(
        EVIDENCE,
        // Пока играет — светлая заливка и свечение: звук идёт.
        playing ? "bg-fill text-canvas shadow-glow-fill" : "bg-hairline hover:bg-hairline-strong",
        "disabled:bg-transparent disabled:text-text-disabled",
        className,
      )}
    >
      <PlayGlyph filled={playing} />
      {playing ? "играет" : "основание"}
    </button>
  );
}

/**
 * То же «основание», но на странице без плеера: ведёт на реплику внутри
 * сессии. Без источника — не ссылка, а погашенная подпись: вести некуда.
 */
export function EvidenceLink({ href }: { href: string | null }) {
  if (!href) {
    return (
      <span className={cx(EVIDENCE, "text-text-tertiary")} title="источник не найден">
        <Play size={12} aria-hidden />
        основание
      </span>
    );
  }
  return (
    <Link href={href} className={cx(EVIDENCE, "bg-hairline hover:bg-hairline-strong")}>
      <PlayGlyph />
      основание
    </Link>
  );
}
