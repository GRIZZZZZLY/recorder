import type { SessionRow } from "@/lib/api";

/** Точка статуса записи: саммари готово, ещё считается или упало. */
export function statusTone(row: SessionRow): "ok" | "warn" | "accent" {
  if (row.summary_status === "done") return "ok";
  if (row.summary_status === "failed") return "accent";
  return "warn";
}

export const STATUS_TEXT = {
  ok: "готово",
  warn: "обрабатывается",
  accent: "ошибка",
} as const;
