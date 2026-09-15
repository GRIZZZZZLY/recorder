"use client";

import { useEffect, useRef, useState } from "react";
import { resample } from "@/lib/resample";
import { cx } from "@/utils/cx";

/**
 * Волновая форма записи — настоящая громкость, а не украшение.
 *
 * Сервер считает огибающую из самого файла при приёме части (server/envelope.py):
 * одно число на полсекунды, 0..100 внутри своей части. Здесь она только
 * рисуется. Раньше форма выводилась из номера сессии и выглядела как данные,
 * данными не являясь, — этого больше нет.
 *
 * Столбик всегда одной ширины, как у голосового сообщения: число столбиков
 * даёт ширина дорожки, а данные пересчитываются под него (`lib/resample.ts`).
 * Раньше столбики тянулись на всю ширину, и запись в полминуты — 17 чисел —
 * превращалась в забор из широких прямоугольников.
 */
export function Waveform({
  values, height = 24, played = 0, tone = "bg-waveform", bar = 3, gap = 2, className, style,
}: {
  /** Огибающая 0..100. Пустая — запись есть, а громкость не посчитана. */
  values: number[];
  height?: number;
  /** Доля проигранного, 0…1: эти полосы светлые, остальные приглушённые. */
  played?: number;
  /** Цвет непроигранной части: состояние записи на ленте дня. */
  tone?: string;
  /** Ширина столбика и зазор, px. Лента дня берёт 2 и 1: там масштаб 80 px на час. */
  bar?: number;
  gap?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const count = Math.max(0, Math.floor((width + gap) / (bar + gap)));
  // Ровная линия вместо волны: у части старого архива огибающей нет, и рисовать
  // вместо неё выдуманную форму нельзя.
  const bars = count ? resample(values.length ? values : [2], count) : [];
  const edge = Math.round(bars.length * Math.min(Math.max(played, 0), 1));

  // До первого замера ширины столбиков нет, высота места уже занята.
  return (
    <div ref={box} aria-hidden
         className={cx("flex items-center justify-between overflow-hidden", className)}
         style={{ height, ...style }}>
      {bars.map((value, index) => (
        <span
          key={index}
          className={cx("lit-bar shrink-0 rounded-full", index < edge ? "bg-fill" : tone)}
          style={{ width: bar, height: Math.max(bar, Math.round((value / 100) * height)) }}
        />
      ))}
    </div>
  );
}
