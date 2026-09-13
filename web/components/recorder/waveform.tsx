import { cx } from "@/utils/cx";

/**
 * Волновая форма записи — настоящая громкость, а не украшение.
 *
 * Сервер считает огибающую из самого файла при приёме части (server/envelope.py):
 * одно число на две секунды, 0..100 внутри своей сессии. Здесь она только
 * рисуется. Раньше форма выводилась из номера сессии и выглядела как данные,
 * данными не являясь, — этого больше нет.
 */

/** Сжать до нужного числа столбиков, беря пик в окне: та же логика, что на сервере. */
export function thin(values: number[], bars: number): number[] {
  if (!values.length || bars <= 0) return [];
  if (values.length <= bars) return values;
  const step = values.length / bars;
  return Array.from({ length: bars }, (_, i) =>
    Math.max(...values.slice(Math.floor(i * step),
                             Math.max(Math.floor((i + 1) * step), Math.floor(i * step) + 1))));
}

export function Waveform({ values, height = 24, played = 0, tone = "bg-waveform", className, style }: {
  /** Огибающая 0..100. Пустая — запись есть, а громкость не посчитана. */
  values: number[];
  height?: number;
  /** Доля проигранного, 0…1: эти полосы светлые, остальные приглушённые. */
  played?: number;
  /** Цвет непроигранной части: состояние записи на ленте дня. */
  tone?: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  // Ровная линия вместо волны: у части старого архива огибающей нет, и рисовать
  // вместо неё выдуманную форму нельзя.
  const bars = values.length ? values : new Array(24).fill(2);
  const edge = Math.round(bars.length * Math.min(Math.max(played, 0), 1));
  // Столбики тянутся на всю ширину: волна одинаково заполняет и строку списка,
  // и плеер, и десятипиксельный блок на ленте дня. Число столбиков выбирает
  // вызывающий — по ширине своего места.
  return (
    <div aria-hidden className={cx("flex items-center gap-px overflow-hidden", className)}
         style={{ height, ...style }}>
      {bars.map((value, index) => (
        <span
          key={index}
          className={cx("min-w-px flex-1 rounded-[1px]", index < edge ? "bg-fill" : tone)}
          style={{ height: Math.max(2, Math.round((value / 100) * height)) }}
        />
      ))}
    </div>
  );
}
