import type { SessionRow } from "@/lib/api";
import { formatDayShort } from "@/lib/format";
import { minutesSince } from "@/lib/use-now";

/** Точка статуса записи: саммари готово, ещё считается или упало. Упавшая
 * часть — тоже ошибка: саммари соберётся и без неё, но кусок звука так и не
 * стал текстом, и «готово» было бы неправдой. */
export function statusTone(row: Pick<SessionRow, "summary_status" | "failed_parts">): "ok" | "warn" | "accent" {
  if (row.failed_parts > 0) return "accent";
  if (row.summary_status === "done") return "ok";
  if (row.summary_status === "failed") return "accent";
  return "warn";
}

/** Слово статуса для записи: больше часа без новых частей — «ждёт с …»,
 * а не «обрабатывается». Одна функция для карточки и ленты дня. */
export function statusWord(row: Pick<SessionRow, "summary_status" | "failed_parts" | "last_at" | "day">, now: Date): string {
  const tone = statusTone(row);
  if (tone === "warn" && row.day && minutesSince(row.last_at, now) > 60) return `ждёт с ${formatDayShort(row.day)}`;
  return STATUS_TEXT[tone];
}

export const STATUS_TEXT = {
  ok: "готово",
  warn: "обрабатывается",
  accent: "ошибка",
} as const;
