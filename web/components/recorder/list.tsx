import Link from "next/link";
import { normalizeClock } from "@/lib/format";
import { cx } from "@/utils/cx";

/**
 * Grouped list — основная форма макета «Студия»: секция с подписью, под ней
 * карточка на surface, внутри строки, разделённые волосяной линией. Рамок и
 * теней у карточки нет: её границу рисует разница тона с канвасом.
 */

/**
 * Секция с заголовком. `head` крупный (17/600), `note` мелкий (13).
 */
export function Section({ id, head, note, aside, className, children }: {
  /** Якорь для оглавления страницы. */
  id?: string;
  head?: string;
  note?: string;
  aside?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className={cx("recorder-section flex scroll-mt-6 flex-col", head ? "gap-2.5" : "gap-2", className)}>
      {(head || note || aside) && (
        <div className="flex items-baseline justify-between gap-4 px-1">
          {head ? <h2 className="text-section text-text-primary">{head}</h2> : null}
          {/* Подпись секции — её заголовок: h2 для читалки и структуры, и на
              глаз не слабее пунктов под ней, иначе страница — ровная серая лента. */}
          {note && !head ? <h2 className="text-[15px] font-medium text-text-primary">{note}</h2> : null}
          {aside ? <span className="text-note text-text-secondary">{aside}</span> : null}
        </div>
      )}
      {note && head ? <span className="px-1 text-note text-text-secondary">{note}</span> : null}
      {children}
    </section>
  );
}

/** Карточка: контейнер строк. */
export function Card({ className, children }: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cx("recorder-card overflow-hidden rounded-card bg-surface text-row shadow-plate", className)}>
      {children}
    </div>
  );
}

// Разделитель между строками рисует сама строка сверху: так первая строка
// остаётся без линии независимо от того, чем её отрисовали.
const ROW = "recorder-row flex items-center gap-3 px-4 py-3.5 not-first:border-t not-first:border-hairline";

export function Row({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cx(ROW, className)}>{children}</div>;
}

export function RowLink({ href, className, children }: {
  href: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Link href={href} className={cx(ROW, "press-row hover:bg-surface-elevated", className)}>
      {children}
    </Link>
  );
}

export function RowButton({ className, children, ...props }:
  React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" {...props}
            className={cx(ROW, "press-row w-full text-left hover:bg-surface-elevated", className)}>
      {children}
    </button>
  );
}

/** Номер сессии моноширинным: 0042. */
export function SessionNo({ session, className }: { session: number; className?: string }) {
  return (
    <span className={cx("font-mono text-note text-text-tertiary", className)}>
      {String(session).padStart(4, "0")}
    </span>
  );
}

/** Время внутри записи моноширинным: 00:12:34. */
export function Time({ children, className }: { children: React.ReactNode; className?: string }) {
  // Сервер пишет «0:00:27», интерфейс — «0:27»: один формат на экране.
  const text = typeof children === "string" ? normalizeClock(children) : children;
  return <span className={cx("font-mono text-note text-text-tertiary", className)}>{text}</span>;
}
