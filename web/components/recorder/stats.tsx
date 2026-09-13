import { Dot } from "@/components/recorder/ui";
import { at } from "@/lib/motion";
import { cx } from "@/utils/cx";

/**
 * Числа дня и полоса состояния конвейера — верх «командного центра».
 *
 * Числа стоят без карточек: их разделяют волосяные линии, а не рамки. Так
 * сводка читается как приборная панель, а не как набор плиток.
 */
export function Stats({ items }: {
  items: { value: string | number; label: string; tone?: "accent"; dot?: "ok" | "warn" | "accent" }[];
}) {
  return (
    <div className={cx("grid grid-cols-2 gap-y-4 py-2", items.length === 3 ? "lg:grid-cols-3" : "lg:grid-cols-4")}>
      {items.map((item, index) => (
        <div
          key={item.label}
          className={cx(
            "flex flex-col gap-1.5 px-4 first:pl-0",
            // Линия слева у всех, кроме первой в строке: на телефоне строк две.
            index % 2 === 0 ? "border-l-0 pl-0 lg:border-l lg:border-hairline lg:pl-6" : "border-l border-hairline pl-4 lg:pl-6",
            index === 0 && "lg:border-l-0 lg:pl-0",
          )}
        >
          <span className={cx("text-stat-sm lg:text-stat", item.tone === "accent" ? "text-accent" : "text-text-primary")}>
            {item.value}
          </span>
          <span className="flex items-center gap-2 text-note text-text-secondary lg:text-row">
            {item.dot ? <Dot tone={item.dot} size={6} flash /> : null}
            {item.label}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Плашка полосы состояния: точка, что именно, короткое значение. */
export function StatusPill({ tone, live, title, value, at: tick }: {
  tone: "ok" | "warn" | "accent" | "missing";
  live?: boolean;
  title: string;
  value: string;
  /** Такт хореографии страницы: плашка входит, затем точка загорается. */
  at?: number;
}) {
  return (
    <div className={cx("flex items-center gap-2.5 rounded-inner bg-surface px-3.5 py-2.5 text-row",
                       tick !== undefined && "enter")}
         style={tick !== undefined ? at(tick) : undefined}>
      <Dot tone={tone} live={live} flash={tick !== undefined} />
      <span>{title}</span>
      <span className="text-text-tertiary" aria-hidden>·</span>
      <span className="text-text-secondary">{value}</span>
    </div>
  );
}
