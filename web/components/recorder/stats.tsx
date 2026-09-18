import { Dot } from "@/components/recorder/ui";
import { cx } from "@/utils/cx";

/**
 * Числа за период на странице «Состояние».
 *
 * Числа стоят без карточек: их разделяют волосяные линии, а не рамки. Так
 * страница читается как приборная панель, а не как набор плиток. На сводке
 * счётчики дня встроены в сам прибор (app/page.tsx).
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
            {item.dot ? <Dot tone={item.dot} size={6} /> : null}
            {item.label}
          </span>
        </div>
      ))}
    </div>
  );
}
